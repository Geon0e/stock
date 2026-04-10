"""
RSI (상대강도지수) 전략 V2

승율 개선 포인트:
    1. 단순 평균 RSI → Wilder 평활 RSI (더 정확한 신호)
    2. 레벨 터치 진입 → 크로스오버 확인 진입 (허위 신호 제거)
    3. 추세 필터 추가 (가격 > MA50): 하락 추세에서 물타기 방지
    4. ATR 트레일링 스톱 추가 (손절 없음 문제 해결)
    5. 포지션 동기화 (외부 청산 반영)
    6. RSI 회복 후 재진입 방지 (연속 과매도 체크)

진입 조건 (모두 충족):
    1. RSI가 과매도(oversold) 아래에서 위로 크로스 (확인된 반등)
    2. 가격 > MA50 (단기 상승 추세 컨텍스트에서만 평균회귀)
    3. RSI 가 70 미만 (이미 과매수 아닌지 확인)

청산 조건 (하나라도 충족):
    1. RSI > 과매수 기준 (이익 실현)
    2. 가격 < MA50 (추세 이탈 방어 청산)
    3. ATR 트레일링 스톱 트리거 (손절)

포지션 사이징:
    - use_vol_sizing=True  → 리스크 기반 사이징
    - use_vol_sizing=False → invest_pct 고정 비율 (기본 100%)
"""

import pandas as pd
from typing import Optional
from .base import BaseStrategy


class RSIStrategy(BaseStrategy):
    """
    RSI 과매수/과매도 전략 V2 (승율 개선)

    - RSI 크로스 진입: 과매도 → 과매도 상향 돌파 시 매수 (확인 진입)
    - 추세 필터: 가격 > MA50에서만 매수 (하락추세 물타기 방지)
    - ATR 트레일링 스톱: 손절 자동화
    """

    def __init__(
        self,
        ticker:         str,
        period:         int   = 14,
        oversold:       float = 30.0,
        overbought:     float = 70.0,
        invest_pct:     float = 1.0,
        trend_window:   int   = 50,    # 추세 필터용 MA 기간 (0=비활성화)
        trail_mult:     float = 2.0,   # ATR 트레일링 스톱 배수 (0=비활성화)
        exit_ma_break:  bool  = True,  # MA 이탈 시 청산 여부
        market_df:      Optional[pd.DataFrame] = None,
        regime_window:  int   = 200,
        use_vol_sizing: bool  = False,
        risk_pct:       float = 0.02,
    ):
        self.ticker         = ticker
        self.period         = period
        self.oversold       = oversold
        self.overbought     = overbought
        self.invest_pct     = invest_pct
        self.trend_window   = trend_window
        self.trail_mult     = trail_mult
        self.exit_ma_break  = exit_ma_break
        self.market_df      = market_df
        self.regime_window  = regime_window
        self.use_vol_sizing = use_vol_sizing
        self.risk_pct       = risk_pct

        self.price_history:  list = []
        self.high_history:   list = []
        self.low_history:    list = []

        self.in_position:         bool  = False
        self.stop_armed:          bool  = False
        self.highest_since_entry: float = 0.0
        self.prev_rsi:            float = 50.0   # 직전 봉 RSI (크로스 감지용)

        # 시장 레짐 MA 사전 계산
        self._market_ma: Optional[pd.Series] = None
        if market_df is not None and not market_df.empty:
            col = "Close" if "Close" in market_df.columns else market_df.columns[0]
            self._market_ma = market_df[col].rolling(regime_window, min_periods=regime_window).mean()

    def initialize(self, engine) -> None:
        pass

    # ── 보조 지표 ────────────────────────────────────────────────────────

    def _rsi(self) -> float:
        """Wilder 평활 RSI (단순 평균 대비 더 안정적)"""
        period = self.period
        prices = self.price_history
        if len(prices) < period + 1:
            return 50.0
        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        gains  = [max(d, 0.0) for d in deltas]
        losses = [max(-d, 0.0) for d in deltas]
        avg_g  = sum(gains[:period]) / period
        avg_l  = sum(losses[:period]) / period
        for g, l in zip(gains[period:], losses[period:]):
            avg_g = (avg_g * (period - 1) + g) / period
            avg_l = (avg_l * (period - 1) + l) / period
        if avg_l == 0:
            return 100.0
        return 100.0 - (100.0 / (1.0 + avg_g / avg_l))

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

        # ── 포지션 동기화 (외부 손절 반영) ──────────────────────────────
        actual_qty    = engine.get_position(self.ticker).quantity
        pending_buy   = any(p.ticker == self.ticker and p.action == "BUY"
                            for p in engine.pending_orders)
        actual_in_pos = actual_qty > 0 or pending_buy
        if self.in_position and not actual_in_pos:
            self.in_position         = False
            self.stop_armed          = False
            self.highest_since_entry = 0.0

        # 데이터 부족
        need = max(self.period + 2, self.trend_window if self.trend_window > 0 else 1)
        if len(self.price_history) < need:
            self.prev_rsi = self._rsi()
            return

        rsi    = self._rsi()
        atr    = self._atr(14)
        regime = self._regime_ok(date)

        # ── 추세 필터 ─────────────────────────────────────────────────
        trend_ok = True
        if self.trend_window > 0 and len(self.price_history) >= self.trend_window:
            trend_ma = sum(self.price_history[-self.trend_window:]) / self.trend_window
            trend_ok = price > trend_ma

        # ── 트레일링 스톱 업데이트 ────────────────────────────────────
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

        # ── 진입 조건: RSI 크로스오버 (과매도 구간 이탈 확인) ──────────
        # 직전 RSI < oversold 이고, 현재 RSI >= oversold → 반등 확인
        rsi_crossup = (self.prev_rsi < self.oversold) and (rsi >= self.oversold)
        entry_ok = (
            regime
            and trend_ok
            and rsi_crossup
            and rsi < self.overbought   # 이미 과매수 아님
        )

        # ── 청산 조건 ─────────────────────────────────────────────────
        ma_break = False
        if self.exit_ma_break and self.trend_window > 0 and len(self.price_history) >= self.trend_window:
            trend_ma = sum(self.price_history[-self.trend_window:]) / self.trend_window
            ma_break = price < trend_ma

        exit_ok = (rsi > self.overbought) or ma_break or (not regime)

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

        self.prev_rsi = rsi

    def name(self) -> str:
        parts = [f"RSI크로스V2({self.period},{self.oversold}/{self.overbought})"]
        if self.trend_window > 0:
            parts.append(f"추세MA{self.trend_window}")
        if self.trail_mult > 0:
            parts.append(f"트레일ATR{self.trail_mult}x")
        if self._market_ma is not None:
            parts.append("레짐필터ON")
        return " ".join(parts)
