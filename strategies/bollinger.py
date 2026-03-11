"""
볼린저 밴드 전략
"""

import pandas as pd
import numpy as np
from .base import BaseStrategy


class BollingerBandStrategy(BaseStrategy):
    """
    볼린저 밴드 전략

    - 가격이 하단 밴드 아래 → 매수 (반등 기대)
    - 가격이 상단 밴드 위  → 매도 (과매수 조정)
    """

    def __init__(
        self,
        ticker: str,
        window: int = 20,
        num_std: float = 2.0,
        invest_pct: float = 1.0,
    ):
        """
        Args:
            ticker: 종목코드
            window: 이동평균 기간
            num_std: 표준편차 배수
            invest_pct: 투자 비율
        """
        self.ticker = ticker
        self.window = window
        self.num_std = num_std
        self.invest_pct = invest_pct
        self.price_history = []
        self.in_position = False

    def initialize(self, engine) -> None:
        print(f"[{self.name()}] {self.ticker} / 기간:{self.window} 표준편차:{self.num_std}σ")

    def on_bar(self, engine, date: pd.Timestamp, data: dict, prices: dict) -> None:
        if self.ticker not in prices:
            return

        price = prices[self.ticker]
        self.price_history.append(price)

        if len(self.price_history) < self.window:
            return

        window_prices = self.price_history[-self.window:]
        ma = np.mean(window_prices)
        std = np.std(window_prices, ddof=1)

        upper = ma + self.num_std * std
        lower = ma - self.num_std * std

        if price < lower and not self.in_position:
            success = engine.buy_pct(date, self.ticker, self.invest_pct)
            if success:
                self.in_position = True
                pos = engine.get_position(self.ticker)
                pct_b = (price - lower) / (upper - lower) * 100 if upper != lower else 50
                print(f"  [매수] {date.date()} {self.ticker} %B={pct_b:.1f} {pos.quantity}주 @ {price:,.0f}원")

        elif price > upper and self.in_position:
            success = engine.sell(date, self.ticker)
            if success:
                self.in_position = False
                pct_b = (price - lower) / (upper - lower) * 100 if upper != lower else 50
                print(f"  [매도] {date.date()} {self.ticker} %B={pct_b:.1f} @ {price:,.0f}원")

    def name(self) -> str:
        return f"볼린저밴드({self.window},{self.num_std}σ)"
