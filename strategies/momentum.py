"""
모멘텀 전략 (듀얼 모멘텀)
"""

import pandas as pd
from .base import BaseStrategy


class MomentumStrategy(BaseStrategy):
    """
    모멘텀 전략

    - N일 수익률이 양수 → 매수 유지
    - N일 수익률이 음수 → 현금 전환 (매도)
    """

    def __init__(
        self,
        ticker: str,
        lookback: int = 120,
        invest_pct: float = 1.0,
        rebalance_freq: int = 20,  # 리밸런싱 주기 (거래일)
    ):
        """
        Args:
            ticker: 종목코드
            lookback: 모멘텀 측정 기간 (거래일)
            invest_pct: 투자 비율
            rebalance_freq: 리밸런싱 주기 (거래일 단위)
        """
        self.ticker = ticker
        self.lookback = lookback
        self.invest_pct = invest_pct
        self.rebalance_freq = rebalance_freq
        self.price_history = []
        self.in_position = False
        self.bar_count = 0

    def initialize(self, engine) -> None:
        print(f"[{self.name()}] {self.ticker} / 모멘텀기간:{self.lookback}일 리밸런싱:{self.rebalance_freq}일")

    def on_bar(self, engine, date: pd.Timestamp, data: dict, prices: dict) -> None:
        if self.ticker not in prices:
            return

        self.price_history.append(prices[self.ticker])
        self.bar_count += 1

        if len(self.price_history) <= self.lookback:
            return

        # 리밸런싱 주기마다만 체크
        if self.bar_count % self.rebalance_freq != 0:
            return

        momentum = (self.price_history[-1] / self.price_history[-self.lookback] - 1) * 100

        if momentum > 0 and not self.in_position:
            success = engine.buy_pct(date, self.ticker, self.invest_pct)
            if success:
                self.in_position = True
                pos = engine.get_position(self.ticker)
                print(f"  [매수] {date.date()} {self.ticker} 모멘텀={momentum:.1f}% {pos.quantity}주 @ {prices[self.ticker]:,.0f}원")

        elif momentum <= 0 and self.in_position:
            success = engine.sell(date, self.ticker)
            if success:
                self.in_position = False
                print(f"  [매도] {date.date()} {self.ticker} 모멘텀={momentum:.1f}% @ {prices[self.ticker]:,.0f}원")

    def name(self) -> str:
        return f"모멘텀({self.lookback}일)"
