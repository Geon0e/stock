"""
KOSPI 200 전 종목 자동 승률 개선 루프

각 종목마다 meta_optimize 사이클을 돌리고,
사이클 완료 시 strategies/{ticker}_v{N}.json 저장 + git 커밋.

실행:
    python meta_optimize_kospi200.py
    python meta_optimize_kospi200.py --target 65 --iter 150 --cycles 3
    python meta_optimize_kospi200.py --resume          # 이미 완료된 종목 건너뜀
"""

import argparse
import os
import sys
import copy
import time
import json

sys.path.insert(0, os.path.dirname(__file__))

from meta_optimize import ROUNDS
from backtest.strategy_export import save_strategy


def already_done(ticker: str, project_root: str, min_version: int = 1) -> bool:
    """해당 종목의 전략 파일이 min_version 이상 존재하면 True"""
    strategies_dir = os.path.join(project_root, "strategies")
    if not os.path.isdir(strategies_dir):
        return False
    files = [
        f for f in os.listdir(strategies_dir)
        if f.startswith(f"{ticker}_v") and f.endswith(".json")
    ]
    return len(files) >= min_version


def run_single_ticker(df, ticker, target_win_rate, iter_per_round, min_trades,
                      num_cycles, start_date, end_date, project_root):
    """단일 종목에 대해 num_cycles 사이클 실행"""
    from backtest.optimizer import StrategyOptimizer, PARAM_SPACE_V2, PARAM_SPACE_BREAKOUT
    from backtest.reasoner import reason

    best_wr       = 0.0
    best_params   = {}
    best_strategy = ""
    best_metrics  = {}
    global_round  = 0
    history       = []
    all_results   = []   # 전체 사이클에 걸친 유효 결과 누적
    extra_rounds  = []   # reasoner가 생성한 추가 라운드

    print(f"\n{'#'*60}")
    print(f"  [{ticker}]  목표: {target_win_rate}%  |  사이클: {num_cycles}회")
    print(f"{'#'*60}")

    for cycle in range(1, num_cycles + 1):
        print(f"\n  === Cycle {cycle}/{num_cycles} ===  (현재 최고: {best_wr:.1f}%)")

        # 기본 9 라운드 + 이전 사이클 추론으로 생성된 추가 라운드
        rounds_this_cycle = list(ROUNDS) + extra_rounds

        for rnd_cfg in rounds_this_cycle:
            global_round += 1
            name       = rnd_cfg["name"]
            hypothesis = rnd_cfg.get("hypothesis", "")
            b_space    = rnd_cfg.get("breakout_space", PARAM_SPACE_BREAKOUT)
            v2_space   = rnd_cfg.get("v2_space", PARAM_SPACE_V2)

            print(f"\n  [R{global_round}] {name}")
            print(f"       가설: {hypothesis}")

            import backtest.optimizer as opt_mod
            orig_b  = copy.deepcopy(opt_mod.PARAM_SPACE_BREAKOUT)
            orig_v2 = copy.deepcopy(opt_mod.PARAM_SPACE_V2)
            if b_space is not None:
                opt_mod.PARAM_SPACE_BREAKOUT = b_space
            if v2_space is not None:
                opt_mod.PARAM_SPACE_V2 = v2_space

            opt = StrategyOptimizer(
                df=df, ticker=ticker,
                target_win_rate=target_win_rate,
                max_iter=iter_per_round,
                min_trades=min_trades,
                seed=None,
            )
            opt.run()

            opt_mod.PARAM_SPACE_BREAKOUT = orig_b
            opt_mod.PARAM_SPACE_V2       = orig_v2

            # 이번 라운드 결과 누적
            all_results.extend(opt.results)

            round_wr     = opt.best.get("승률(%)", 0) or 0
            round_params = opt.best.get("_params", {})
            round_strat  = opt.best.get("_strategy", "")

            if round_wr > best_wr:
                best_wr       = round_wr
                best_params   = round_params
                best_strategy = round_strat
                best_metrics  = opt.best
                print(f"       [+] 신기록 {best_wr:.1f}%  전략={best_strategy}")
            else:
                print(f"       [-] {round_wr:.1f}%  (최고: {best_wr:.1f}%)")

            history.append((global_round, name, round_wr))

            if best_wr >= target_win_rate:
                target_win_rate += 2.0
                print(f"       [*] 목표 달성 → 새 목표 {target_win_rate:.0f}%")

            time.sleep(0.05)

        # ── 사이클 완료: reasoner 추론 ─────────────────────────────
        print(f"\n  --- Cycle {cycle} 완료  최고 승률: {best_wr:.1f}% ---")

        if all_results and cycle < num_cycles:
            analysis = reason(
                all_results=all_results,
                best_strategy=best_strategy,
                best_params=best_params,
                best_wr=best_wr,
                round_history=history,
            )
            print(f"\n  [추론] {analysis['hypothesis']}")
            for ins in analysis["insights"]:
                print(f"         - {ins}")

            # 다음 사이클의 추가 라운드로 등록
            extra_rounds = []
            if analysis["breakout_space"]:
                extra_rounds.append({
                    "name": f"추론 기반 집중 탐색 (Cycle {cycle})",
                    "hypothesis": analysis["hypothesis"],
                    "breakout_space": analysis["breakout_space"],
                    "v2_space": analysis["v2_space"],
                })

        # ── JSON 저장 + git 커밋 ────────────────────────────────────
        if best_metrics:
            try:
                saved = save_strategy(
                    ticker=ticker,
                    cycle=cycle,
                    strategy_type=best_strategy,
                    params=best_params,
                    metrics=best_metrics,
                    backtest_period={"start": start_date, "end": end_date},
                    project_root=project_root,
                )
                print(f"  저장: {os.path.basename(saved)}")
            except Exception as e:
                print(f"  저장 실패: {e}")
        else:
            print(f"  유효한 전략 없음 - 저장 건너뜀")

    return best_wr, best_strategy, best_params


