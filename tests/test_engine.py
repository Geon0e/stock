# -*- coding: utf-8 -*-
"""Backtest engine unit tests"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
try:
    import pytest
except ImportError:
    pytest = None
from backtest import BacktestEngine, Portfolio
from backtest.strategies.base import BaseStrategy


def make_sample_data(n=100, start_price=50000, ticker="TEST"):
    """테스트용 더미 OHLCV 데이터 생성"""
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    np.random.seed(42)
    returns = np.random.randn(n) * 0.01
    close = start_price * np.cumprod(1 + returns)
    df = pd.DataFrame({
        "Open": close * 0.99,
        "High": close * 1.01,
        "Low": close * 0.98,
        "Close": close,
        "Volume": np.random.randint(100000, 1000000, n),
    }, index=dates)
    return df


class AlwaysBuyStrategy(BaseStrategy):
    """테스트용: 첫날 매수 후 보유"""
    def initialize(self, engine):
        self.bought = False

    def on_bar(self, engine, date, data, prices):
        if not self.bought and "TEST" in prices:
            engine.buy_pct(date, "TEST", 1.0)
            self.bought = True


class BuyAndSellStrategy(BaseStrategy):
    """테스트용: 10일마다 매수/매도 반복"""
    def initialize(self, engine):
        self.count = 0
        self.in_position = False

    def on_bar(self, engine, date, data, prices):
        self.count += 1
        if self.count % 10 == 0:
            if not self.in_position:
                engine.buy_pct(date, "TEST", 0.5)
                self.in_position = True
            else:
                engine.sell(date, "TEST")
                self.in_position = False


def test_portfolio_initial_state():
    portfolio = Portfolio(10_000_000)
    assert portfolio.cash == 10_000_000
    assert portfolio.initial_capital == 10_000_000
    assert len(portfolio.positions) == 0


def test_engine_run():
    data = {"TEST": make_sample_data()}
    engine = BacktestEngine(data=data, initial_capital=10_000_000)
    engine.run(AlwaysBuyStrategy())
    assert engine.results is not None
    assert len(engine.results) > 0


def test_buy_reduces_cash():
    data = {"TEST": make_sample_data()}
    engine = BacktestEngine(data=data, initial_capital=10_000_000)
    engine.run(AlwaysBuyStrategy())
    # 매수 후 현금이 줄어야 함
    assert engine.portfolio.cash < 10_000_000


def test_sell_increases_cash():
    data = {"TEST": make_sample_data()}
    engine = BacktestEngine(data=data, initial_capital=10_000_000)
    engine.run(BuyAndSellStrategy())
    orders = engine.get_orders()
    # 매도 주문이 있어야 함
    assert "SELL" in orders["action"].values


def test_equity_curve_length():
    data = {"TEST": make_sample_data(n=50)}
    engine = BacktestEngine(data=data, initial_capital=5_000_000)
    engine.run(AlwaysBuyStrategy())
    assert len(engine.results) == 50


def test_report_returns_dict():
    data = {"TEST": make_sample_data()}
    engine = BacktestEngine(data=data, initial_capital=10_000_000)
    engine.run(AlwaysBuyStrategy())
    metrics = engine.report()
    assert isinstance(metrics, dict)
    assert "총수익률(%)" in metrics
    assert "최대낙폭(MDD,%)" in metrics
    assert "샤프비율" in metrics


def test_commission_deducted():
    data = {"TEST": make_sample_data()}
    engine = BacktestEngine(data=data, initial_capital=10_000_000, commission_rate=0.001)
    engine.run(BuyAndSellStrategy())
    orders = engine.get_orders()
    buy_orders = orders[orders["action"] == "BUY"]
    assert (buy_orders["commission"] > 0).all()


if __name__ == "__main__":
    test_portfolio_initial_state()
    test_engine_run()
    test_buy_reduces_cash()
    test_sell_increases_cash()
    test_equity_curve_length()
    test_report_returns_dict()
    test_commission_deducted()
    print("\n모든 테스트 통과!")
