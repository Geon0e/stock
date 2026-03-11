"""
이동평균 골든크로스 / 데드크로스 전략
"""

import pandas as pd
from .base import BaseStrategy


class MovingAverageCrossStrategy(BaseStrategy):
    """
    이동평균 크로스 전략

    - 단기 이동평균 > 장기 이동평균 → 매수 (골든크로스)
    - 단기 이동평균 < 장기 이동평균 → 매도 (데드크로스)
    """

    def __init__(self, ticker: str, short_window: int = 20, long_window: int = 60, invest_pct: float = 1.0):
        """
        Args:
            ticker: 종목코드
            short_window: 단기 이동평균 기간 (일)
            long_window: 장기 이동평균 기간 (일)
            invest_pct: 매수 시 투자 비율 (0~1)
        """
        self.ticker = ticker
        self.short_window = short_window
        self.long_window = long_window
        self.invest_pct = invest_pct
        self.price_history = []
        self.in_position = False

    def initialize(self, engine) -> None:
        print(f"[{self.name()}] {self.ticker} / MA{self.short_window} x MA{self.long_window}")

    def on_bar(self, engine, date: pd.Timestamp, data: dict, prices: dict) -> None:
        if self.ticker not in prices:
            return

        self.price_history.append(prices[self.ticker])

        if len(self.price_history) < self.long_window:
            return

        short_ma = sum(self.price_history[-self.short_window:]) / self.short_window
        long_ma = sum(self.price_history[-self.long_window:]) / self.long_window

        if short_ma > long_ma and not self.in_position:
            success = engine.buy_pct(date, self.ticker, self.invest_pct)
            if success:
                self.in_position = True
                pos = engine.get_position(self.ticker)
                print(f"  [매수] {date.date()} {self.ticker} {pos.quantity}주 @ {prices[self.ticker]:,.0f}원")

        elif short_ma < long_ma and self.in_position:
            success = engine.sell(date, self.ticker)
            if success:
                self.in_position = False
                print(f"  [매도] {date.date()} {self.ticker} @ {prices[self.ticker]:,.0f}원")

    def name(self) -> str:
        return f"MA크로스({self.short_window}/{self.long_window})"
