"""
모멘텀 전략 V2 (듀얼 모멘텀 + 손절 강화)

승율 개선 포인트:
    1. 단일 모멘텀 → 듀얼 모멘텀 (장기 + 단기 모두 양수여야 진입)
       - 장기 모멘텀: 시장 방향성 확인
       - 단기 모멘텀: 최근 가속도 확인 (약세 종목 진입 방지)
    2. 리밸런싱 사이 손절: ATR 트레일링 스톱 (중간 급락 보호)
    3. 추세 필터: 가격 > MA200 (하락장 필터)
    4. 포지션 동기화 (외부 청산 반영)
    5. 변동성 사이징 옵션 (ATR 기반 포지션 조절)

진입 조건 (리밸런싱 시점, 모두 충족):
    1. 장기 모멘텀 (lookback일) > min_momentum% (장기 방향성 양수)
    2. 단기 모멘텀 (short_lookback일) > 0% (최근 가속 양수)
    3. 가격 > MA(trend_window) (추세 확인)

청산 조건 (하나라도 충족):
    1. 리밸런싱 시점: 모멘텀 기준 미충족
    2. ATR 트레일링 스톱 (리밸런싱 사이 손절)
    3. 추세 이탈 (가격 < MA trend_window) - 즉시 청산
"""

import pandas as pd
from typing import Optional
from .base import BaseStrategy