def main():
    parser = argparse.ArgumentParser(description="KOSPI 200 전 종목 전략 자동 최적화")
    parser.add_argument("--start",   default="2018-01-01")
    parser.add_argument("--end",     default="2024-12-31")
    parser.add_argument("--target",  type=float, default=60.0,  help="목표 승률 (%)")
    parser.add_argument("--iter",    type=int,   default=150,   help="라운드당 반복 횟수")
    parser.add_argument("--cycles",  type=int,   default=1,     help="종목당 사이클 수")
    parser.add_argument("--trades",  type=int,   default=10,    help="최소 유효 거래 횟수")
    parser.add_argument("--resume",  action="store_true",       help="이미 완료된 종목 건너뜀")
    parser.add_argument("--limit",   type=int,   default=0,     help="처리할 최대 종목 수 (0=전체)")
    args = parser.parse_args()

    project_root = os.path.dirname(__file__)

    # ── KOSPI 200 종목 목록 ────────────────────────────────────────────
    print("KOSPI 200 종목 목록 조회 중...")
    from data.fetcher import get_kospi200_tickers, get_ohlcv
    tickers_df = get_kospi200_tickers(use_cache=True)
    print(f"총 {len(tickers_df)}개 종목\n")

    if args.limit > 0:
        tickers_df = tickers_df.head(args.limit)

    # ── 진행 상황 추적 ─────────────────────────────────────────────────
    summary = []  # [(ticker, name, win_rate, strategy)]
    skipped  = []
    failed   = []

    total = len(tickers_df)
    for idx, row in enumerate(tickers_df.itertuples(), 1):
        ticker = row.Code
        name   = row.Name

        print(f"\n{'='*60}")
        print(f"  [{idx}/{total}] {name} ({ticker})")
        print(f"{'='*60}")

        # resume 모드: 이미 전략 파일 있으면 건너뜀
        if args.resume and already_done(ticker, project_root, min_version=args.cycles):
            print(f"  → 건너뜀 (이미 완료)")
            skipped.append(ticker)
            continue

        # 데이터 수집
        try:
            df = get_ohlcv(ticker, args.start, args.end)
        except Exception as e:
            print(f"  데이터 수집 실패: {e}")
            failed.append((ticker, name))
            continue

        if df is None or df.empty or len(df) < 100:
            print(f"  데이터 부족 ({0 if df is None else len(df)}봉) - 건너뜀")
            failed.append((ticker, name))
            continue

        print(f"  데이터: {len(df)}봉  ({df.index[0].date()} ~ {df.index[-1].date()})")

        # 최적화 실행
        try:
            wr, strat, params = run_single_ticker(
                df=df,
                ticker=ticker,
                target_win_rate=args.target,
                iter_per_round=args.iter,
                min_trades=args.trades,
                num_cycles=args.cycles,
                start_date=args.start,
                end_date=args.end,
                project_root=project_root,
            )
            summary.append((ticker, name, wr, strat))
        except KeyboardInterrupt:
            print("\n\n사용자가 중단했습니다.")
            break
        except Exception as e:
            print(f"  최적화 실패: {e}")
            failed.append((ticker, name))
            continue

    # ── 최종 요약 ──────────────────────────────────────────────────────
    print(f"\n\n{'#'*60}")
    print(f"  KOSPI 200 최적화 완료")
    print(f"{'#'*60}")
    print(f"  처리: {len(summary)}개  |  건너뜀: {len(skipped)}개  |  실패: {len(failed)}개\n")

    if summary:
        summary.sort(key=lambda x: x[2], reverse=True)
        print(f"  ▶ 승률 상위 20개")
        print(f"  {'티커':>8}  {'종목명':<15}  {'승률':>6}  {'전략'}")
        print(f"  {'-'*50}")
        for ticker, name, wr, strat in summary[:20]:
            print(f"  {ticker:>8}  {name:<15}  {wr:>5.1f}%  {strat}")

        # 요약 JSON 저장
        summary_path = os.path.join(project_root, "strategies", "kospi200_summary.json")
        os.makedirs(os.path.dirname(summary_path), exist_ok=True)
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(
                [{"ticker": t, "name": n, "win_rate": w, "strategy": s}
                 for t, n, w, s in summary],
                f, ensure_ascii=False, indent=2
            )
        print(f"\n  요약 저장: strategies/kospi200_summary.json")

    if failed:
        print(f"\n  실패 종목: {[t for t, _ in failed]}")


if __name__ == "__main__":
    main()
