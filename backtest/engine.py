"""
백테스팅 엔진 (기관급 설계)

개선사항:
  1. T+1 체결  — 신호는 Close[t], 체결은 Open[t+1]  (look-ahead bias 제거)
  2. 거래량 기반 슬리피지 — base(0.02%) + market impact (주문/일거래대금 × 0.1)
  3. ATR 손절  — register_stop() 등록 시 Low[t] 기준 장중 체결
  4. 퀀트 성과지표 — Profit Factor / Expectancy / Sortino / Calmar
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

from config import DEFAULT_CAPITAL, COMMISSION_RATE, TAX_RATE, SLIPPAGE_BASE


# ── 데이터 클래스 ─────────────────────────────────────────────────────────

@dataclass
class Order:
    date:       pd.Timestamp
    ticker:     str
    action:     str        # 'BUY' | 'SELL'
    quantity:   int
    price:      float
    commission: float = 0.0
    tax:        float = 0.0
    order_type: str   = "MARKET"   # 'MARKET' | 'STOP'


@dataclass
class PendingOrder:
    """T+1 Open 체결 대기 주문"""
    ticker:   str
    action:   str          # 'BUY' | 'SELL'
    pct:      float = None   # buy_pct용 (0~1)
    quantity: int   = None   # sell용 (None = 전량)


@dataclass
class Position:
    ticker:     str
    quantity:   int   = 0
    avg_price:  float = 0.0
    entry_date: object = None   # pd.Timestamp

    @property
    def cost(self):
        return self.quantity * self.avg_price


# ── 포트폴리오 ────────────────────────────────────────────────────────────

class Portfolio:
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
        return self.cash + self.get_holdings_value(current_prices)

    def get_holdings_value(self, current_prices: dict) -> float:
        return sum(
            pos.quantity * current_prices.get(pos.ticker, pos.avg_price)
            for pos in self.positions.values()
            if pos.quantity > 0
        )


# ── 엔진 ─────────────────────────────────────────────────────────────────

class BacktestEngine:
    """
    기관급 백테스팅 엔진

    사용법:
        engine = BacktestEngine(data={ticker: df}, initial_capital=10_000_000)
        engine.run(strategy)
        metrics = engine.report()
    """

    def __init__(
        self,
        data: dict,                        # {ticker: OHLCV DataFrame}
        initial_capital: float = DEFAULT_CAPITAL,
        commission_rate: float = COMMISSION_RATE,
        tax_rate:        float = TAX_RATE,
        slippage_rate:   float = SLIPPAGE_BASE,
    ):
        self.data             = data
        self.initial_capital  = initial_capital
        self.commission_rate  = commission_rate
        self.tax_rate         = tax_rate
        self.slippage_rate    = slippage_rate   # base slippage

        all_dates = set()
        for df in data.values():
            all_dates.update(df.index.tolist())
        self.dates = sorted(all_dates)

        self.portfolio      = Portfolio(initial_capital)
        self.results:       Optional[pd.DataFrame] = None
        self.pending_orders: list[PendingOrder] = []
        self.stops:         dict[str, float] = {}   # {ticker: stop_price}

    # ── 슬리피지 계산 ────────────────────────────────────────────────────

    def _calc_slippage(self, ticker: str, price: float, quantity: int,
                       date: pd.Timestamp, is_buy: bool) -> float:
        """거래량 기반 슬리피지 = base + market impact"""
        base   = self.slippage_rate
        impact = 0.0
        try:
            row    = self.data[ticker].loc[date]
            volume = float(row.get("Volume", 0) if hasattr(row, 'get') else row["Volume"])
            if volume > 0:
                order_value    = price * quantity
                daily_turnover = price * volume
                impact = (order_value / daily_turnover) * 0.1
                impact = min(impact, 0.005)   # cap 0.5%
        except Exception:
            pass
        total = base + impact
        return total if is_buy else -total   # 매수: +, 매도: -

    # ── 주문 등록 (T+1 대기) ─────────────────────────────────────────────

    def buy(self, date: pd.Timestamp, ticker: str, quantity: int, price: float = None) -> bool:
        """매수 주문 → T+1 Open 체결"""
        self.pending_orders.append(PendingOrder(ticker=ticker, action="BUY", quantity=quantity))
        return True

    def sell(self, date: pd.Timestamp, ticker: str, quantity: int = None, price: float = None) -> bool:
        """매도 주문 → T+1 Open 체결"""
        pos = self.portfolio.get_position(ticker)
        if pos.quantity <= 0 and not any(
            p.ticker == ticker and p.action == "BUY" for p in self.pending_orders
        ):
            return False
        self.pending_orders.append(PendingOrder(ticker=ticker, action="SELL", quantity=quantity))
        return True

    def buy_pct(self, date: pd.Timestamp, ticker: str, pct: float, price: float = None) -> bool:
        """현금 비율 매수 → T+1 Open 체결"""
        self.pending_orders.append(PendingOrder(ticker=ticker, action="BUY", pct=pct))
        return True

    # ── 손절 관리 ────────────────────────────────────────────────────────

    def register_stop(self, ticker: str, stop_price: float) -> None:
        """ATR 손절가 등록. Low[t] ≤ stop_price 시 장중 즉시 체결."""
        self.stops[ticker] = stop_price

    def clear_stop(self, ticker: str) -> None:
        self.stops.pop(ticker, None)

    def get_stop(self, ticker: str) -> Optional[float]:
        return self.stops.get(ticker)

    # ── 즉시 매도 (손절 전용) ────────────────────────────────────────────

    def _execute_sell_immediate(self, date: pd.Timestamp, ticker: str, price: float) -> bool:
        pos = self.portfolio.get_position(ticker)
        if pos.quantity <= 0:
            return False

        quantity  = pos.quantity
        slip      = abs(self._calc_slippage(ticker, price, quantity, date, is_buy=False))
        exec_price = price * (1 - slip)
        commission = exec_price * quantity * self.commission_rate
        tax        = exec_price * quantity * self.tax_rate
        proceeds   = exec_price * quantity - commission - tax

        pos.quantity  = 0
        pos.avg_price = 0.0
        self.portfolio.cash += proceeds
        self.portfolio.orders.append(
            Order(date, ticker, "SELL", quantity, exec_price, commission, tax, "STOP")
        )
        self.clear_stop(ticker)
        return True

    # ── T+1 대기 주문 실행 ───────────────────────────────────────────────

    def _execute_pending(self, date: pd.Timestamp, open_data: dict) -> None:
        """대기 주문을 오늘 시가(Open)에 체결"""
        pending = self.pending_orders[:]
        self.pending_orders.clear()

        for order in pending:
            ticker     = order.ticker
            open_price = open_data.get(ticker)
            if open_price is None or open_price <= 0:
                continue

            if order.action == "BUY":
                pct  = order.pct if order.pct is not None else 1.0
                slip = self._calc_slippage(ticker, open_price, 1, date, is_buy=True)
                exec_price = open_price * (1 + slip)
                budget     = self.portfolio.cash * pct
                qty        = int(budget / (exec_price * (1 + self.commission_rate)))
                if qty <= 0:
                    continue
                commission = exec_price * qty * self.commission_rate
                total_cost = exec_price * qty + commission
                if total_cost > self.portfolio.cash:
                    qty = int(self.portfolio.cash / (exec_price * (1 + self.commission_rate)))
                    if qty <= 0:
                        continue
                    commission = exec_price * qty * self.commission_rate
                    total_cost = exec_price * qty + commission

                pos = self.portfolio.get_position(ticker)
                total_qty = pos.quantity + qty
                pos.avg_price = (pos.avg_price * pos.quantity + exec_price * qty) / total_qty
                pos.quantity  = total_qty
                pos.entry_date = date
                self.portfolio.cash -= total_cost
                self.portfolio.orders.append(Order(date, ticker, "BUY", qty, exec_price, commission))

            elif order.action == "SELL":
                pos = self.portfolio.get_position(ticker)
                if pos.quantity <= 0:
                    continue
                quantity  = order.quantity if order.quantity is not None else pos.quantity
                quantity  = min(quantity, pos.quantity)
                slip      = self._calc_slippage(ticker, open_price, quantity, date, is_buy=False)
                exec_price = open_price * (1 + slip)  # slip 음수 → 매도가 낮아짐
                commission = exec_price * quantity * self.commission_rate
                tax        = exec_price * quantity * self.tax_rate
                proceeds   = exec_price * quantity - commission - tax

                pos.quantity -= quantity
                if pos.quantity == 0:
                    pos.avg_price = 0.0
                self.portfolio.cash += proceeds
                self.portfolio.orders.append(Order(date, ticker, "SELL", quantity, exec_price, commission, tax))
                self.clear_stop(ticker)

    # ── 손절 체크 (장중 Low 기준) ────────────────────────────────────────

    def _check_stops(self, date: pd.Timestamp, current_data: dict) -> None:
        """Low[t] ≤ stop_price 이면 즉시 매도"""
        for ticker, stop_price in list(self.stops.items()):
            pos = self.portfolio.get_position(ticker)
            if pos.quantity <= 0:
                self.stops.pop(ticker, None)
                continue
            row = current_data.get(ticker)
            if row is None:
                continue
            try:
                low = float(row["Low"]) if "Low" in row.index else float(row["Close"])
            except Exception:
                continue
            if low <= stop_price:
                # 갭다운 고려: 실제 체결은 Low와 stop_price 중 높은 쪽
                fill_price = max(low, stop_price * 0.97)
                self._execute_sell_immediate(date, ticker, fill_price)
                print(f"  [손절] {date.date()} {ticker} @ {fill_price:,.0f}  stop={stop_price:,.0f}")

    # ── 메인 루프 ────────────────────────────────────────────────────────

    def run(self, strategy) -> "BacktestEngine":
        """백테스트 실행 (T+1 Open 체결 방식)"""
        print(f"백테스트 시작: {self.dates[0].date()} ~ {self.dates[-1].date()}")
        print(f"초기 자본금: {self.initial_capital:,.0f}  |  T+1 Open 체결  |  base slip={self.slippage_rate*100:.3f}%")
        print("-" * 55)

        strategy.initialize(self)

        for date in self.dates:
            open_data:      dict = {}
            current_data:   dict = {}
            current_prices: dict = {}

            for ticker, df in self.data.items():
                if date in df.index:
                    row = df.loc[date]
                    current_prices[ticker] = float(row["Close"])
                    open_data[ticker]      = float(row["Open"]) if "Open" in row.index else float(row["Close"])
                    current_data[ticker]   = row

            # 1. T+1 대기 주문 → 오늘 시가에 체결
            self._execute_pending(date, open_data)

            # 2. 손절 체크 (오늘 장중 Low 기준)
            self._check_stops(date, current_data)

            # 3. 전략 실행 → 신호 발생 → 대기 주문 등록
            strategy.on_bar(self, date, current_data, current_prices)

            # 4. 자산 기록 (종가 기준)
            total_value    = self.portfolio.get_total_value(current_prices)
            holdings_value = self.portfolio.get_holdings_value(current_prices)
            self.portfolio.equity_curve.append({
                "date":           date,
                "total_value":    total_value,
                "cash":           self.portfolio.cash,
                "holdings_value": holdings_value,
            })

        self.results = pd.DataFrame(self.portfolio.equity_curve).set_index("date")
        print("\n백테스트 완료!")
        return self

    # ── 조회 헬퍼 ────────────────────────────────────────────────────────

    def get_position(self, ticker: str) -> Position:
        return self.portfolio.get_position(ticker)

    def get_cash(self) -> float:
        return self.portfolio.cash

    def get_orders(self) -> pd.DataFrame:
        if not self.portfolio.orders:
            return pd.DataFrame()
        return pd.DataFrame([vars(o) for o in self.portfolio.orders])

    # ── 성과 지표 ────────────────────────────────────────────────────────

    def report(self) -> dict:
        if self.results is None:
            print("먼저 run()을 실행하세요.")
            return {}

        equity  = self.results["total_value"]
        returns = equity.pct_change().dropna()

        total_return = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
        days         = (equity.index[-1] - equity.index[0]).days
        years        = max(days / 365, 0.01)
        cagr         = ((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100

        # MDD
        roll_max = equity.cummax()
        drawdown = (equity - roll_max) / roll_max * 100
        mdd      = drawdown.min()

        # 무위험 수익률 연 3.5%
        rf = 0.035 / 252

        # Sharpe
        excess = returns - rf
        sharpe = (excess.mean() / excess.std() * np.sqrt(252)
                  if excess.std() > 0 else 0.0)

        # Sortino (하방 변동성만)
        downside = returns[returns < rf]
        sortino  = (((returns.mean() - rf) / downside.std() * np.sqrt(252))
                    if len(downside) > 1 and downside.std() > 0 else 0.0)

        # Calmar
        calmar = abs(cagr / mdd) if mdd != 0 else 0.0

        # 거래 통계
        orders_df   = self.get_orders()
        trade_stats = self._calc_trade_stats(orders_df)
        n_trades    = len(orders_df) if not orders_df.empty else 0

        metrics = {
            "초기자본":              self.initial_capital,
            "최종자본":              round(equity.iloc[-1]),
            "총수익률(%)":           round(total_return, 2),
            "연환산수익률(CAGR,%)":  round(cagr, 2),
            "최대낙폭(MDD,%)":       round(mdd, 2),
            "샤프비율":              round(sharpe, 2),
            "소르티노비율":           round(sortino, 2),
            "칼마비율":              round(calmar, 2),
            "Profit Factor":        round(trade_stats["profit_factor"], 2),
            "Expectancy(%)":        round(trade_stats["expectancy"], 2),
            "총거래횟수":            n_trades,
            "승률(%)":              round(trade_stats["win_rate"], 2),
            "백테스트기간":          f"{equity.index[0].date()} ~ {equity.index[-1].date()}",
        }

        print("\n" + "=" * 55)
        print("백테스트 성과 요약 (T+1 Open 체결)")
        print("=" * 55)
        for k, v in metrics.items():
            if isinstance(v, float):
                print(f"  {k:<26} {v:>12,.2f}")
            elif isinstance(v, int):
                print(f"  {k:<26} {v:>12,}")
            else:
                print(f"  {k:<26} {v}")
        print("=" * 55)

        return metrics

    def _calc_trade_stats(self, orders_df: pd.DataFrame) -> dict:
        """매매 쌍 기반 거래 통계 (Profit Factor, Expectancy, 승률)"""
        empty = {"win_rate": 0.0, "profit_factor": 0.0, "expectancy": 0.0}
        if orders_df.empty or "action" not in orders_df.columns:
            return empty

        buys  = orders_df[orders_df["action"] == "BUY"]
        sells = orders_df[orders_df["action"] == "SELL"]
        trade_returns = []

        for ticker in orders_df["ticker"].unique():
            tb = buys[buys["ticker"]  == ticker]["price"].tolist()
            ts = sells[sells["ticker"] == ticker]["price"].tolist()
            for b, s in zip(tb, ts):
                trade_returns.append((s - b) / b * 100)

        if not trade_returns:
            return empty

        wins   = [r for r in trade_returns if r > 0]
        losses = [r for r in trade_returns if r <= 0]
        n      = len(trade_returns)

        win_rate     = len(wins) / n * 100
        total_gain   = sum(wins)
        total_loss   = abs(sum(losses))
        profit_factor = (total_gain / total_loss if total_loss > 0
                         else (10.0 if total_gain > 0 else 0.0))
        avg_win  = np.mean(wins)         if wins   else 0.0
        avg_loss = abs(np.mean(losses))  if losses else 0.0
        expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

        return {
            "win_rate":      win_rate,
            "profit_factor": profit_factor,
            "expectancy":    expectancy,
        }
