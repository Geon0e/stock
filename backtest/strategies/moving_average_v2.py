"""
MA 크로스 V2 전략 — 추세 필터 + RSI 진입 조건

V1과의 차이:
  V1: MA5 > MA20 이면 무조건 매수
  V2: MA5 > MA20  AND  종가 > MA60(추세 필터)  AND  RSI < 65(과매수 제외)
      매도: MA5 < MA20  OR  종가 < MA60 (추세 이탈 시 즉시 청산)

기대 효과:
  - 횡보장 휩소(whipsaw) 대폭 감소
  - 과매수 구간 진입 방지 → MDD 감소
  - 추세 이탈 즉시 청산 → 손실 제한
  - 거래 횟수 감소 → 비용 절감
"""

import pandas as pd
from .base import BaseStrategy


class MovingAverageCrossV2Strategy(BaseStrategy):
    """
    개선된 MA 크로스 전략 (V2)

    진입 조건 (세 가지 모두 충족):
        1. MA5 > MA20  (단기 골든크로스)
        2. 종가 > MA60 (중기 상승 추세 확인)
        3. RSI(14) < 65 (과매수 구간 진입 방지)

    청산 조건 (하나라도 충족):
        1. MA5 < MA20  (단기 데드크로스)
        2. 종가 < MA60 (중기 추세 이탈)
    """

    def __init__(
        self,
        ticker: str,
        short_window: int = 5,
        long_window: int = 20,
        trend_window: int = 60,
        rsi_period: int = 14,
        rsi_entry_max: float = 65.0,
        invest_pct: float = 1.0,
    ):
        self.ticker       = ticker
        self.short_window = short_window
        self.long_window  = long_window
        self.trend_window = trend_window
        self.rsi_period   = rsi_period
        self.rsi_entry_max = rsi_entry_max
        self.invest_pct   = invest_pct
        self.price_history = []
        self.in_position   = False

    def initialize(self, engine) -> None:
        print(f"[{self.name()}] {self.ticker}")

    def _rsi(self) -> float:
        """Wilder 평활 RSI"""
        period = self.rsi_period
        prices = self.price_history
        if len(prices) < period + 1:
            return 50.0
        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        gains  = [max(d, 0.0) for d in deltas]
        losses = [max(-d, 0.0) for d in deltas]
        # 초기 평균
        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period
        # Wilder 평활 (이후 값)
        for g, l in zip(gains[period:], losses[period:]):
            avg_g = (avg_g * (period - 1) + g) / period
            avg_l = (avg_l * (period - 1) + l) / period
        if avg_l == 0:
            return 100.0
        return 100.0 - (100.0 / (1.0 + avg_g / avg_l))

    def on_bar(self, engine, date: pd.Timestamp, data: dict, prices: dict) -> None:
        if self.ticker not in prices:
            return

        self.price_history.append(prices[self.ticker])

        if len(self.price_history) < self.trend_window:
            return

        short_ma = sum(self.price_history[-self.short_window:]) / self.short_window
        long_ma  = sum(self.price_history[-self.long_window:])  / self.long_window
        trend_ma = sum(self.price_history[-self.trend_window:]) / self.trend_window
        rsi      = self._rsi()
        price    = prices[self.ticker]

        # ── 진입 ──────────────────────────────────────────────────────────
        entry_ok = (
            short_ma > long_ma       # 골든크로스
            and price > trend_ma     # 중기 상승 추세
            and rsi < self.rsi_entry_max  # 과매수 아님
        )
        # ── 청산 ──────────────────────────────────────────────────────────
        exit_ok = (short_ma < long_ma) or (price < trend_ma)

        if entry_ok and not self.in_position:
            if engine.buy_pct(date, self.ticker, self.invest_pct):
                self.in_position = True
                pos = engine.get_position(self.ticker)
                print(f"  [매수] {date.date()} {self.ticker} "
                      f"{pos.quantity}주 @ {price:,.0f}  RSI={rsi:.1f}")

        elif exit_ok and self.in_position:
            if engine.sell(date, self.ticker):
                self.in_position = False
                reason = "데드크로스" if short_ma < long_ma else "추세이탈(MA60)"
                print(f"  [매도/{reason}] {date.date()} {self.ticker} @ {price:,.0f}")

    def name(self) -> str:
        return (f"MA크로스V2 "
                f"({self.short_window}/{self.long_window}/추세{self.trend_window} "
                f"RSI<{self.rsi_entry_max:.0f})")
