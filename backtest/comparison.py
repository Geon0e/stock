"""
V1 vs V2 vs Buy & Hold 비교 프레임워크

주요 기능:
  - 동일 데이터에서 세 가지(V1·V2·B&H)를 동시 실행
  - 다기간(1Y·2Y·3Y) 일관성 검증
  - 슬리피지 민감도 테스트 (1x·2x·3x)
  - 로버스트니스 체크리스트 (5개 기준) 자동 평가
"""

from __future__ import annotations

import contextlib
import io
import numpy as np
import pandas as pd

from config import COMMISSION_RATE, TAX_RATE, SLIPPAGE_RATE


# ── 거래일 기준 기간 ──────────────────────────────────────────────────────────
PERIOD_LABELS = {252: "1년", 504: "2년", 756: "3년"}


# ── Buy & Hold 벤치마크 ───────────────────────────────────────────────────────

def _calc_metrics(equity: pd.Series, orders_df: pd.DataFrame, capital: float) -> dict:
    """공통 성과 지표 계산"""
    if equity.empty or len(equity) < 2:
        return {}

    returns = equity.pct_change().dropna()
    total_return = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
    days  = (equity.index[-1] - equity.index[0]).days
    years = max(days / 365, 0.01)
    cagr  = ((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100

    roll_max  = equity.cummax()
    drawdown  = (equity - roll_max) / roll_max * 100
    mdd       = drawdown.min()

    rf = 0.035 / 252
    excess = returns - rf
    sharpe = (excess.mean() / excess.std() * np.sqrt(252)
              if excess.std() > 0 else 0.0)

    n_trades = 0
    win_rate = 0.0
    if not orders_df.empty and "action" in orders_df.columns:
        n_trades = len(orders_df)
        buys  = orders_df[orders_df["action"] == "BUY"]
        sells = orders_df[orders_df["action"] == "SELL"]
        trades = []
        for ticker in orders_df["ticker"].unique():
            tb = buys[buys["ticker"] == ticker]["price"].tolist()
            ts = sells[sells["ticker"] == ticker]["price"].tolist()
            for b, s in zip(tb, ts):
                trades.append(s > b)
        win_rate = sum(trades) / len(trades) * 100 if trades else 0.0

    trades_per_year = n_trades / max(years, 0.1)

    return {
        "총수익률(%)":           round(total_return, 2),
        "CAGR(%)":              round(cagr, 2),
        "MDD(%)":               round(mdd, 2),
        "샤프비율":              round(sharpe, 2),
        "거래횟수":              n_trades,
        "연간거래횟수":           round(trades_per_year, 1),
        "승률(%)":               round(win_rate, 1),
        "최종자본":              round(equity.iloc[-1]),
    }


def calc_buyhold(df: pd.DataFrame, ticker: str, capital: float,
                 commission_rate: float = COMMISSION_RATE,
                 tax_rate: float = TAX_RATE,
                 slippage_rate: float = SLIPPAGE_RATE) -> dict:
    """첫날 매수 → 마지막날 매도 Buy & Hold"""
    if df is None or df.empty or len(df) < 2:
        return {}

    close = df["Close"]
    buy_price  = close.iloc[0]  * (1 + slippage_rate)
    sell_price = close.iloc[-1] * (1 - slippage_rate)

    buy_comm  = buy_price * commission_rate
    qty = int(capital / (buy_price + buy_comm))
    if qty <= 0:
        return {}

    cost     = (buy_price + buy_comm) * qty
    proceeds = sell_price * qty * (1 - commission_rate - tax_rate)
    cash_end = (capital - cost) + proceeds

    # 자산 곡선 (보유 중 시가 기준)
    equity = (capital - cost) + close / close.iloc[0] * (buy_price * qty)
    equity.iloc[-1] = cash_end

    orders_df = pd.DataFrame([
        {"date": df.index[0],  "ticker": ticker, "action": "BUY",  "price": buy_price},
        {"date": df.index[-1], "ticker": ticker, "action": "SELL", "price": sell_price},
    ])
    m = _calc_metrics(equity, orders_df, capital)
    m["전략"] = "Buy & Hold"
    return m


# ── 전략 실행 헬퍼 ────────────────────────────────────────────────────────────

def _run_engine(df: pd.DataFrame, ticker: str, strategy,
                capital: float, commission_rate: float,
                tax_rate: float, slippage_rate: float) -> dict:
    from backtest.engine import BacktestEngine

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        engine = BacktestEngine(
            data={ticker: df},
            initial_capital=capital,
            commission_rate=commission_rate,
            tax_rate=tax_rate,
            slippage_rate=slippage_rate,
        )
        engine.run(strategy)
        engine.report()

    if engine.results is None or engine.results.empty:
        return {}

    equity    = engine.results["total_value"]
    orders_df = engine.get_orders()
    m = _calc_metrics(equity, orders_df, capital)
    m["전략"] = strategy.name()
    m["_equity"]    = equity      # 차트용
    m["_orders_df"] = orders_df   # 차트용
    return m


# ── 비교 메인 ─────────────────────────────────────────────────────────────────

def run_comparison(
    df: pd.DataFrame,
    ticker: str,
    capital: float = 10_000_000,
    commission_rate: float = COMMISSION_RATE,
    tax_rate: float = TAX_RATE,
    base_slippage: float = SLIPPAGE_RATE,
    period_days: list[int] | None = None,
    slippage_multipliers: list[float] | None = None,
) -> dict:
    """
    V1 vs V2 vs B&H 전면 비교

    Returns:
        {
          "multi_period":  DataFrame  — 기간별 성과 테이블,
          "slippage_sens": DataFrame  — 슬리피지 민감도 테이블,
          "robustness":    list[dict] — 로버스트니스 체크리스트,
          "equity_full":   {v1, v2, bh} — 전체 기간 자산 곡선,
          "orders":        {v1, v2} — 매매 내역,
          "summary":       {v1, v2, bh} — 전체 기간 지표,
        }
    """
    from backtest.strategies.moving_average import MovingAverageCrossStrategy
    from backtest.strategies.moving_average_v2 import MovingAverageCrossV2Strategy

    if period_days is None:
        period_days = [252, 504, 756]
    if slippage_multipliers is None:
        slippage_multipliers = [1.0, 2.0, 3.0]

    # 사용 가능한 기간으로 필터
    period_days = [p for p in period_days if len(df) >= p]

    # ── 전체 기간 실행 ────────────────────────────────────────────────────
    def _v1(d): return _run_engine(d, ticker,
        MovingAverageCrossStrategy(ticker, 5, 20),
        capital, commission_rate, tax_rate, base_slippage)

    def _v2(d): return _run_engine(d, ticker,
        MovingAverageCrossV2Strategy(ticker, 5, 20, 60),
        capital, commission_rate, tax_rate, base_slippage)

    def _bh(d): return calc_buyhold(d, ticker, capital,
        commission_rate, tax_rate, base_slippage)

    r_v1 = _v1(df)
    r_v2 = _v2(df)
    r_bh = _bh(df)

    eq_v1 = r_v1.pop("_equity",    pd.Series(dtype=float))
    eq_v2 = r_v2.pop("_equity",    pd.Series(dtype=float))
    od_v1 = r_v1.pop("_orders_df", pd.DataFrame())
    od_v2 = r_v2.pop("_orders_df", pd.DataFrame())

    # ── 다기간 비교 ───────────────────────────────────────────────────────
    rows = []
    display_cols = ["CAGR(%)", "MDD(%)", "샤프비율", "연간거래횟수", "승률(%)"]
    for pd_days in period_days:
        sub = df.tail(pd_days)
        label = PERIOD_LABELS.get(pd_days, f"{pd_days}일")
        for name, fn in [("V1 (MA5/20)", lambda d: _v1(d)),
                         ("V2 (MA5/20/60+RSI)", lambda d: _v2(d)),
                         ("Buy & Hold", lambda d: _bh(d))]:
            m = fn(sub)
            m.pop("_equity", None); m.pop("_orders_df", None)
            row = {"기간": label, "전략": name}
            for c in display_cols:
                row[c] = m.get(c, None)
            rows.append(row)

    multi_df = pd.DataFrame(rows)

    # ── 슬리피지 민감도 ───────────────────────────────────────────────────
    slip_rows = []
    for mult in slippage_multipliers:
        slip = base_slippage * mult
        for name, fn in [
            ("V1", lambda d, s: _run_engine(d, ticker,
                MovingAverageCrossStrategy(ticker, 5, 20),
                capital, commission_rate, tax_rate, s)),
            ("V2", lambda d, s: _run_engine(d, ticker,
                MovingAverageCrossV2Strategy(ticker, 5, 20, 60),
                capital, commission_rate, tax_rate, s)),
        ]:
            m = fn(df, slip)
            m.pop("_equity", None); m.pop("_orders_df", None)
            slip_rows.append({
                "슬리피지": f"×{mult:.0f} ({slip*100:.3f}%)",
                "전략": name,
                "CAGR(%)": m.get("CAGR(%)", None),
                "MDD(%)":  m.get("MDD(%)", None),
                "거래횟수": m.get("거래횟수", None),
            })

    slip_df = pd.DataFrame(slip_rows)

    # ── 로버스트니스 체크리스트 ───────────────────────────────────────────
    robustness = _robustness_check(r_v1, r_v2, r_bh, multi_df, slip_df, base_slippage)

    return {
        "multi_period":  multi_df,
        "slippage_sens": slip_df,
        "robustness":    robustness,
        "equity_full":   {"v1": eq_v1, "v2": eq_v2, "bh": None},
        "orders":        {"v1": od_v1, "v2": od_v2},
        "summary":       {"v1": r_v1, "v2": r_v2, "bh": r_bh},
    }


# ── 로버스트니스 체크 ─────────────────────────────────────────────────────────

def _robustness_check(v1: dict, v2: dict, bh: dict,
                      multi_df: pd.DataFrame,
                      slip_df: pd.DataFrame,
                      base_slippage: float) -> list[dict]:
    """
    5개 기준으로 V1·V2 각각 pass/fail 평가
    Returns: [{"항목": ..., "기준": ..., "V1": pass/fail/val, "V2": ...}, ...]
    """
    results = []

    def _chk(label, criterion, v1_ok, v2_ok, v1_val="", v2_val=""):
        results.append({
            "항목": label,
            "기준": criterion,
            "V1":  ("✅ " + str(v1_val)) if v1_ok else ("❌ " + str(v1_val)),
            "V2":  ("✅ " + str(v2_val)) if v2_ok else ("❌ " + str(v2_val)),
        })

    bh_cagr = bh.get("CAGR(%)", 0) or 0

    # 1. CAGR vs B&H
    v1_cagr = v1.get("CAGR(%)", 0) or 0
    v2_cagr = v2.get("CAGR(%)", 0) or 0
    _chk("CAGR > B&H + 2%p",
         f"B&H CAGR = {bh_cagr:+.1f}%",
         v1_cagr >= bh_cagr + 2,
         v2_cagr >= bh_cagr + 2,
         f"{v1_cagr:+.1f}%", f"{v2_cagr:+.1f}%")

    # 2. MDD <= B&H MDD × 1.1
    bh_mdd = bh.get("MDD(%)", -999) or -999
    v1_mdd = v1.get("MDD(%)", -999) or -999
    v2_mdd = v2.get("MDD(%)", -999) or -999
    threshold = bh_mdd * 1.1  # bh_mdd 음수이므로 × 1.1 → 더 작은 음수 (더 나쁨)
    _chk("MDD ≤ B&H MDD",
         f"B&H MDD = {bh_mdd:.1f}%",
         v1_mdd >= threshold,
         v2_mdd >= threshold,
         f"{v1_mdd:.1f}%", f"{v2_mdd:.1f}%")

    # 3. 연간 거래횟수 <= 24
    v1_tpy = v1.get("연간거래횟수", 999) or 999
    v2_tpy = v2.get("연간거래횟수", 999) or 999
    _chk("연간 거래 ≤ 24회",
         "과도한 매매 = 비용 누적",
         v1_tpy <= 24, v2_tpy <= 24,
         f"{v1_tpy:.0f}회", f"{v2_tpy:.0f}회")

    # 4. 슬리피지 2배에도 CAGR 양수
    slip2x = slip_df[slip_df["슬리피지"].str.startswith("×2")]
    v1_s2  = slip2x[slip2x["전략"] == "V1"]["CAGR(%)"].values
    v2_s2  = slip2x[slip2x["전략"] == "V2"]["CAGR(%)"].values
    v1_s2v = float(v1_s2[0]) if len(v1_s2) else 0
    v2_s2v = float(v2_s2[0]) if len(v2_s2) else 0
    _chk("슬리피지 2배 후 CAGR > 0",
         "비용 내성 확인",
         v1_s2v > 0, v2_s2v > 0,
         f"{v1_s2v:+.1f}%", f"{v2_s2v:+.1f}%")

    # 5. 다기간 일관성 — 2개 이상 기간에서 CAGR > B&H
    if not multi_df.empty and "기간" in multi_df.columns:
        periods = multi_df["기간"].unique()
        v1_wins, v2_wins = 0, 0
        for p in periods:
            sub = multi_df[multi_df["기간"] == p]
            bh_c = sub[sub["전략"] == "Buy & Hold"]["CAGR(%)"].values
            v1_c = sub[sub["전략"] == "V1 (MA5/20)"]["CAGR(%)"].values
            v2_c = sub[sub["전략"] == "V2 (MA5/20/60+RSI)"]["CAGR(%)"].values
            if len(bh_c) and len(v1_c) and v1_c[0] is not None:
                v1_wins += int(float(v1_c[0]) > float(bh_c[0]))
            if len(bh_c) and len(v2_c) and v2_c[0] is not None:
                v2_wins += int(float(v2_c[0]) > float(bh_c[0]))
        n_periods = len(periods)
        _chk(f"다기간 일관성 ({n_periods}개 기간 중 과반)",
             "최근 1년만 좋으면 탈락",
             v1_wins > n_periods // 2,
             v2_wins > n_periods // 2,
             f"{v1_wins}/{n_periods}", f"{v2_wins}/{n_periods}")

    return results