class MomentumStrategy(BaseStrategy):
    """
    듀얼 모멘텀 전략 V2 (승율 개선)

    - 장기 + 단기 모멘텀 동시 확인
    - ATR 트레일링 스톱 (리밸런싱 사이 손절)
    - 추세 필터 (MA200)
    """

    def __init__(
        self,
        ticker:          str,
        lookback:        int   = 120,    # 장기 모멘텀 기간
        short_lookback:  int   = 20,     # 단기 모멘텀 기간 (0=비활성화)
        min_momentum:    float = 0.0,    # 장기 모멘텀 최소 기준 (%)
        invest_pct:      float = 1.0,
        rebalance_freq:  int   = 20,     # 리밸런싱 주기 (거래일)
        trend_window:    int   = 200,    # 추세 필터 MA 기간 (0=비활성화)
        trail_mult:      float = 3.0,    # ATR 트레일링 스톱 배수 (0=비활성화)
        market_df:       Optional[pd.DataFrame] = None,
        regime_window:   int   = 200,
        use_vol_sizing:  bool  = False,
        risk_pct:        float = 0.02,
    ):
        self.ticker          = ticker
        self.lookback        = lookback
        self.short_lookback  = short_lookback
        self.min_momentum    = min_momentum
        self.invest_pct      = invest_pct
        self.rebalance_freq  = rebalance_freq
        self.trend_window    = trend_window
        self.trail_mult      = trail_mult
        self.market_df       = market_df
        self.regime_window   = regime_window
        self.use_vol_sizing  = use_vol_sizing
        self.risk_pct        = risk_pct

        self.price_history:  list = []
        self.high_history:   list = []
        self.low_history:    list = []

        self.in_position:         bool  = False
        self.stop_armed:          bool  = False
        self.highest_since_entry: float = 0.0
        self.bar_count:           int   = 0

        # 시장 레짐 MA 사전 계산
        self._market_ma: Optional[pd.Series] = None
        if market_df is not None and not market_df.empty:
            col = "Close" if "Close" in market_df.columns else market_df.columns[0]
            self._market_ma = market_df[col].rolling(regime_window, min_periods=regime_window).mean()

    def initialize(self, engine) -> None:
        pass

    # ── 보조 지표 ────────────────────────────────────────────────────────

    def _atr(self, period: int = 14) -> float:
        """Wilder 평활 ATR"""
        need = period + 1
        if len(self.high_history) < need:
            return 0.0
        highs  = self.high_history[-need:]
        lows   = self.low_history[-need:]
        closes = self.price_history[-need:]
        trs = []
        for i in range(1, len(highs)):
            c_prev = closes[i - 1]
            tr = max(highs[i] - lows[i],
                     abs(highs[i] - c_prev),
                     abs(lows[i] - c_prev))
            trs.append(tr)
        if not trs:
            return 0.0
        atr = sum(trs[:period]) / period
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        return atr

    def _regime_ok(self, date: pd.Timestamp) -> bool:
        if self._market_ma is None:
            return True
        try:
            ma_series = self._market_ma.loc[:date].dropna()
            if ma_series.empty:
                return True
            ma_val    = float(ma_series.iloc[-1])
            col       = "Close" if "Close" in self.market_df.columns else self.market_df.columns[0]
            close_val = float(self.market_df[col].loc[:date].iloc[-1])
            return close_val > ma_val
        except Exception:
            return True

    # ── 매 봉 처리 ───────────────────────────────────────────────────────

    def on_bar(self, engine, date: pd.Timestamp, data: dict, prices: dict) -> None:
        if self.ticker not in prices:
            return

        price = prices[self.ticker]
        row   = data.get(self.ticker)

        self.price_history.append(price)
        self.bar_count += 1
        if row is not None:
            try:
                self.high_history.append(float(row["High"]) if "High" in row.index else price)
                self.low_history.append( float(row["Low"])  if "Low"  in row.index else price)
            except Exception:
                self.high_history.append(price)
                self.low_history.append(price)
        else:
            self.high_history.append(price)
            self.low_history.append(price)

        # ── 포지션 동기화 ─────────────────────────────────────────────
        actual_qty    = engine.get_position(self.ticker).quantity
        pending_buy   = any(p.ticker == self.ticker and p.action == "BUY"
                            for p in engine.pending_orders)
        actual_in_pos = actual_qty > 0 or pending_buy
        if self.in_position and not actual_in_pos:
            self.in_position         = False
            self.stop_armed          = False
            self.highest_since_entry = 0.0

        if len(self.price_history) <= self.lookback:
            return

        atr    = self._atr(14)
        regime = self._regime_ok(date)

        # ── 추세 필터 (매 봉 즉시 청산용) ──────────────────────────
        trend_ok = True
        if self.trend_window > 0 and len(self.price_history) >= self.trend_window:
            trend_ma = sum(self.price_history[-self.trend_window:]) / self.trend_window
            trend_ok = price > trend_ma

        # ── 트레일링 스톱 업데이트 (매 봉, 리밸런싱 무관) ────────
        if self.in_position:
            pos = engine.get_position(self.ticker)
            if pos.quantity > 0:
                if price > self.highest_since_entry:
                    self.highest_since_entry = price
                if self.trail_mult > 0 and atr > 0:
                    new_stop     = self.highest_since_entry - self.trail_mult * atr
                    current_stop = engine.get_stop(self.ticker)
                    if current_stop is None or new_stop > current_stop:
                        engine.register_stop(self.ticker, new_stop)
                        self.stop_armed = True

        # ── 추세 이탈 즉시 청산 (리밸런싱 무관) ──────────────────
        if self.in_position and (not trend_ok or not regime):
            engine.sell(date, self.ticker)
            self.in_position         = False
            self.stop_armed          = False
            self.highest_since_entry = 0.0
            engine.clear_stop(self.ticker)
            return

        # ── 리밸런싱 시점에만 모멘텀 신호 체크 ──────────────────
        if self.bar_count % self.rebalance_freq != 0:
            return

        # 장기 모멘텀
        long_momentum = (self.price_history[-1] / self.price_history[-self.lookback] - 1) * 100

        # 단기 모멘텀
        short_momentum_ok = True
        if self.short_lookback > 0 and len(self.price_history) > self.short_lookback:
            short_momentum = (self.price_history[-1] / self.price_history[-self.short_lookback] - 1) * 100
            short_momentum_ok = short_momentum > 0

        entry_ok = (
            regime
            and trend_ok
            and long_momentum > self.min_momentum
            and short_momentum_ok
        )
        exit_ok = not entry_ok   # 기준 미충족 시 청산

        if entry_ok and not self.in_position:
            if self.use_vol_sizing and atr > 0 and self.trail_mult > 0:
                stop_distance_pct = (self.trail_mult * atr) / price
                inv_pct = min(self.risk_pct / stop_distance_pct, self.invest_pct)
                inv_pct = max(inv_pct, 0.05)
            else:
                inv_pct = self.invest_pct
            engine.buy_pct(date, self.ticker, inv_pct)
            self.in_position         = True
            self.stop_armed          = False
            self.highest_since_entry = price

        elif exit_ok and self.in_position:
            engine.sell(date, self.ticker)
            self.in_position         = False
            self.stop_armed          = False
            self.highest_since_entry = 0.0
            engine.clear_stop(self.ticker)

    def name(self) -> str:
        parts = [f"듀얼모멘텀V2({self.lookback}일"]
        if self.short_lookback > 0:
            parts[0] += f"/{self.short_lookback}일"
        parts[0] += ")"
        if self.trend_window > 0:
            parts.append(f"추세MA{self.trend_window}")
        if self.trail_mult > 0:
            parts.append(f"트레일ATR{self.trail_mult}x")
        if self._market_ma is not None:
            parts.append("레짐필터ON")
        return " ".join(parts)
