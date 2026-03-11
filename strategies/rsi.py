"""
RSI (상대강도지수) 전략
"""

import pandas as pd
import numpy as np
from .base import BaseStrategy


class RSIStrategy(BaseStrategy):
    """
    RSI 과매수/과매도 전략

    - RSI < 과매도 기준 → 매수
    - RSI > 과매수 기준 → 매도
    """

    def __init__(
        self,
        ticker: str,
        period: int = 14,
        oversold: float = 30.0,
        overbought: float = 70.0,
        invest_pct: float = 1.0,
    ):
        """
        Args:
            ticker: 종목코드
            period: RSI 계산 기간
            oversold: 과매도 기준값 (이하 시 매수)
            overbought: 과매수 기준값 (이상 시 매도)
            invest_pct: 매수 시 투자 비율
        """
        self.ticker = ticker
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.invest_pct = invest_pct
        self.price_history = []
        self.in_position = False

    def initialize(self, engine) -> None:
        print(f"[{self.name()}] {self.ticker} / RSI{self.period} 과매도:{self.oversold} 과매수:{self.overbought}")

    def on_bar(self, engine, date: pd.Timestamp, data: dict, prices: dict) -> None:
        if self.ticker not in prices:
            return

        self.price_history.append(prices[self.ticker])

        if len(self.price_history) < self.period + 1:
            return

        rsi = self._calc_rsi()

        if rsi < self.oversold and not self.in_position:
            success = engine.buy_pct(date, self.ticker, self.invest_pct)
            if success:
                self.in_position = True
                pos = engine.get_position(self.ticker)
                print(f"  [매수] {date.date()} {self.ticker} RSI={rsi:.1f} {pos.quantity}주 @ {prices[self.ticker]:,.0f}원")

        elif rsi > self.overbought and self.in_position:
            success = engine.sell(date, self.ticker)
            if success:
                self.in_position = False
                print(f"  [매도] {date.date()} {self.ticker} RSI={rsi:.1f} @ {prices[self.ticker]:,.0f}원")

    def _calc_rsi(self) -> float:
        prices = np.array(self.price_history[-(self.period + 1):])
        deltas = np.diff(prices)

        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def name(self) -> str:
        return f"RSI({self.period})"
