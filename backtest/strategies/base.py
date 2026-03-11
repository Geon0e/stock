"""
전략 기본 클래스
"""

from abc import ABC, abstractmethod
import pandas as pd


class BaseStrategy(ABC):
    """
    모든 전략이 상속받는 기본 클래스

    커스텀 전략 작성 예시:

        class MyStrategy(BaseStrategy):
            def initialize(self, engine):
                self.ticker = '005930'

            def on_bar(self, engine, date, data, prices):
                pos = engine.get_position(self.ticker)
                if pos.quantity == 0:
                    engine.buy_pct(date, self.ticker, 1.0)  # 전액 매수
    """

    @abstractmethod
    def initialize(self, engine) -> None:
        """전략 초기화 (백테스트 시작 전 1회 실행)"""
        pass

    @abstractmethod
    def on_bar(self, engine, date: pd.Timestamp, data: dict, prices: dict) -> None:
        """
        매 거래일마다 실행

        Args:
            engine: BacktestEngine 인스턴스
            date: 현재 날짜
            data: {ticker: OHLCV Series} 당일 데이터
            prices: {ticker: 종가} 딕셔너리
        """
        pass

    def name(self) -> str:
        return self.__class__.__name__
