"""
승률 자동 개선 무한 루프

Claude가 매 라운드마다 새로운 전략 개선을 자동으로 시도합니다.
각 라운드: 파라미터 공간 변경 → 최적화 → 승률 비교 → 개선 시 유지

실행:
    python meta_optimize.py --ticker 005930
    python meta_optimize.py --ticker AAPL --us
    python meta_optimize.py --ticker 005930 --target 70  # 높은 목표
"""

import argparse
import sys
import os
import time
import copy

sys.path.insert(0, os.path.dirname(__file__))


# ── 라운드별 파라미터 공간 정의 ───────────────────────────────────────────
# 각 라운드는 이전 라운드보다 더 정교한 파라미터 공간을 탐색

ROUNDS = [
    # ── Cycle13 확정 (2018-2024, 80종목+ 기준) ───────────────────
    # best: entry=520, exit=175, trail=19.0, profit=0.3, volume=1.3
    #       rsi=0, adx=0, trend=0 -> 86.0%, 150종목, 1773거래
    # 핵심 발견: trail=19.0 (C12의 15.0보다 훨씬 큰 값이 최적)
    # C14: 2018-2026 확장 데이터로 재검증 + trail 고범위(17~25) 정밀 탐색

    # ── Round 1: trail 고범위 정밀 (16~26) ──────────────────────
    {
        "name": "ATR 손절폭 고범위 정밀 (16~26)",
        "hypothesis": "trail=19.0이 C13 최고. 16~26 범위로 확장 탐색. 더 넓은 stop이 맞는지 확인",
        "breakout_space": {
            "entry_window":       [510, 520, 530],
            "exit_window":        [170, 175, 180],
            "trail_mult":         [16.0, 17.0, 18.0, 19.0, 20.0, 21.0, 22.0, 24.0, 26.0],
            "profit_target_mult": [0.25, 0.3, 0.35],
            "volume_ratio":       [1.2, 1.3, 1.4],
            "invest_pct":         [0.4, 0.45, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 2: trail × exit 교차 탐색 ──────────────────────────
    {
        "name": "trail×exit 교차 탐색",
        "hypothesis": "trail=19 × exit=175 조합 정밀 확인. trail 17~22 × exit 160~220",
        "breakout_space": {
            "entry_window":       [510, 520, 530],
            "exit_window":        [160, 165, 170, 175, 180, 190, 200, 210, 220],
            "trail_mult":         [17.0, 18.0, 19.0, 20.0, 21.0, 22.0],
            "profit_target_mult": [0.28, 0.3, 0.32],
            "volume_ratio":       [1.2, 1.3, 1.4],
            "invest_pct":         [0.4, 0.45, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 3: entry 정밀 (480~580, trail=19 고정) ─────────────
    {
        "name": "진입윈도우 정밀 (trail=19 고정)",
        "hypothesis": "trail=19로 고정 후 entry 480~580 정밀 탐색. 최적 entry 재확인",
        "breakout_space": {
            "entry_window":       [480, 490, 500, 505, 510, 515, 520, 525, 530, 540, 550, 560, 580],
            "exit_window":        [170, 175, 180],
            "trail_mult":         [18.0, 19.0, 20.0],
            "profit_target_mult": [0.25, 0.3, 0.35],
            "volume_ratio":       [1.2, 1.3, 1.4],
            "invest_pct":         [0.4, 0.45, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 4: profit × volume 초정밀 ──────────────────────────
    {
        "name": "profit×volume 초정밀 (trail=19 고정)",
        "hypothesis": "trail=19 고정 후 profit 0.2~0.45 × volume 1.1~1.5 격자 탐색",
        "breakout_space": {
            "entry_window":       [515, 520, 525],
            "exit_window":        [170, 175, 180],
            "trail_mult":         [18.0, 19.0, 20.0],
            "profit_target_mult": [0.2, 0.22, 0.25, 0.28, 0.3, 0.32, 0.35, 0.38, 0.4, 0.45],
            "volume_ratio":       [1.1, 1.15, 1.2, 1.25, 1.3, 1.35, 1.4, 1.5],
            "invest_pct":         [0.4, 0.43, 0.45, 0.48, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 5: C13 최적 주변 초촘촘 Local Search ───────────────
    {
        "name": "C13 최적 주변 Local Search",
        "hypothesis": "entry=520, exit=175, trail=19, profit=0.3, vol=1.3 초촘촘 격자",
        "breakout_space": {
            "entry_window":       [510, 515, 518, 520, 522, 525, 530],
            "exit_window":        [165, 168, 170, 172, 175, 178, 180, 185],
            "trail_mult":         [17.0, 17.5, 18.0, 18.5, 19.0, 19.5, 20.0, 21.0],
            "profit_target_mult": [0.25, 0.28, 0.3, 0.32, 0.35],
            "volume_ratio":       [1.2, 1.25, 1.3, 1.35, 1.4],
            "invest_pct":         [0.4, 0.43, 0.45, 0.48, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 6: 고커버리지 탐색 (trail=19, 130종목+) ────────────
    {
        "name": "고커버리지 탐색 (trail=19, 130종목+)",
        "hypothesis": "trail=19로 고정, entry=400~500에서 130종목+ 탐색. 더 안정적인 전략",
        "breakout_space": {
            "entry_window":       [380, 400, 420, 450, 480, 500],
            "exit_window":        [150, 160, 170, 180, 200, 220],
            "trail_mult":         [17.0, 18.0, 19.0, 20.0],
            "profit_target_mult": [0.25, 0.3, 0.35],
            "volume_ratio":       [1.0, 1.1, 1.2, 1.3],
            "invest_pct":         [0.4, 0.45, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 7: 전방위 격자 탐색 (trail 15~25 포함) ─────────────
    {
        "name": "전방위 격자 탐색 (trail 15~25)",
        "hypothesis": "entry 400~700 × exit 150~300 × trail 15~25 넓은 격자. trail 고범위 재확인",
        "breakout_space": {
            "entry_window":       [400, 450, 500, 520, 550, 600, 650, 700],
            "exit_window":        [150, 170, 200, 220, 250, 280],
            "trail_mult":         [15.0, 17.0, 19.0, 21.0, 23.0, 25.0],
            "profit_target_mult": [0.25, 0.3, 0.35],
            "volume_ratio":       [1.1, 1.2, 1.3, 1.5],
            "invest_pct":         [0.35, 0.4, 0.45, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 8: invest_pct 정밀 탐색 ────────────────────────────
    {
        "name": "invest_pct 정밀 탐색",
        "hypothesis": "invest=0.4가 C13 최고. 0.3~0.7 범위로 확장. 포지션 크기 최적화",
        "breakout_space": {
            "entry_window":       [515, 520, 525],
            "exit_window":        [170, 175, 180],
            "trail_mult":         [18.0, 19.0, 20.0],
            "profit_target_mult": [0.28, 0.3, 0.32],
            "volume_ratio":       [1.2, 1.3, 1.4],
            "invest_pct":         [0.3, 0.35, 0.38, 0.4, 0.43, 0.45, 0.5, 0.55, 0.6, 0.7],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 9: 광역 재탐색 (200~700, 2026 데이터 반영) ──────────
    {
        "name": "광역 재탐색 (200~700, trail 10~25)",
        "hypothesis": "2026 확장 데이터로 80종목+ 조건 전체 재탐색. trail 고범위 포함",
        "breakout_space": {
            "entry_window":       [200, 300, 400, 450, 500, 520, 550, 600, 650, 700],
            "exit_window":        [100, 140, 170, 200, 230, 260, 300],
            "trail_mult":         [10.0, 13.0, 15.0, 17.0, 19.0, 21.0, 23.0, 25.0],
            "profit_target_mult": [0.2, 0.25, 0.3, 0.35, 0.4],
            "volume_ratio":       [1.0, 1.1, 1.2, 1.3, 1.5],
            "invest_pct":         [0.3, 0.35, 0.4, 0.45, 0.5, 0.6],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },
]


# ── 메인 루프 ──────────────────────────────────────────────────────────────

def run_meta_loop(df, ticker, target_win_rate, iter_per_round, min_trades,
                  start_date, end_date):
    from backtest.optimizer import StrategyOptimizer, PARAM_SPACE_V2, PARAM_SPACE_BREAKOUT
    from backtest.strategy_export import save_strategy

    best_wr        = 0.0
    best_params    = {}
    best_strategy  = ""
    best_metrics   = {}
    global_round   = 0
    history        = []   # [(라운드, 전략명, 승률, 파라미터)]
    project_root   = os.path.dirname(__file__)

    print("\n" + "#" * 65)
    print("  자동 승률 개선 무한 루프 시작")
    print(f"  종목: {ticker}  |  목표: {target_win_rate}%  |  라운드당 반복: {iter_per_round}회")
    print("#" * 65)

    cycle = 0
    while True:
        cycle += 1
        print(f"\n\n{'='*65}")
        print(f"  Cycle {cycle} 시작  (현재 최고 승률: {best_wr:.1f}%)")
        print(f"{'='*65}")

        for rnd_cfg in ROUNDS:
            global_round += 1
            name       = rnd_cfg["name"]
            hypothesis = rnd_cfg.get("hypothesis", "")
            b_space    = rnd_cfg.get("breakout_space", PARAM_SPACE_BREAKOUT)
            v2_space   = rnd_cfg.get("v2_space", PARAM_SPACE_V2)

            print(f"\n[Round {global_round}] {name}")
            print(f"  가설: {hypothesis}")
            print(f"  현재 최고 승률: {best_wr:.1f}%")

            # 파라미터 공간 임시 패치
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
                seed=None,   # 매 라운드 다른 시드
            )
            opt.run()

            # 파라미터 공간 복원
            opt_mod.PARAM_SPACE_BREAKOUT = orig_b
            opt_mod.PARAM_SPACE_V2       = orig_v2

            round_best_wr = opt.best.get("승률(%)", 0) or 0
            round_best_params = opt.best.get("_params", {})
            round_best_strat  = opt.best.get("_strategy", "")

            improvement = round_best_wr - best_wr
            if round_best_wr > best_wr:
                best_wr       = round_best_wr
                best_params   = round_best_params
                best_strategy = round_best_strat
                best_metrics  = opt.best
                tag = f"[+] +{improvement:.1f}%p 개선!"
            else:
                tag = f"[-] 개선 없음 (이 라운드 최고: {round_best_wr:.1f}%)"

            history.append((global_round, name, round_best_wr, round_best_params, round_best_strat))

            print(f"\n  결과: {tag}")
            print(f"  전체 최고 승률: {best_wr:.1f}% | 전략={best_strategy} | 파라미터={best_params}")

            # 진행 이력 출력
            print(f"\n  ─ 누적 이력 ─")
            for h in history[-5:]:
                rn, nm, wr, _, _ = h
                mark = "★" if wr == best_wr else " "
                print(f"  {mark} R{rn:02d} {nm:<25} 승률={wr:.1f}%")

            # 목표 달성 시 계속할지 물어보지 않고 더 높은 목표로
            if best_wr >= target_win_rate:
                target_win_rate += 2.0   # 목표를 2%씩 높여서 계속
                print(f"\n  [*] 목표 달성! 새 목표: {target_win_rate:.0f}%로 상향")

            time.sleep(0.1)  # 약간의 텀

        # ── 사이클 완료: 전략 JSON 저장 + git 커밋 ──────────────────
        print(f"\n{'='*65}")
        print(f"  Cycle {cycle} 완료.  최고 승률: {best_wr:.1f}%")
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
                print(f"  전략 저장: {os.path.basename(saved)}")
            except Exception as e:
                print(f"  전략 저장 실패: {e}")
        print(f"  계속 반복 중... (Ctrl+C 로 종료)")
        print(f"{'='*65}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker",  default="005930")
    parser.add_argument("--us",      action="store_true")
    parser.add_argument("--start",   default="2018-01-01")
    parser.add_argument("--end",     default="2026-03-31")
    parser.add_argument("--target",  type=float, default=60.0)
    parser.add_argument("--iter",    type=int,   default=200,
                        help="라운드당 최대 반복 횟수 (기본 200)")
    parser.add_argument("--trades",  type=int,   default=10)
    args = parser.parse_args()

    print(f"데이터 수집: {args.ticker} ({args.start}~{args.end}) ...")
    try:
        if args.us:
            from data.us_fetcher import get_ohlcv_us
            df = get_ohlcv_us(args.ticker, args.start, args.end, use_cache=True)
        else:
            from data.fetcher import get_ohlcv
            df = get_ohlcv(args.ticker, args.start, args.end)
    except Exception as e:
        print(f"데이터 수집 실패: {e}")
        sys.exit(1)

    if df is None or df.empty:
        print("데이터 없음")
        sys.exit(1)

    print(f"수집 완료: {len(df)}봉\n")

    try:
        run_meta_loop(df, args.ticker, args.target, args.iter, args.trades,
                      args.start, args.end)
    except KeyboardInterrupt:
        print("\n\n사용자가 종료했습니다.")


if __name__ == "__main__":
    main()
