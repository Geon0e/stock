"""
백테스팅 엔진 핵심 모듈
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from config import DEFAULT_CAPITAL, COMMISSION_RATE, TAX_RATE, SLIPPAGE_RATE


@dataclass
class Order:
    date: pd.Timestamp
    ticker: str
    action: str      # 'BUY' | 'SELL'
    quantity: int
    price: float
    commission: float = 0.0
    tax: float = 0.0


@dataclass
class Position:
    ticker: str
    quantity: int = 0
    avg_price: float = 0.0

    @property
    def cost(self):
        return self.quantity * self.avg_price


class Portfolio:
    """포트폴리오 상태 관리"""

    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: dict[str, Position] = {}
        self.orders: list[Order] = []
        self.equity_curve: list[dict] = []

    def get_position(self, ticker: str) -> Position:
        if ticker not in self.positions:
            self.positions[ticker] = Position(ticker)
        return self.positions[ticker]

    def get_total_value(self, current_prices: dict) -> float:
        stock_value = sum(
            pos.quantity * current_prices.get(pos.ticker, pos.avg_price)
            for pos in self.positions.values()
            if pos.quantity > 0
        )
        return self.cash + stock_value

    def get_holdings_value(self, current_prices: dict) -> float:
        return sum(
            pos.quantity * current_prices.get(pos.ticker, pos.avg_price)
            for pos in self.positions.values()
            if pos.quantity > 0
        )


class BacktestEngine:
    """
    백테스팅 엔진

    사용법:
        engine = BacktestEngine(data, initial_capital=10_000_000)
        engine.run(MyStrategy())
        engine.report()
    """

    def __init__(
        self,
        data: dict[str, pd.DataFrame],
        initial_capital: float = DEFAULT_CAPITAL,
        commission_rate: float = COMMISSION_RATE,
        tax_rate: float = TAX_RATE,
        slippage_rate: float = SLIPPAGE_RATE,
    ):
        """
        Args:
            data: {ticker: OHLCV DataFrame} 딕셔너리
            initial_capital: 초기 자본금
            commission_rate: 수수료율 (매수/매도 각각)
            tax_rate: 증권거래세율 (매도 시)
            slippage_rate: 슬리피지율
        """
        self.data = data
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.tax_rate = tax_rate
        self.slippage_rate = slippage_rate

        # 공통 날짜 인덱스 생성
        all_dates = set()
        for df in data.values():
            all_dates.update(df.index.tolist())
        self.dates = sorted(all_dates)

        self.portfolio = Portfolio(initial_capital)
        self.results: Optional[pd.DataFrame] = None

    def run(self, strategy) -> "BacktestEngine":
        """백테스트 실행"""
        print(f"백테스트 시작: {self.dates[0].date()} ~ {self.dates[-1].date()}")
        print(f"초기 자본금: {self.initial_capital:,.0f}원")
        print("-" * 50)

        strategy.initialize(self)

        for date in self.dates:
            current_prices = {}
            current_data = {}

            for ticker, df in self.data.items():
                if date in df.index:
                    row = df.loc[date]
                    current_prices[ticker] = row["Close"]
                    current_data[ticker] = row

            # 전략 실행
            strategy.on_bar(self, date, current_data, current_prices)

            # 자산 기록
            total_value = self.portfolio.get_total_value(current_prices)
            holdings_value = self.portfolio.get_holdings_value(current_prices)

            self.portfolio.equity_curve.append({
                "date": date,
                "total_value": total_value,
                "cash": self.portfolio.cash,
                "holdings_value": holdings_value,
            })

        self.results = pd.DataFrame(self.portfolio.equity_curve).set_index("date")
        print("\n백테스트 완료!")
        return self

    def buy(self, date: pd.Timestamp, ticker: str, quantity: int, price: float = None) -> bool:
        """매수 주문"""
        if price is None:
            if date in self.data[ticker].index:
                price = self.data[ticker].loc[date, "Close"]
            else:
                return False

        # 슬리피지 적용 (매수 시 불리하게)
        exec_price = price * (1 + self.slippage_rate)
        commission = exec_price * quantity * self.commission_rate
        total_cost = exec_price * quantity + commission

        if total_cost > self.portfolio.cash:
            # 살 수 있는 최대 수량으로 조정
            quantity = int(self.portfolio.cash / (exec_price * (1 + self.commission_rate)))
            if quantity <= 0:
                return False
            commission = exec_price * quantity * self.commission_rate
            total_cost = exec_price * quantity + commission

        # 포지션 업데이트
        pos = self.portfolio.get_position(ticker)
        total_qty = pos.quantity + quantity
        pos.avg_price = (pos.avg_price * pos.quantity + exec_price * quantity) / total_qty
        pos.quantity = total_qty

        self.portfolio.cash -= total_cost

        order = Order(date, ticker, "BUY", quantity, exec_price, commission)
        self.portfolio.orders.append(order)
        return True

    def sell(self, date: pd.Timestamp, ticker: str, quantity: int = None, price: float = None) -> bool:
        """매도 주문"""
        pos = self.portfolio.get_position(ticker)
        if pos.quantity <= 0:
            return False

        if quantity is None:
            quantity = pos.quantity  # 전량 매도

        quantity = min(quantity, pos.quantity)

        if price is None:
            if date in self.data[ticker].index:
                price = self.data[ticker].loc[date, "Close"]
            else:
                return False

        # 슬리피지 적용 (매도 시 불리하게)
        exec_price = price * (1 - self.slippage_rate)
        commission = exec_price * quantity * self.commission_rate
        tax = exec_price * quantity * self.tax_rate
        proceeds = exec_price * quantity - commission - tax

        pos.quantity -= quantity
        if pos.quantity == 0:
            pos.avg_price = 0.0

        self.portfolio.cash += proceeds

        order = Order(date, ticker, "SELL", quantity, exec_price, commission, tax)
        self.portfolio.orders.append(order)
        return True

    def buy_pct(self, date: pd.Timestamp, ticker: str, pct: float, price: float = None) -> bool:
        """
        보유 현금의 일정 비율만큼 매수

        Args:
            pct: 0.0 ~ 1.0 (예: 0.1 = 10%)
        """
        if price is None:
            if date not in self.data[ticker].index:
                return False
            price = self.data[ticker].loc[date, "Close"]

        budget = self.portfolio.cash * pct
        quantity = int(budget / (price * (1 + self.commission_rate + self.slippage_rate)))
        if quantity <= 0:
            return False
        return self.buy(date, ticker, quantity, price)

    def get_position(self, ticker: str) -> Position:
        return self.portfolio.get_position(ticker)

    def get_cash(self) -> float:
        return self.portfolio.cash

    def get_orders(self) -> pd.DataFrame:
        if not self.portfolio.orders:
            return pd.DataFrame()
        return pd.DataFrame([vars(o) for o in self.portfolio.orders])

    def report(self) -> dict:
        """성과 지표 계산 및 출력"""
        if self.results is None:
            print("먼저 run()을 실행하세요.")
            return {}

        equity = self.results["total_value"]
        returns = equity.pct_change().dropna()

        total_return = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
        days = (equity.index[-1] - equity.index[0]).days
        years = days / 365
        cagr = ((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100 if years > 0 else 0

        # 최대 낙폭 (MDD)
        roll_max = equity.cummax()
        drawdown = (equity - roll_max) / roll_max * 100
        mdd = drawdown.min()

        # 샤프 비율 (연율화, 무위험수익률 3.5% 가정)
        risk_free = 0.035 / 252
        excess_returns = returns - risk_free
        sharpe = (excess_returns.mean() / excess_returns.std() * np.sqrt(252)
                  if excess_returns.std() > 0 else 0)

        # 승률
        orders_df = self.get_orders()
        win_rate = self._calc_win_rate(orders_df)

        metrics = {
            "초기자본": self.initial_capital,
            "최종자본": equity.iloc[-1],
            "총수익률(%)": round(total_return, 2),
            "연환산수익률(CAGR,%)": round(cagr, 2),
            "최대낙폭(MDD,%)": round(mdd, 2),
            "샤프비율": round(sharpe, 2),
            "총거래횟수": len(orders_df) if not orders_df.empty else 0,
            "승률(%)": round(win_rate, 2),
            "백테스트기간": f"{equity.index[0].date()} ~ {equity.index[-1].date()}",
        }

        print("\n" + "=" * 50)
        print("백테스트 성과 요약")
        print("=" * 50)
        for k, v in metrics.items():
            if isinstance(v, float):
                print(f"  {k:<25} {v:>12,.2f}")
            elif isinstance(v, int):
                print(f"  {k:<25} {v:>12,}")
            else:
                print(f"  {k:<25} {v}")
        print("=" * 50)

        return metrics

    def _calc_win_rate(self, orders_df: pd.DataFrame) -> float:
        """매매 승률 계산"""
        if orders_df.empty or "action" not in orders_df.columns:
            return 0.0

        trades = []
        buys = orders_df[orders_df["action"] == "BUY"].copy()
        sells = orders_df[orders_df["action"] == "SELL"].copy()

        for ticker in orders_df["ticker"].unique():
            t_buys = buys[buys["ticker"] == ticker].to_dict("records")
            t_sells = sells[sells["ticker"] == ticker].to_dict("records")

            for buy, sell in zip(t_buys, t_sells):
                pnl = (sell["price"] - buy["price"]) / buy["price"]
                trades.append(pnl > 0)

        return sum(trades) / len(trades) * 100 if trades else 0.0
