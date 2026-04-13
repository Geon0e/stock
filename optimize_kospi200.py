"""
KOSPI 200 유니버설 전략 최적화

전 종목에 동일한 파라미터를 적용했을 때
합산 승률이 가장 높은 전략을 찾는다.

평가 방식:
  - 전 종목 백테스팅 후 총 거래 합산
  - 전체 승률 = 전 종목 총 승리 거래 / 전 종목 총 거래
  - 최소 50거래 이상, 최소 20개 종목 이상 적용 가능해야 유효

실행:
    python optimize_kospi200.py
    python optimize_kospi200.py --iter 200 --cycles 3
    python optimize_kospi200.py --start 2018-01-01 --end 2026-03-31
"""

import argparse
import contextlib
import copy
import io
import os
import random
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

from meta_optimize import ROUNDS
from backtest.optimizer import (
    PARAM_SPACE_BREAKOUT, PARAM_SPACE_V2,
    _sample_breakout_params, _sample_v2_params, _perturb,
)
from backtest.reasoner import reason
from backtest.strategy_export import save_strategy_md


# ── 코스피200 전체 평가 함수 ───────────────────────────────────────────────

def _eval_universal(strategy_type: str, params: dict,
                    all_data: list[tuple], capital: float,
                    min_trades_per_stock: int = 3) -> dict:
    """
    전 종목에 동일 파라미터 적용 -> 합산 지표 반환.
    all_data: [(ticker, name, df), ...]
    """
    from backtest.engine import BacktestEngine

    total_trades = 0
    total_wins   = 0
    total_pf_num = 0.0   # profit factor 분자 합
    total_pf_den = 0.0   # profit factor 분모 합
    cagr_list    = []
    mdd_list     = []
    per_stock    = []

    for ticker, name, df in all_data:
        try:
            if strategy_type == "Breakout":
                from backtest.strategies import BreakoutStrategy
                strategy = BreakoutStrategy(ticker, **params)
            else:
                from backtest.strategies import MovingAverageCrossV2Strategy
                strategy = MovingAverageCrossV2Strategy(ticker, **params)

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                engine = BacktestEngine(data={ticker: df}, initial_capital=capital)
                engine.run(strategy)
                m = engine.report()

            trades = m.get("총거래횟수", 0) or 0
            if trades < min_trades_per_stock:
                continue

            wr    = m.get("승률(%)", 0) or 0
            wins  = round(trades * wr / 100)
            pf    = m.get("Profit Factor", 1.0) or 1.0
            cagr  = m.get("연환산수익률(CAGR,%)", 0) or 0
            mdd   = m.get("최대낙폭(MDD,%)", 0) or 0

            total_trades += trades
            total_wins   += wins
            # profit factor 합산: PF = sum(wins_pnl) / sum(loss_pnl)
            # 근사: wins * avg_win = PF * losses * avg_loss -> 그냥 PF 평균으로 근사
            cagr_list.append(cagr)
            mdd_list.append(mdd)

            per_stock.append({
                "ticker":        ticker,
                "name":          name,
                "win_rate":      wr,
                "total_trades":  trades,
                "profit_factor": pf,
                "cagr":          cagr,
                "mdd":           mdd,
            })

        except Exception:
            continue

    if total_trades < 100 or len(per_stock) < 80:
        return {}

    aggregate_wr = total_wins / total_trades * 100
    avg_cagr     = sum(cagr_list) / len(cagr_list) if cagr_list else 0
    avg_mdd      = sum(mdd_list)  / len(mdd_list)  if mdd_list  else 0

    # profit factor 근사 (승률과 평균 수익/손실 비율로)
    # 간단히: per_stock PF 중앙값 사용
    pf_list = [r["profit_factor"] for r in per_stock]
    median_pf = sorted(pf_list)[len(pf_list)//2] if pf_list else 1.0

    return {
        "win_rate":       aggregate_wr,
        "profit_factor":  median_pf,
        "avg_cagr":       avg_cagr,
        "avg_mdd":        avg_mdd,
        "total_trades":   total_trades,
        "covered_stocks": len(per_stock),
        "per_stock":      per_stock,
        "_strategy":      strategy_type,
        "_params":        params.copy(),
    }


# ── 최적화 루프 ──────────────────────────────────────────────────────────────

def run_optimization(all_data, capital, target_wr, max_iter,
                     b_space=None, v2_space=None):
    """
    Random Search (70%) + Local Refinement (30%).
    Returns: best result dict
    """
    if b_space is None:
        b_space = PARAM_SPACE_BREAKOUT
    if v2_space is None:
        v2_space = PARAM_SPACE_V2

    results  = []
    best     = {}
    best_wr  = 0.0
    seen     = set()
    n_random = int(max_iter * 0.7)
    n_refine = max_iter - n_random

    def key(st, p):
        return st + "|" + str(sorted(p.items()))

    def record(m):
        nonlocal best, best_wr
        if not m:
            return
        results.append(m)
        wr = m.get("win_rate", 0)
        if wr > best_wr:
            best_wr = wr
            best    = m
            print(
                f"  [+] {len(results):>4}회  {m['_strategy']:<10}"
                f"  합산승률={wr:.1f}%  종목={m['covered_stocks']}개"
                f"  거래={m['total_trades']}  PF={m['profit_factor']:.2f}"
                f"  CAGR={m['avg_cagr']:+.1f}%"
            )
            print(f"       파라미터: {m['_params']}")

    # Phase 1: Random Search
    print(f"\n[Phase 1] Random Search ({n_random}회) ...")
    for i in range(n_random):
        if i % 2 == 0:
            p = _sample_breakout_params(b_space)
            k = key("Breakout", p)
            if k in seen:
                continue
            seen.add(k)
            m = _eval_universal("Breakout", p, all_data, capital)
        else:
            p = _sample_v2_params(v2_space)
            k = key("V2", p)
            if k in seen:
                continue
            seen.add(k)
            m = _eval_universal("V2", p, all_data, capital)
        record(m)
        if best_wr >= target_wr:
            print(f"  목표 {target_wr}% 달성 - 조기 종료")
            break

    # Phase 2: Local Refinement
    if results and n_refine > 0:
        print(f"\n[Phase 2] Local Refinement ({n_refine}회) ...")
        top5 = sorted(results, key=lambda x: x.get("win_rate", 0), reverse=True)[:5]
        for i in range(n_refine):
            base   = top5[i % len(top5)]
            st     = base["_strategy"]
            space  = b_space if st == "Breakout" else v2_space
            p      = _perturb(base["_params"], space)
            k      = key(st, p)
            if k in seen:
                continue
            seen.add(k)
            m = _eval_universal(st, p, all_data, capital)
            record(m)
            if best_wr >= target_wr:
                break

    return best, results


# ── 메인 루프 ────────────────────────────────────────────────────────────────

def _save_round_log(project_root: str, cycle: int, round_num: int,
                    round_name: str, best_round: dict, all_results: list):
    """라운드 결과를 JSON으로 저장 (Claude 추론용)"""
    import json
    log_dir  = os.path.join(project_root, "strategies", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"cycle{cycle}_round{round_num:02d}.json")

    # per_stock 제외하고 상위 10개 결과만 저장 (용량 절약)
    top10 = sorted(all_results, key=lambda x: x.get("win_rate", 0), reverse=True)[:10]
    slim  = [{k: v for k, v in r.items() if k != "per_stock"} for r in top10]

    log = {
        "cycle":       cycle,
        "round":       round_num,
        "round_name":  round_name,
        "best": {k: v for k, v in best_round.items() if k != "per_stock"} if best_round else {},
        "top10":       slim,
    }
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    return log_path


def run_meta_loop(all_data, all_ticker_names, capital,
                  target_wr, iter_per_round, num_cycles,
                  start_date, end_date, project_root):

    best_global   = {}
    best_wr       = 0.0
    all_results   = []
    extra_rounds  = []
    cycle_target  = target_wr
    round_num     = 0

    for cycle in range(1, num_cycles + 1):
        print(f"\n{'='*65}")
        print(f"  Cycle {cycle}/{num_cycles}  현재 최고 합산승률: {best_wr:.1f}%")
        print(f"{'='*65}")

        rounds_this_cycle = list(ROUNDS) + extra_rounds

        for rnd_cfg in rounds_this_cycle:
            round_num += 1
            name       = rnd_cfg["name"]
            hypothesis = rnd_cfg.get("hypothesis", "")
            b_space    = rnd_cfg.get("breakout_space", PARAM_SPACE_BREAKOUT)
            v2_space   = rnd_cfg.get("v2_space",       PARAM_SPACE_V2)

            print(f"\n[R{round_num:02d}] {name}")
            print(f"  가설: {hypothesis}")

            best_round, round_results = run_optimization(
                all_data, capital, cycle_target, iter_per_round,
                b_space=b_space, v2_space=v2_space,
            )
            all_results.extend(round_results)

            # 라운드 결과 로그 저장
            log_path = _save_round_log(
                project_root, cycle, round_num, name, best_round, round_results
            )
            print(f"  [log] {os.path.relpath(log_path, project_root)}")

            round_wr = best_round.get("win_rate", 0)
            if round_wr > best_wr:
                best_wr     = round_wr
                best_global = best_round
                print(f"  [+] 신기록 합산승률 {best_wr:.1f}%")
            else:
                print(f"  [-] {round_wr:.1f}%  (최고: {best_wr:.1f}%)")

            if best_wr >= cycle_target:
                cycle_target += 2.0
                print(f"  [*] 목표 달성 -> 새 목표 {cycle_target:.0f}%")

        # ── 사이클 완료: 추론 + MD 저장 ──────────────────────────────
        print(f"\n{'='*65}")
        print(f"  Cycle {cycle} 완료  합산승률: {best_wr:.1f}%")

        if best_global:
            # 사이클 전체 요약 저장 (Claude 추론용)
            import json
            summary_path = os.path.join(
                project_root, "strategies", "logs", f"cycle{cycle}_summary.json"
            )
            os.makedirs(os.path.dirname(summary_path), exist_ok=True)

            # 라운드별 최고 승률 집계
            round_logs = []
            logs_dir = os.path.join(project_root, "strategies", "logs")
            for f in sorted(os.listdir(logs_dir)):
                if f.startswith(f"cycle{cycle}_round") and f.endswith(".json"):
                    with open(os.path.join(logs_dir, f), encoding="utf-8") as fp:
                        round_logs.append(json.load(fp))

            summary = {
                "cycle":      cycle,
                "best_wr":    best_wr,
                "best_strategy": best_global.get("_strategy"),
                "best_params":   best_global.get("_params"),
                "best_covered":  best_global.get("covered_stocks"),
                "best_trades":   best_global.get("total_trades"),
                "rounds": [
                    {
                        "round":      r["round"],
                        "name":       r["round_name"],
                        "best_wr":    r["best"].get("win_rate", 0) if r.get("best") else 0,
                        "strategy":   r["best"].get("_strategy", "") if r.get("best") else "",
                        "params":     r["best"].get("_params", {}) if r.get("best") else {},
                    }
                    for r in round_logs
                ],
            }
            with open(summary_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
            print(f"\n  [분석용] {os.path.relpath(summary_path, project_root)}")

            # MD 저장 + git 커밋
            try:
                saved = save_strategy_md(
                    strategy_type  = best_global["_strategy"],
                    params         = best_global["_params"],
                    aggregate      = best_global,
                    per_stock      = best_global.get("per_stock", []),
                    backtest_period= {"start": start_date, "end": end_date},
                    project_root   = project_root,
                    cycle          = cycle,
                )
                print(f"  저장: {os.path.basename(saved)}")
            except Exception as e:
                print(f"  저장 실패: {e}")

    return best_global


# ── 진입점 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="KOSPI 200 전 종목 합산 승률 최대화 전략 탐색"
    )
    parser.add_argument("--start",   default="2018-01-01")
    parser.add_argument("--end",     default="2026-03-31")
    parser.add_argument("--capital", type=float, default=10_000_000)
    parser.add_argument("--target",  type=float, default=60.0, help="목표 합산 승률 (%)")
    parser.add_argument("--iter",    type=int,   default=100,  help="라운드당 반복 횟수")
    parser.add_argument("--cycles",  type=int,   default=2,    help="사이클 수")
    args = parser.parse_args()

    project_root = os.path.dirname(__file__)

    # ── 데이터 로드 ────────────────────────────────────────────────
    print("KOSPI 200 종목 목록 조회 중...")
    from data.fetcher import get_kospi200_tickers, get_ohlcv
    tickers_df = get_kospi200_tickers(use_cache=True)
    print(f"총 {len(tickers_df)}개 종목\n")

    print(f"전체 데이터 로드 중 ({args.start} ~ {args.end}) ...")
    all_data = []
    failed   = []
    for row in tickers_df.itertuples():
        try:
            df = get_ohlcv(row.Code, args.start, args.end)
            if df is not None and len(df) >= 100:
                all_data.append((row.Code, row.Name, df))
                print(f"  {row.Code} {row.Name} ({len(df)}봉)", end="\r")
            else:
                failed.append(row.Code)
        except Exception:
            failed.append(row.Code)

    print(f"\n로드 완료: {len(all_data)}개 종목  (실패: {len(failed)}개)\n")

    if len(all_data) < 50:
        print("데이터 부족. 종료.")
        sys.exit(1)

    # ── 최적화 실행 ────────────────────────────────────────────────
    try:
        best = run_meta_loop(
            all_data     = all_data,
            all_ticker_names = [(t, n) for t, n, _ in all_data],
            capital      = args.capital,
            target_wr    = args.target,
            iter_per_round = args.iter,
            num_cycles   = args.cycles,
            start_date   = args.start,
            end_date     = args.end,
            project_root = project_root,
        )
    except KeyboardInterrupt:
        print("\n\n사용자가 중단했습니다.")
        return

    if best:
        print(f"\n최종 결과: 합산승률 {best.get('win_rate',0):.1f}%")
        print(f"전략: {best.get('_strategy')}  파라미터: {best.get('_params')}")


if __name__ == "__main__":
    main()
