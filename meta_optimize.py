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
    # ── Cycle18 확정 (2018-2026, 80종목+ 기준) ───────────────────
    # best: entry=610, exit=207, trail=23.0, profit=0.5, volume=1.6, invest=0.45
    #       rsi=0, adx=0, trend=0 -> 89.2%, 163종목, 2076거래
    # 핵심: 89% 돌파! entry=640→610 (하향!), trail=28→23 (하향!), profit=0.38→0.5 (급등!)
    # R07: entry=500, exit=260, trail=28, profit=0.4, vol=1.5 → 88.5%, 171종목 (고커버리지!)
    # C19: entry=600~620 × trail=22~25 × profit=0.45~0.55 초정밀 탐색. 90% 달성 도전

    # ── Round 1: C18 최적 초촘촘 Local Search ───────────────────
    {
        "name": "C18 최적 주변 초촘촘 Local Search",
        "hypothesis": "entry=610, exit=207, trail=23, profit=0.5, vol=1.6, inv=0.45. 초촘촘 격자",
        "breakout_space": {
            "entry_window":       [595, 600, 603, 605, 607, 610, 613, 615, 618, 620, 625],
            "exit_window":        [200, 203, 205, 207, 209, 210, 213, 215],
            "trail_mult":         [21.0, 22.0, 22.5, 23.0, 23.5, 24.0, 25.0],
            "profit_target_mult": [0.45, 0.47, 0.48, 0.5, 0.52, 0.53, 0.55],
            "volume_ratio":       [1.5, 1.55, 1.6, 1.65, 1.7],
            "invest_pct":         [0.42, 0.43, 0.45, 0.47, 0.48, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 2: profit=0.5 집중 탐색 (entry=610, trail=23) ─────
    {
        "name": "profit=0.5 집중 탐색 (entry=610, trail=23)",
        "hypothesis": "profit=0.5가 C18 핵심. profit 0.4~0.7 × entry=600~620 × trail=21~25 집중",
        "breakout_space": {
            "entry_window":       [600, 605, 610, 615, 620],
            "exit_window":        [200, 205, 207, 210, 215],
            "trail_mult":         [21.0, 22.0, 23.0, 24.0, 25.0],
            "profit_target_mult": [0.4, 0.42, 0.45, 0.47, 0.5, 0.52, 0.55, 0.6, 0.65, 0.7],
            "volume_ratio":       [1.5, 1.6, 1.7],
            "invest_pct":         [0.42, 0.45, 0.48, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 3: entry=580~650 × trail=20~26 × profit=0.5 격자 ──
    {
        "name": "entry=580~650 × trail=20~26 × profit=0.5 격자",
        "hypothesis": "entry=610, trail=23, profit=0.5 방향. 이 조합 근방 넓은 격자 탐색",
        "breakout_space": {
            "entry_window":       [580, 590, 600, 605, 610, 615, 620, 630, 640, 650],
            "exit_window":        [200, 205, 207, 210, 215],
            "trail_mult":         [20.0, 21.0, 22.0, 23.0, 24.0, 25.0, 26.0],
            "profit_target_mult": [0.45, 0.47, 0.5, 0.52, 0.55],
            "volume_ratio":       [1.5, 1.6, 1.7],
            "invest_pct":         [0.42, 0.45, 0.48, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 4: volume 정밀 (entry=610, trail=23, profit=0.5) ───
    {
        "name": "거래량비율 정밀 (entry=610, trail=23, profit=0.5)",
        "hypothesis": "volume=1.6이 C18 최고. 1.3~2.5 범위 정밀 탐색. 고거래량 필터 효과 극대화",
        "breakout_space": {
            "entry_window":       [605, 610, 615],
            "exit_window":        [203, 207, 210],
            "trail_mult":         [22.0, 23.0, 24.0],
            "profit_target_mult": [0.45, 0.5, 0.55],
            "volume_ratio":       [1.3, 1.4, 1.5, 1.55, 1.6, 1.65, 1.7, 1.8, 2.0, 2.2, 2.5],
            "invest_pct":         [0.42, 0.45, 0.48, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 5: exit 정밀 (entry=610, trail=23, profit=0.5) ─────
    {
        "name": "청산윈도우 정밀 (entry=610, trail=23, profit=0.5)",
        "hypothesis": "exit=207이 C18 최고. entry=610 + trail=23 + profit=0.5 고정 후 exit 160~300",
        "breakout_space": {
            "entry_window":       [605, 610, 615],
            "exit_window":        [160, 175, 190, 200, 205, 207, 210, 215, 220, 230, 250, 270, 290],
            "trail_mult":         [21.0, 22.0, 23.0, 24.0, 25.0],
            "profit_target_mult": [0.45, 0.5, 0.55],
            "volume_ratio":       [1.5, 1.6, 1.7],
            "invest_pct":         [0.42, 0.45, 0.48, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 6: invest_pct 정밀 (entry=610, trail=23, profit=0.5) ─
    {
        "name": "invest_pct 정밀 (entry=610, trail=23, profit=0.5)",
        "hypothesis": "invest=0.45가 C18 최고. 0.3~0.8 범위 정밀 탐색",
        "breakout_space": {
            "entry_window":       [605, 610, 615],
            "exit_window":        [203, 207, 210],
            "trail_mult":         [21.0, 22.0, 23.0, 24.0],
            "profit_target_mult": [0.45, 0.5, 0.55],
            "volume_ratio":       [1.5, 1.6, 1.7],
            "invest_pct":         [0.3, 0.33, 0.35, 0.38, 0.4, 0.42, 0.43, 0.45, 0.47, 0.5, 0.55, 0.6, 0.7],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 7: 고커버리지 탐색 (entry=500, exit=260, 171종목) ──
    {
        "name": "고커버리지 탐색 (entry=500, exit=260, trail=28)",
        "hypothesis": "R07: entry=500, exit=260, trail=28 → 88.5%, 171종목. 이 방향 정밀 탐색",
        "breakout_space": {
            "entry_window":       [470, 480, 490, 500, 510, 520, 530],
            "exit_window":        [230, 240, 250, 260, 270, 280, 290, 300],
            "trail_mult":         [24.0, 25.0, 26.0, 27.0, 28.0, 30.0],
            "profit_target_mult": [0.38, 0.4, 0.42, 0.45, 0.5],
            "volume_ratio":       [1.3, 1.4, 1.5, 1.6],
            "invest_pct":         [0.42, 0.45, 0.48, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 8: trail=30~40 극고범위 (entry=610~650) ─────────────
    {
        "name": "trail 극고범위 탐색 (30~40, entry=610~650)",
        "hypothesis": "trail=32에서 R03 89.0% 달성. trail 30~40 × entry 610~650 집중 탐색",
        "breakout_space": {
            "entry_window":       [610, 620, 630, 640, 650],
            "exit_window":        [200, 205, 207, 210, 215],
            "trail_mult":         [28.0, 30.0, 32.0, 34.0, 36.0, 38.0, 40.0],
            "profit_target_mult": [0.4, 0.42, 0.45, 0.5],
            "volume_ratio":       [1.4, 1.5, 1.6],
            "invest_pct":         [0.42, 0.45, 0.5, 0.55],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 9: 광역 재탐색 (entry 500~800, trail 20~40, profit 0.4~0.7) ─
    {
        "name": "광역 재탐색 (entry 500~800, trail 20~40, profit 0.4~0.7)",
        "hypothesis": "89%+ 달성 새 조합 발굴. entry × trail × profit 전방위 탐색. 90% 도전",
        "breakout_space": {
            "entry_window":       [500, 550, 580, 600, 610, 620, 640, 660, 700, 750, 800],
            "exit_window":        [180, 200, 210, 230, 260, 300],
            "trail_mult":         [18.0, 20.0, 22.0, 25.0, 28.0, 32.0, 36.0, 40.0],
            "profit_target_mult": [0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.7],
            "volume_ratio":       [1.2, 1.4, 1.6, 1.8, 2.0],
            "invest_pct":         [0.4, 0.43, 0.45, 0.5, 0.55],
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
