"""
백테스팅 파라미터 자동 최적화

방법: Random Search (Phase 1) → Local Refinement (Phase 2)
목표: 승률 극대화
제약: Profit Factor > 1.0, Expectancy > 0, 거래횟수 >= 10
"""

from __future__ import annotations

import contextlib
import io
import random
import time
from typing import Optional

import numpy as np
import pandas as pd

# ── 파라미터 탐색 공간 ─────────────────────────────────────────────────────

PARAM_SPACE_V2 = {
    "short_window":  [3, 5, 7, 10],
    "long_window":   [15, 20, 25, 30],
    "trend_window":  [40, 50, 60, 80, 100, 120],
    "rsi_entry_max": [55, 60, 65, 70, 75],
    "trail_mult":    [0.0, 2.0, 2.5, 3.0, 3.5, 4.0],
    "invest_pct":    [0.3, 0.4, 0.5, 0.6, 0.7],
}

PARAM_SPACE_BREAKOUT = {
    "entry_window":       [10, 15, 20, 25, 30, 40, 50],
    "exit_window":        [5, 7, 10, 15, 20],
    "trail_mult":         [2.0, 2.5, 3.0, 3.5, 4.0],
    "profit_target_mult": [0.0, 1.5, 2.0, 2.5, 3.0],   # 이익 목표 ATR 배수
    "volume_ratio":       [1.0, 1.2, 1.5, 2.0],
    "invest_pct":         [0.3, 0.4, 0.5, 0.6],
}


# ── 단일 백테스트 실행 헬퍼 ────────────────────────────────────────────────

def _run_once(df: pd.DataFrame, ticker: str, strategy, capital: float) -> dict:
    """백테스트 1회 실행 → 성과지표 반환 (실패 시 빈 dict)"""
    from backtest.engine import BacktestEngine
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            engine = BacktestEngine(data={ticker: df}, initial_capital=capital)
            engine.run(strategy)
            metrics = engine.report()
        return metrics
    except Exception:
        return {}


def _sample_v2_params(space: dict) -> dict:
    """V2 파라미터 샘플링 (제약: short < long < trend)"""
    while True:
        p = {k: random.choice(v) for k, v in space.items()}
        if p["short_window"] < p["long_window"] < p["trend_window"]:
            return p


def _sample_breakout_params(space: dict) -> dict:
    """Breakout 파라미터 샘플링 (제약: exit <= entry)"""
    while True:
        p = {k: random.choice(v) for k, v in space.items()}
        if p["exit_window"] <= p["entry_window"]:
            return p


def _is_valid(m: dict, min_trades: int = 10) -> bool:
    """제약 조건 검사"""
    if not m:
        return False
    if m.get("총거래횟수", 0) < min_trades:
        return False
    if (m.get("Profit Factor") or 0) <= 1.0:
        return False
    if (m.get("Expectancy(%)") or 0) <= 0:
        return False
    return True


def _perturb(params: dict, space: dict) -> dict:
    """현재 파라미터에서 한 값만 바꾸는 Local Search"""
    new_p = params.copy()
    key = random.choice(list(space.keys()))
    new_p[key] = random.choice(space[key])
    return new_p


# ── 핵심 최적화 클래스 ─────────────────────────────────────────────────────

