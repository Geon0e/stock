"""
백테스팅 실행 메인 스크립트

사용법:
    python backtest.py

종목코드 예시:
    005930 삼성전자
    000660 SK하이닉스
    035420 NAVER
    035720 카카오
    068270 셀트리온
    069500 KODEX 200 (ETF)
    114800 KODEX 인버스 (ETF)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from data.fetcher import get_ohlcv
from backtest import BacktestEngine
from backtest.strategies import (
    MovingAverageCrossStrategy,
    RSIStrategy,
    MomentumStrategy,
    BollingerBandStrategy,
)
from backtest.visualizer import plot_equity_curve, plot_price_with_signals, plot_monthly_returns


# ===================== 설정 =====================
TICKER = "005930"        # 삼성전자
START_DATE = "2020-01-01"
END_DATE = "2024-12-31"
INITIAL_CAPITAL = 10_000_000  # 1천만원

BENCHMARK_TICKER = "069500"  # KODEX 200
# ================================================


def run_single_strategy():
    """단일 전략 백테스트"""
    print("=" * 60)
    print("한국 주식 백테스팅 시스템")
    print("=" * 60)

    # 데이터 수집
    print(f"\n데이터 수집: {TICKER} ({START_DATE} ~ {END_DATE})")
    data = {TICKER: get_ohlcv(TICKER, START_DATE, END_DATE)}

    # 벤치마크 데이터
    try:
        benchmark_data = get_ohlcv(BENCHMARK_TICKER, START_DATE, END_DATE)
    except Exception:
        benchmark_data = None
        print("[경고] 벤치마크 데이터 수집 실패")

    # 전략 선택
    strategy = MovingAverageCrossStrategy(
        ticker=TICKER,
        short_window=20,
        long_window=60,
    )

    # 백테스트 실행
    engine = BacktestEngine(
        data=data,
        initial_capital=INITIAL_CAPITAL,
    )
    engine.run(strategy)

    # 성과 출력
    metrics = engine.report()

    # 차트 생성
    strategy_name = strategy.name()
    plot_equity_curve(engine, benchmark_data, strategy_name)
    plot_price_with_signals(engine, TICKER, strategy_name)
    plot_monthly_returns(engine, strategy_name)

    return engine, metrics


def compare_strategies():
    """여러 전략 비교"""
    print("=" * 60)
    print("전략 비교 백테스트")
    print("=" * 60)

    data = {TICKER: get_ohlcv(TICKER, START_DATE, END_DATE)}

    strategies = [
        MovingAverageCrossStrategy(TICKER, short_window=5, long_window=20),
        MovingAverageCrossStrategy(TICKER, short_window=20, long_window=60),
        RSIStrategy(TICKER, period=14, oversold=30, overbought=70),
        BollingerBandStrategy(TICKER, window=20, num_std=2.0),
        MomentumStrategy(TICKER, lookback=60),
    ]

    results = []
    for strategy in strategies:
        print(f"\n{'─' * 50}")
        engine = BacktestEngine(data=data, initial_capital=INITIAL_CAPITAL)
        engine.run(strategy)
        metrics = engine.report()
        metrics["전략명"] = strategy.name()
        results.append(metrics)

    # 비교표 출력
    import pandas as pd
    compare_df = pd.DataFrame(results).set_index("전략명")
    cols = ["총수익률(%)", "연환산수익률(CAGR,%)", "최대낙폭(MDD,%)", "샤프비율", "승률(%)"]
    available_cols = [c for c in cols if c in compare_df.columns]

    print("\n" + "=" * 60)
    print("전략 비교 요약")
    print("=" * 60)
    print(compare_df[available_cols].to_string())
    print("=" * 60)

    return results


def custom_strategy_example():
    """커스텀 전략 예시"""
    from backtest.strategies.base import BaseStrategy
    import pandas as pd

    class MyCustomStrategy(BaseStrategy):
        """
        커스텀 전략 예시: 5일선 위에서 RSI 과매도 시 매수
        """
        def __init__(self, ticker):
            self.ticker = ticker
            self.prices = []
            self.in_position = False

        def initialize(self, engine):
            print(f"[커스텀전략] 초기화: {self.ticker}")

        def on_bar(self, engine, date, data, prices):
            if self.ticker not in prices:
                return

            self.prices.append(prices[self.ticker])

            if len(self.prices) < 20:
                return

            # 5일 이동평균
            ma5 = sum(self.prices[-5:]) / 5
            price = self.prices[-1]

            # RSI 계산
            import numpy as np
            arr = np.array(self.prices[-15:])
            deltas = np.diff(arr)
            gains = np.where(deltas > 0, deltas, 0)
            losses = np.where(deltas < 0, -deltas, 0)
            rsi = 100 - 100 / (1 + gains.mean() / (losses.mean() + 1e-10))

            # 조건: 5일선 위 + RSI 과매도 구간 탈출
            if price > ma5 and rsi < 35 and not self.in_position:
                engine.buy_pct(date, self.ticker, 1.0)
                self.in_position = True

            elif rsi > 65 and self.in_position:
                engine.sell(date, self.ticker)
                self.in_position = False

        def name(self):
            return "커스텀(MA5+RSI)"

    data = {TICKER: get_ohlcv(TICKER, START_DATE, END_DATE)}
    engine = BacktestEngine(data=data, initial_capital=INITIAL_CAPITAL)
    engine.run(MyCustomStrategy(TICKER))
    engine.report()
    plot_equity_curve(engine, strategy_name="커스텀전략")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="한국 주식 백테스팅")
    parser.add_argument(
        "--mode",
        choices=["single", "compare", "custom"],
        default="single",
        help="실행 모드: single(단일전략) | compare(전략비교) | custom(커스텀전략)",
    )
    args = parser.parse_args()

    if args.mode == "single":
        run_single_strategy()
    elif args.mode == "compare":
        compare_strategies()
    elif args.mode == "custom":
        custom_strategy_example()
