"""
백테스팅 비교 / Walk-Forward 분석 프레임워크

주요 기능:
  - 퀀트 성과지표: Profit Factor / Expectancy / Sortino / Calmar
  - Walk-Forward Analysis: 롤링 Train/Test 창으로 과최적화 검증
  - 슬리피지 민감도 테스트
  - 로버스트니스 체크리스트
"""

from __future__ import annotations

import contextlib
import io
import numpy as np
import pandas as pd
from typing import Optional

from config import COMMISSION_RATE, TAX_RATE, SLIPPAGE_BASE


# ── 공통 성과 지표 계산 ───────────────────────────────────────────────────

def _calc_metrics(equity: pd.Series, orders_df: pd.DataFrame, capital: float) -> dict:
    """퀀트 표준 성과 지표 (Sharpe / Sortino / Calmar / Profit Factor / Expectancy / 회복계수 등)"""
    if equity.empty or len(equity) < 2:
        return {}

    returns = equity.pct_change().dropna()
    days    = (equity.index[-1] - equity.index[0]).days
    years   = max(days / 365, 0.01)

    total_return = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
    cagr         = ((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100

    roll_max = equity.cummax()
    drawdown = (equity - roll_max) / roll_max * 100
    mdd      = drawdown.min()

    # 회복계수 = 총수익률 / |MDD| (손실 1에 대한 수익 배율)
    recovery_factor = abs(total_return / mdd) if mdd != 0 else 0.0

    rf     = 0.035 / 252
    excess = returns - rf
    sharpe = (excess.mean() / excess.std() * np.sqrt(252)
              if excess.std() > 0 else 0.0)

    downside = returns[returns < rf]
    sortino  = (((returns.mean() - rf) / downside.std() * np.sqrt(252))
                if len(downside) > 1 and downside.std() > 0 else 0.0)

    calmar = abs(cagr / mdd) if mdd != 0 else 0.0

    # Omega Ratio: 임계값(rf) 초과 수익 합 / 임계값 미만 손실 합
    gains_omega = returns[returns > rf] - rf
    loss_omega  = rf - returns[returns <= rf]
    omega = (gains_omega.sum() / loss_omega.sum()
             if loss_omega.sum() > 0 else (10.0 if gains_omega.sum() > 0 else 0.0))

    # 거래 통계
    trade_results: list = []   # (pnl%, holding_days) 순서 있는 리스트
    n_trades = 0
    if not orders_df.empty and "action" in orders_df.columns:
        n_trades = len(orders_df)
        buys  = orders_df[orders_df["action"] == "BUY"].copy()
        sells = orders_df[orders_df["action"] == "SELL"].copy()
        for ticker in orders_df["ticker"].unique():
            t_buys  = buys[buys["ticker"]   == ticker].reset_index(drop=True)
            t_sells = sells[sells["ticker"] == ticker].reset_index(drop=True)
            for i in range(min(len(t_buys), len(t_sells))):
                buy_row  = t_buys.iloc[i]
                sell_row = t_sells.iloc[i]
                pnl = (sell_row["price"] - buy_row["price"]) / buy_row["price"] * 100
                hd  = 0
                if "date" in buy_row.index and "date" in sell_row.index:
                    try:
                        hd = (pd.Timestamp(sell_row["date"]) - pd.Timestamp(buy_row["date"])).days
                        hd = max(hd, 0)
                    except Exception:
                        pass
                trade_results.append((pnl, hd))

    wins         = [r[0] for r in trade_results if r[0] > 0]
    losses       = [r[0] for r in trade_results if r[0] <= 0]
    holding_days = [r[1] for r in trade_results]

    n_closed = len(wins) + len(losses)
    win_rate = len(wins) / n_closed * 100 if n_closed > 0 else 0.0

    total_gain    = sum(wins)
    total_loss    = abs(sum(losses))
    profit_factor = (total_gain / total_loss if total_loss > 0
                     else (10.0 if total_gain > 0 else 0.0))
    avg_win   = np.mean(wins)         if wins   else 0.0
    avg_loss  = abs(np.mean(losses))  if losses else 0.0
    expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

    # R배수 = 평균수익 / 평균손실
    r_multiple = avg_win / avg_loss if avg_loss > 0 else 0.0

    # 최대 연속 손실 횟수 (순서 있는 trade_results로 계산)
    max_consec_losses = _max_consecutive_losses([r[0] for r in trade_results])

    # 평균 보유 기간
    avg_holding = round(np.mean(holding_days), 1) if holding_days else 0.0

    return {
        "총수익률(%)":       round(total_return, 2),
        "CAGR(%)":          round(cagr, 2),
        "MDD(%)":           round(mdd, 2),
        "샤프비율":          round(sharpe, 2),
        "소르티노비율":       round(sortino, 2),
        "칼마비율":          round(calmar, 2),
        "오메가비율":         round(omega, 2),
        "회복계수":           round(recovery_factor, 2),
        "Profit Factor":    round(profit_factor, 2),
        "Expectancy(%)":    round(expectancy, 2),
        "R배수(평균승/패)":   round(r_multiple, 2),
        "거래횟수":          n_trades,
        "연간거래횟수":       round(n_trades / max(years, 0.1), 1),
        "승률(%)":           round(win_rate, 1),
        "최대연속손실":       max_consec_losses,
        "평균보유일":         avg_holding,
        "최종자본":          round(equity.iloc[-1]),
    }


def _max_consecutive_losses(pnl_list: list) -> int:
    """순서 있는 PnL 리스트에서 최대 연속 손실 횟수 계산"""
    if not pnl_list:
        return 0
    max_streak = current = 0
    for pnl in pnl_list:
        if pnl <= 0:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


# ── Buy & Hold 벤치마크 ───────────────────────────────────────────────────

def calc_buyhold(df: pd.DataFrame, ticker: str, capital: float,
                 commission_rate: float = COMMISSION_RATE,
                 tax_rate:        float = TAX_RATE,
                 slippage_rate:   float = SLIPPAGE_BASE) -> dict:
    """첫날 매수 → 마지막날 매도 Buy & Hold"""
    if df is None or df.empty or len(df) < 2:
        return {}
    close      = df["Close"]
    buy_price  = close.iloc[0]  * (1 + slippage_rate)
    sell_price = close.iloc[-1] * (1 - slippage_rate)
    buy_comm   = buy_price * commission_rate
    qty        = int(capital / (buy_price + buy_comm))
    if qty <= 0:
        return {}
    cost     = (buy_price + buy_comm) * qty
    proceeds = sell_price * qty * (1 - commission_rate - tax_rate)
    cash_end = (capital - cost) + proceeds
    equity   = (capital - cost) + close / close.iloc[0] * (buy_price * qty)
    equity.iloc[-1] = cash_end
    orders_df = pd.DataFrame([
        {"date": df.index[0],  "ticker": ticker, "action": "BUY",  "price": buy_price},
        {"date": df.index[-1], "ticker": ticker, "action": "SELL", "price": sell_price},
    ])
    m = _calc_metrics(equity, orders_df, capital)
    m["전략"] = "Buy & Hold"
    return m


# ── 전략 실행 헬퍼 ────────────────────────────────────────────────────────

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
    m["전략"]      = strategy.name()
    m["_equity"]   = equity
    m["_orders_df"] = orders_df
    return m


# ── Walk-Forward Analysis ─────────────────────────────────────────────────

def walk_forward_test(
    ohlcv:       pd.DataFrame,
    ticker:      str,
    capital:     float = 10_000_000,
    train_years: int   = 2,
    test_years:  int   = 1,
    commission_rate: float = COMMISSION_RATE,
    tax_rate:        float = TAX_RATE,
    slippage_rate:   float = SLIPPAGE_BASE,
    market_df:       Optional[pd.DataFrame] = None,
    atr_stop_mult:   float = 2.0,
) -> dict:
    """
    Walk-Forward Analysis (WFA):
      - Train window (warmup): train_years — 지표 워밍업용
      - Test  window (OOS)  : test_years  — 실제 성과 측정
      - 매 test_years마다 1 step 롤링

    Args:
        ohlcv:       종목 OHLCV DataFrame
        ticker:      종목코드
        train_years: 워밍업 기간 (년)
        test_years:  OOS 테스트 기간 (년)
        market_df:   시장 레짐 필터용 데이터 (선택)

    Returns:
        {
          "periods":   list[dict],      # 기간별 OOS 성과
          "oos_equity": pd.Series,      # 이어붙인 OOS 자산 곡선
          "summary":   dict,            # 집계 통계
          "error":     str (실패 시),
        }
    """
    from backtest.strategies.moving_average_v2 import MovingAverageCrossV2Strategy

    TRADING_DAYS_PER_YEAR = 252
    train_days  = train_years * TRADING_DAYS_PER_YEAR
    test_days   = test_years  * TRADING_DAYS_PER_YEAR
    min_bars    = train_days + test_days

    if len(ohlcv) < min_bars:
        return {"error": f"데이터 부족 ({len(ohlcv)}거래일, 최소 {min_bars}일 필요)"}

    periods:     list[dict]     = []
    oos_parts:   list[pd.Series] = []

    step = 0
    while True:
        # 각 OOS 창: 워밍업(train_days) 포함 전체 슬라이스
        context_start = step * test_days
        test_start    = context_start + train_days
        test_end      = test_start + test_days

        if test_end > len(ohlcv):
            break

        # 전체 슬라이스 (워밍업 포함)
        full_slice = ohlcv.iloc[context_start:test_end]
        # OOS 구간 날짜
        oos_start_date = ohlcv.index[test_start]

        strategy = MovingAverageCrossV2Strategy(
            ticker,
            short_window=5, long_window=20, trend_window=60,
            invest_pct=0.5,
            market_df=market_df,
            atr_stop_mult=atr_stop_mult,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            engine = BacktestEngine_lazy(full_slice, ticker, capital,
                                         commission_rate, tax_rate, slippage_rate, strategy)

        if engine is None or engine.results is None:
            step += 1
            continue

        # OOS 구간만 추출
        oos_equity = engine.results["total_value"][engine.results.index >= oos_start_date]
        if oos_equity.empty:
            step += 1
            continue

        orders_df = engine.get_orders()
        # OOS 구간 주문만 필터
        if not orders_df.empty and "date" in orders_df.columns:
            orders_df = orders_df[orders_df["date"] >= oos_start_date]

        m = _calc_metrics(oos_equity, orders_df, float(oos_equity.iloc[0]))
        if not m:
            step += 1
            continue

        periods.append({
            "기간":     f"{oos_start_date.date()}~{ohlcv.index[test_end-1].date()}",
            "CAGR(%)":  m.get("CAGR(%)", 0),
            "MDD(%)":   m.get("MDD(%)", 0),
            "샤프비율":  m.get("샤프비율", 0),
            "Profit Factor": m.get("Profit Factor", 0),
            "승률(%)":  m.get("승률(%)", 0),
            "거래횟수":  m.get("거래횟수", 0),
        })
        oos_parts.append(oos_equity / oos_equity.iloc[0])
        step += 1

    if not periods:
        return {"error": "유효한 OOS 기간 없음 (데이터 부족)"}

    # OOS 자산 곡선 이어붙이기 (각 구간을 정규화=100 후 체인)
    oos_equity_stitched = _stitch_equity(oos_parts, capital)

    cagrs    = [p.get("CAGR(%)", 0) or 0 for p in periods]
    sharpes  = [p.get("샤프비율", 0) or 0 for p in periods]
    pf_list  = [p.get("Profit Factor", 0) or 0 for p in periods]
    positive = sum(1 for c in cagrs if c > 0)

    summary = {
        "검증 기간 수":     len(periods),
        "수익 기간 비율(%)": round(positive / len(periods) * 100, 1),
        "평균 CAGR(%)":    round(float(np.mean(cagrs)), 2),
        "평균 샤프비율":    round(float(np.mean(sharpes)), 2),
        "평균 PF":         round(float(np.mean(pf_list)), 2),
    }

    return {
        "periods":    periods,
        "oos_equity": oos_equity_stitched,
        "summary":    summary,
    }


def _stitch_equity(parts: list[pd.Series], base_capital: float) -> pd.Series:
    """OOS 구간별 자산 곡선을 이어붙임"""
    if not parts:
        return pd.Series(dtype=float)
    result = []
    current_value = base_capital
    for part in parts:
        scaled = part * current_value
        result.append(scaled)
        current_value = float(scaled.iloc[-1])
    return pd.concat(result)


def BacktestEngine_lazy(df, ticker, capital, commission_rate, tax_rate, slippage_rate, strategy):
    """내부 헬퍼: 엔진 실행 후 반환"""
    from backtest.engine import BacktestEngine
    import contextlib, io
    try:
        engine = BacktestEngine(
            data={ticker: df},
            initial_capital=capital,
            commission_rate=commission_rate,
            tax_rate=tax_rate,
            slippage_rate=slippage_rate,
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            engine.run(strategy)
            engine.report()
        return engine
    except Exception:
        return None


# ── 로버스트니스 체크리스트 ───────────────────────────────────────────────

def robustness_check(v2: dict, bh: dict,
                     multi_df: pd.DataFrame,
                     slip_df: pd.DataFrame,
                     base_slippage: float) -> list[dict]:
    """전략 로버스트니스 7개 항목 평가"""
    results = []

    def _chk(label, criterion, ok, val=""):
        results.append({
            "항목":  label,
            "기준":  criterion,
            "결과":  ("✅ " + str(val)) if ok else ("❌ " + str(val)),
        })

    bh_cagr = bh.get("CAGR(%)", 0) or 0
    v2_cagr = v2.get("CAGR(%)", 0) or 0
    _chk("CAGR > B&H + 2%p",
         f"B&H CAGR = {bh_cagr:+.1f}%",
         v2_cagr >= bh_cagr + 2,
         f"{v2_cagr:+.1f}%")

    bh_mdd = bh.get("MDD(%)", -999) or -999
    v2_mdd = v2.get("MDD(%)", -999) or -999
    _chk("MDD ≤ B&H MDD × 1.1",
         f"B&H MDD = {bh_mdd:.1f}%",
         v2_mdd >= bh_mdd * 1.1,
         f"{v2_mdd:.1f}%")

    v2_tpy = v2.get("연간거래횟수", 999) or 999
    _chk("연간 거래 ≤ 24회",
         "과도한 매매 = 비용 누적",
         v2_tpy <= 24,
         f"{v2_tpy:.0f}회")

    # 슬리피지 2배에서도 CAGR > 0
    if not slip_df.empty and "슬리피지" in slip_df.columns:
        slip2x = slip_df[slip_df["슬리피지"].str.startswith("×2")]
        v2_s2v = float(slip2x["CAGR(%)"].values[0]) if len(slip2x) else 0
        _chk("슬리피지 2배 후 CAGR > 0", "비용 내성 확인", v2_s2v > 0, f"{v2_s2v:+.1f}%")

    # Profit Factor > 1.2
    v2_pf = v2.get("Profit Factor", 0) or 0
    _chk("Profit Factor > 1.2",
         "총이익 / 총손실",
         v2_pf > 1.2,
         f"{v2_pf:.2f}")

    # 회복계수 > 1.0 (손실 1에 수익 1 이상)
    v2_rf = v2.get("회복계수", 0) or 0
    _chk("회복계수 > 1.0",
         "총수익률 / |MDD| (손실 복구력)",
         v2_rf > 1.0,
         f"{v2_rf:.2f}")

    # 오메가비율 > 1.0 (기대수익이 기대손실보다 큼)
    v2_om = v2.get("오메가비율", 0) or 0
    _chk("오메가비율 > 1.0",
         "가중수익 / 가중손실",
         v2_om > 1.0,
         f"{v2_om:.2f}")

    return results