class StrategyOptimizer:
    """
    파라미터 자동 최적화 (Random Search + Local Refinement)

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV 데이터 (Open/High/Low/Close/Volume)
    ticker : str
    capital : float
    target_win_rate : float
        목표 승률 (%) — 이 값 도달 시 조기 종료
    max_iter : int
        최대 반복 횟수
    min_trades : int
        최소 유효 거래 횟수 (이하는 무효 처리)
    seed : int
        재현성을 위한 랜덤 시드 (None = 매번 다른 결과)
    """

    def __init__(
        self,
        df: pd.DataFrame,
        ticker: str,
        capital: float = 10_000_000,
        target_win_rate: float = 60.0,
        max_iter: int = 500,
        min_trades: int = 10,
        seed: Optional[int] = None,
    ):
        self.df              = df
        self.ticker          = ticker
        self.capital         = capital
        self.target_win_rate = target_win_rate
        self.max_iter        = max_iter
        self.min_trades      = min_trades

        if seed is not None:
            random.seed(seed)
            np.random.seed(seed)

        self.results: list[dict] = []   # 유효한 결과만 누적
        self.best: dict = {}
        self._iter = 0
        self._seen_params: set = set()  # 중복 탐색 방지

    # ── 내부 실행 ────────────────────────────────────────────────────────

    def _params_key(self, strategy_type: str, params: dict) -> str:
        return strategy_type + "|" + str(sorted(params.items()))

    def _eval_v2(self, params: dict) -> dict:
        key = self._params_key("V2", params)
        if key in self._seen_params:
            return {}
        self._seen_params.add(key)
        from backtest.strategies import MovingAverageCrossV2Strategy
        strategy = MovingAverageCrossV2Strategy(self.ticker, **params)
        m = _run_once(self.df, self.ticker, strategy, self.capital)
        if _is_valid(m, self.min_trades):
            m["_params"] = params.copy()
            m["_strategy"] = "V2"
        return m

    def _eval_breakout(self, params: dict) -> dict:
        key = self._params_key("Breakout", params)
        if key in self._seen_params:
            return {}
        self._seen_params.add(key)
        from backtest.strategies import BreakoutStrategy
        strategy = BreakoutStrategy(self.ticker, **params)
        m = _run_once(self.df, self.ticker, strategy, self.capital)
        if _is_valid(m, self.min_trades):
            m["_params"] = params.copy()
            m["_strategy"] = "Breakout"
        return m

    def _record(self, m: dict) -> None:
        if not _is_valid(m, self.min_trades):
            return
        self.results.append(m)
        wr = m.get("승률(%)", 0) or 0
        best_wr = self.best.get("승률(%)", 0) or 0
        if wr > best_wr:
            self.best = m
            self._print_new_best(m)

    def _print_new_best(self, m: dict) -> None:
        wr = m.get("승률(%)", 0)
        pf = m.get("Profit Factor", 0)
        cagr = m.get("연환산수익률(CAGR,%)", 0)
        mdd  = m.get("최대낙폭(MDD,%)", 0)
        exp  = m.get("Expectancy(%)", 0)
        strategy = m.get("_strategy", "?")
        params = m.get("_params", {})
        print(
            f"  ★ 신기록 [{self._iter:>4}회] {strategy}  "
            f"승률={wr:.1f}%  PF={pf:.2f}  CAGR={cagr:+.1f}%  MDD={mdd:.1f}%  E={exp:+.2f}%"
        )
        print(f"         파라미터: {params}")

    # ── Phase 1: Random Search ────────────────────────────────────────────

    def _phase1(self, n_random: int) -> None:
        print(f"\n[Phase 1] Random Search ({n_random}회) ...")
        for i in range(n_random):
            self._iter += 1
            # V2 / Breakout 교대로 탐색
            if i % 2 == 0:
                p = _sample_v2_params(PARAM_SPACE_V2)
                m = self._eval_v2(p)
            else:
                p = _sample_breakout_params(PARAM_SPACE_BREAKOUT)
                m = self._eval_breakout(p)
            self._record(m)

            # 진행 보고 (50회마다)
            if self._iter % 50 == 0:
                best_wr = self.best.get("승률(%)", 0) or 0
                valid_n = len(self.results)
                print(f"  [{self._iter:>4}회] 현재 최고 승률={best_wr:.1f}%  유효={valid_n}건")

            # 목표 달성 시 조기 종료
            if (self.best.get("승률(%)", 0) or 0) >= self.target_win_rate:
                return

    # ── Phase 2: Local Refinement ─────────────────────────────────────────

    def _phase2(self, n_refine: int) -> None:
        if not self.results:
            return
        print(f"\n[Phase 2] Local Refinement ({n_refine}회) — 상위 후보 주변 집중 탐색 ...")

        # 상위 5개 후보 추출
        top5 = sorted(self.results, key=lambda x: x.get("승률(%)", 0), reverse=True)[:5]

        for i in range(n_refine):
            self._iter += 1
            base = random.choice(top5)
            strategy_type = base.get("_strategy", "V2")
            base_params   = base.get("_params", {})

            if strategy_type == "V2":
                # 한 파라미터만 변경
                candidate = _perturb(base_params, PARAM_SPACE_V2)
                # short < long < trend 제약 재확인
                if not (candidate.get("short_window", 0) < candidate.get("long_window", 0) < candidate.get("trend_window", 0)):
                    continue
                m = self._eval_v2(candidate)
            else:
                candidate = _perturb(base_params, PARAM_SPACE_BREAKOUT)
                if not (candidate.get("exit_window", 0) <= candidate.get("entry_window", 0)):
                    continue
                m = self._eval_breakout(candidate)

            self._record(m)

            if self._iter % 50 == 0:
                best_wr = self.best.get("승률(%)", 0) or 0
                print(f"  [{self._iter:>4}회] 현재 최고 승률={best_wr:.1f}%  총={len(self.results)}건")

            if (self.best.get("승률(%)", 0) or 0) >= self.target_win_rate:
                return

    # ── 메인 루프 ────────────────────────────────────────────────────────

    def run(self) -> "StrategyOptimizer":
        """목표 승률 도달 또는 max_iter 소진까지 자동 최적화"""
        t0 = time.time()
        print("=" * 60)
        print(f"자동 최적화 시작")
        print(f"  종목: {self.ticker}  |  데이터: {len(self.df)}봉")
        print(f"  목표 승률: {self.target_win_rate}%  |  최대 반복: {self.max_iter}회")
        print(f"  제약: PF > 1.0, Expectancy > 0, 거래 >= {self.min_trades}회")
        print("=" * 60)

        phase1_n = int(self.max_iter * 0.7)
        phase2_n = self.max_iter - phase1_n

        self._phase1(phase1_n)

        if (self.best.get("승률(%)", 0) or 0) < self.target_win_rate:
            self._phase2(phase2_n)

        elapsed = time.time() - t0
        self._print_summary(elapsed)
        return self

    # ── 결과 조회 ────────────────────────────────────────────────────────

    def top_results(self, n: int = 10) -> pd.DataFrame:
        """상위 N개 결과를 DataFrame으로 반환 (중복 파라미터 제거)"""
        if not self.results:
            return pd.DataFrame()
        cols = ["_strategy", "승률(%)", "Profit Factor", "연환산수익률(CAGR,%)",
                "최대낙폭(MDD,%)", "Expectancy(%)", "R배수(평균승/패)",
                "오메가비율", "회복계수", "총거래횟수", "_params"]
        rows = []
        seen = set()
        # 승률 내림차순 정렬 후 중복 제거
        sorted_results = sorted(self.results, key=lambda x: x.get("승률(%)", 0), reverse=True)
        for m in sorted_results:
            key = self._params_key(m.get("_strategy", ""), m.get("_params", {}))
            if key in seen:
                continue
            seen.add(key)
            row = {c: m.get(c) for c in cols}
            rows.append(row)
            if len(rows) >= n:
                break
        return pd.DataFrame(rows).reset_index(drop=True)

    def _print_summary(self, elapsed: float) -> None:
        print("\n" + "=" * 60)
        print("최적화 완료")
        print(f"  총 반복: {self._iter}회  |  유효 결과: {len(self.results)}건  |  소요: {elapsed:.1f}초")

        best_wr = self.best.get("승률(%)", 0) or 0
        if self.best:
            achieved = "✅ 목표 달성!" if best_wr >= self.target_win_rate else "❌ 목표 미달"
            print(f"  {achieved}  최고 승률: {best_wr:.1f}% (목표 {self.target_win_rate}%)")
            print(f"  최적 전략: {self.best.get('_strategy')}  파라미터: {self.best.get('_params')}")
        else:
            print("  ⚠️  유효한 결과 없음 (데이터 부족 또는 제약 조건 과도)")
        print("=" * 60)


# ── 편의 함수 ─────────────────────────────────────────────────────────────

def optimize(
    df: pd.DataFrame,
    ticker: str,
    capital: float = 10_000_000,
    target_win_rate: float = 60.0,
    max_iter: int = 500,
    min_trades: int = 10,
    seed: Optional[int] = None,
    top_n: int = 10,
) -> tuple[dict, pd.DataFrame]:
    """
    한 줄로 최적화 실행

    Returns
    -------
    best_params : dict
        최고 승률 파라미터 {"_strategy": ..., "_params": {...}, ...}
    top_df : pd.DataFrame
        상위 top_n 결과 테이블
    """
    opt = StrategyOptimizer(
        df=df, ticker=ticker, capital=capital,
        target_win_rate=target_win_rate, max_iter=max_iter,
        min_trades=min_trades, seed=seed,
    )
    opt.run()
    return opt.best, opt.top_results(top_n)
