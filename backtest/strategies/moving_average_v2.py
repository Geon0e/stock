"""
MA 크로스 V2 전략 (기관급 설계)

진입 조건 (모두 충족):
    1. MA5  > MA20          (단기 골든크로스)
    2. 종가 > MA60          (중기 추세 확인)
    3. RSI(14) < 65         (과매수 제외)
    4. 시장 지수 > MA200    (상승장만 진입, market_df 제공 시)

청산 조건 (하나라도 충족):
    1. MA5 < MA20           (데드크로스)
    2. 종가 < MA60          (추세 이탈)
    3. 시장 지수 < MA200    (레짐 이탈, market_df 제공 시)
    4. ATR 손절 트리거       (Low <= Entry - 2×ATR, 엔진이 자동 처리)

포지션 사이징:
    - invest_pct 기본값 0.5 (가용 현금의 50% → 풀베팅 방지)

손절 등록:
    - 포지션 진입 확인 후 첫 번째 bar에서 avg_price 기준으로 ATR 손절가 등록
    - atr_stop_mult=0 으로 비활성화 가능
"""

import pandas as pd
from typing import Optional
from .base import BaseStrategy


class MovingAverageCrossV2Strategy(BaseStrategy):

    def __init__(
        self,
        ticker:          str,
        short_window:    int   = 5,
        long_window:     int   = 20,
        trend_window:    int   = 60,
        rsi_period:      int   = 14,
        rsi_entry_max:   float = 65.0,
        invest_pct:      float = 0.5,     # 50% 포지션 (풀베팅 방지)
        market_df:       Optional[pd.DataFrame] = None,   # 시장 레짐 필터
        atr_stop_mult:   float = 2.0,     # ATR 손절 배수 (0 = 비활성화)
        regime_window:   int   = 200,     # 레짐 MA 기간
    ):
        self.ticker          = ticker
        self.short_window    = short_window
        self.long_window     = long_window
        self.trend_window    = trend_window
        self.rsi_period      = rsi_period
        self.rsi_entry_max   = rsi_entry_max
        self.invest_pct      = invest_pct
        self.market_df       = market_df
        self.atr_stop_mult   = atr_stop_mult
        self.regime_window   = regime_window

        self.price_history: list = []
        self.high_history:  list = []
        self.low_history:   list = []

        self.in_position:  bool  = False
        self.stop_armed:   bool  = False   # 손절 등록 여부

        # 시장 레짐 MA 사전 계산
        self._market_ma: Optional[pd.Series] = None
        if market_df is not None and not market_df.empty:
            col = "Close" if "Close" in market_df.columns else market_df.columns[0]
            self._market_ma = market_df[col].rolling(regime_window, min_periods=regime_window).mean()

    # ── 초기화 ───────────────────────────────────────────────────────────

    def initialize(self, engine) -> None:
        print(
            f"[{self.name()}] {self.ticker}  "
            f"invest={self.invest_pct:.0%}  "
            f"ATR손절={self.atr_stop_mult}x  "
            f"레짐필터={'ON' if self._market_ma is not None else 'OFF'}"
        )

    # ── 보조 지표 ────────────────────────────────────────────────────────

    def _rsi(self) -> float:
        """Wilder 평활 RSI"""
        period = self.rsi_period
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
        """Wilder 평활 ATR (True Range 기반)"""
        need = period + 1
        if len(self.high_history) < need or len(self.price_history) < need:
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
        """시장 레짐 필터: 시장 지수 > MA200"""
        if self._market_ma is None:
            return True
        try:
            ma_series = self._market_ma.loc[:date].dropna()
            if ma_series.empty:
                return True
            ma_val = float(ma_series.iloc[-1])
            col    = "Close" if "Close" in self.market_df.columns else self.market_df.columns[0]
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

        # OHLC 이력 업데이트
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

        # ── 포지션 상태 동기화 (손절 등 외부 청산 반영) ───────────────
        actual_qty     = engine.get_position(self.ticker).quantity
        pending_buy    = any(p.ticker == self.ticker and p.action == "BUY"
                             for p in engine.pending_orders)
        actual_in_pos  = actual_qty > 0 or pending_buy
        if self.in_position and not actual_in_pos:
            self.in_position = False
            self.stop_armed  = False

        # 데이터 부족
        if len(self.price_history) < self.trend_window:
            return

        # ── 지표 계산 ─────────────────────────────────────────────────
        short_ma = sum(self.price_history[-self.short_window:]) / self.short_window
        long_ma  = sum(self.price_history[-self.long_window:])  / self.long_window
        trend_ma = sum(self.price_history[-self.trend_window:]) / self.trend_window
        rsi      = self._rsi()
        regime   = self._regime_ok(date)

        # ── ATR 손절가 등록 (포지션 진입 첫 bar) ─────────────────────
        if self.in_position and not self.stop_armed and self.atr_stop_mult > 0:
            pos = engine.get_position(self.ticker)
            if pos.quantity > 0 and pos.avg_price > 0:
                atr = self._atr(14)
                if atr > 0:
                    stop = pos.avg_price - self.atr_stop_mult * atr
                    engine.register_stop(self.ticker, stop)
                    self.stop_armed = True
                    print(f"  [손절등록] {date.date()} {self.ticker}  "
                          f"avg={pos.avg_price:,.0f}  stop={stop:,.0f}  ATR={atr:.1f}")

        # ── 진입 조건 ─────────────────────────────────────────────────
        entry_ok = (
            regime
            and short_ma > long_ma      # 골든크로스
            and price    > trend_ma     # MA60 위
            and rsi      < self.rsi_entry_max
        )

        # ── 청산 조건 ─────────────────────────────────────────────────
        exit_ok = (short_ma < long_ma) or (price < trend_ma) or (not regime)

        if entry_ok and not self.in_position:
            engine.buy_pct(date, self.ticker, self.invest_pct)
            self.in_position = True
            self.stop_armed  = False
            print(f"  [매수신호] {date.date()} {self.ticker} Close={price:,.0f}  "
                  f"RSI={rsi:.1f}  레짐={'✓' if regime else '✗'}  → T+1 시가 체결 예정")

        elif exit_ok and self.in_position:
            engine.sell(date, self.ticker)
            self.in_position = False
            self.stop_armed  = False
            engine.clear_stop(self.ticker)
            if not regime:
                reason = "레짐이탈(MA200)"
            elif short_ma < long_ma:
                reason = "데드크로스"
            else:
                reason = "추세이탈(MA60)"
            print(f"  [매도신호/{reason}] {date.date()} {self.ticker} Close={price:,.0f}  → T+1 시가 체결 예정")

    def name(self) -> str:
        parts = [f"MA크로스V2({self.short_window}/{self.long_window}/추세{self.trend_window})",
                 f"RSI<{self.rsi_entry_max:.0f}",
                 f"ATR{self.atr_stop_mult}x손절",
                 f"포지션{self.invest_pct:.0%}"]
        if self._market_ma is not None:
            parts.append("레짐필터ON")
        return " ".join(parts)
