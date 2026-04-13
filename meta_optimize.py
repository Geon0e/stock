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
    # ── Cycle15 확정 (2018-2026, 80종목+ 기준) ───────────────────
    # best: entry=520, exit=205, trail=19.0, profit=0.4, volume=1.5, invest=0.43
    #       rsi=0, adx=0, trend=0 -> 88.2%, 170종목, 2524거래
    # 핵심: trail=22도 88.1% (거의 동일). profit 0.35→0.4, exit 200→205 소폭 이동.
    # R09: entry=450, exit=260, trail=19, profit=0.4, vol=1.2 → 87.6%, 181종목 (고커버리지!)
    # C16: exit=200~220 × trail=19~23 × profit=0.38~0.45 초정밀 + 고커버리지 탐색

    # ── Round 1: C15 최적 초촘촘 Local Search ───────────────────
    {
        "name": "C15 최적 주변 초촘촘 Local Search",
        "hypothesis": "entry=520, exit=205, trail=19, profit=0.4, vol=1.5, inv=0.43. exit 195~215 × trail 18~23 × profit 0.35~0.45",
        "breakout_space": {
            "entry_window":       [510, 515, 518, 520, 522, 525, 530],
            "exit_window":        [195, 198, 200, 203, 205, 207, 210, 213, 215],
            "trail_mult":         [18.0, 18.5, 19.0, 19.5, 20.0, 21.0, 22.0, 23.0],
            "profit_target_mult": [0.35, 0.37, 0.38, 0.4, 0.42, 0.43, 0.45],
            "volume_ratio":       [1.4, 1.5, 1.6],
            "invest_pct":         [0.4, 0.43, 0.45, 0.48, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 2: trail=22 집중 탐색 ─────────────────────────────
    {
        "name": "trail=22 집중 탐색 (exit=195~215)",
        "hypothesis": "trail=22에서 R04가 88.1% 달성. trail 20~25 × exit 195~220 집중 탐색",
        "breakout_space": {
            "entry_window":       [515, 520, 525],
            "exit_window":        [195, 200, 205, 210, 215, 220],
            "trail_mult":         [20.0, 21.0, 22.0, 23.0, 24.0, 25.0],
            "profit_target_mult": [0.35, 0.38, 0.4, 0.42, 0.45],
            "volume_ratio":       [1.4, 1.5, 1.6],
            "invest_pct":         [0.4, 0.43, 0.45, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 3: entry 450 고커버리지 정밀 ──────────────────────
    {
        "name": "entry=450 고커버리지 정밀 (exit=240~280)",
        "hypothesis": "R09: entry=450, exit=260, vol=1.2 → 87.6%, 181종목. 이 방향 정밀 탐색",
        "breakout_space": {
            "entry_window":       [430, 440, 450, 460, 470, 480, 490, 500],
            "exit_window":        [220, 240, 250, 260, 270, 280, 300],
            "trail_mult":         [17.0, 18.0, 19.0, 20.0, 21.0],
            "profit_target_mult": [0.35, 0.38, 0.4, 0.42, 0.45],
            "volume_ratio":       [1.1, 1.2, 1.3, 1.4],
            "invest_pct":         [0.4, 0.45, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 4: profit × exit 격자 (trail=19~22) ────────────────
    {
        "name": "profit×exit 격자 탐색 (trail=19~22)",
        "hypothesis": "profit=0.4 × exit=200~220 × trail=19~22 격자. 이 조합 공간 정밀 탐색",
        "breakout_space": {
            "entry_window":       [515, 520, 525],
            "exit_window":        [190, 195, 200, 205, 210, 215, 220, 225, 230],
            "trail_mult":         [18.0, 19.0, 20.0, 21.0, 22.0, 23.0],
            "profit_target_mult": [0.3, 0.33, 0.35, 0.38, 0.4, 0.42, 0.45, 0.5],
            "volume_ratio":       [1.4, 1.5, 1.6],
            "invest_pct":         [0.4, 0.43, 0.45, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 5: volume 정밀 (trail=19~22, exit=205) ─────────────
    {
        "name": "거래량비율 정밀 (trail=19~22, exit=205)",
        "hypothesis": "volume=1.5 확정 후 1.2~2.0 재탐색. 높은 volume이 승률 향상에 기여하는지",
        "breakout_space": {
            "entry_window":       [515, 520, 525],
            "exit_window":        [200, 205, 210],
            "trail_mult":         [19.0, 20.0, 21.0, 22.0],
            "profit_target_mult": [0.38, 0.4, 0.42],
            "volume_ratio":       [1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 2.0],
            "invest_pct":         [0.4, 0.43, 0.45, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 6: invest 정밀 (trail=19~22, exit=205) ─────────────
    {
        "name": "invest_pct 정밀 (trail=19~22, exit=205)",
        "hypothesis": "invest=0.43이 C15 최고. 0.3~0.7 범위 정밀 탐색. 최적 포지션 크기 확정",
        "breakout_space": {
            "entry_window":       [515, 520, 525],
            "exit_window":        [200, 205, 210],
            "trail_mult":         [19.0, 20.0, 21.0, 22.0],
            "profit_target_mult": [0.38, 0.4, 0.42],
            "volume_ratio":       [1.4, 1.5, 1.6],
            "invest_pct":         [0.3, 0.33, 0.35, 0.38, 0.4, 0.43, 0.45, 0.5, 0.55, 0.6, 0.7],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 7: entry 정밀 (480~560, exit=205, trail=19~22) ─────
    {
        "name": "진입윈도우 정밀 (exit=205, trail=19~22 고정)",
        "hypothesis": "entry=520 재확인. 480~560 범위 정밀 탐색으로 더 좋은 entry 발굴",
        "breakout_space": {
            "entry_window":       [480, 490, 500, 505, 510, 515, 518, 520, 522, 525, 530, 540, 550, 560],
            "exit_window":        [200, 205, 210],
            "trail_mult":         [19.0, 20.0, 21.0, 22.0],
            "profit_target_mult": [0.38, 0.4, 0.42],
            "volume_ratio":       [1.4, 1.5, 1.6],
            "invest_pct":         [0.4, 0.43, 0.45],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 8: 고커버리지 탐색 (170종목+ 목표) ─────────────────
    {
        "name": "고커버리지 탐색 (170종목+ 목표)",
        "hypothesis": "entry=450~500 + exit=220~300 + vol=1.1~1.3으로 180종목+ 탐색. 커버리지 극대화",
        "breakout_space": {
            "entry_window":       [400, 420, 440, 450, 460, 480, 500],
            "exit_window":        [200, 220, 240, 260, 280, 300],
            "trail_mult":         [17.0, 18.0, 19.0, 20.0, 21.0],
            "profit_target_mult": [0.35, 0.38, 0.4, 0.42],
            "volume_ratio":       [1.0, 1.1, 1.2, 1.3, 1.4],
            "invest_pct":         [0.4, 0.45, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 9: 광역 재탐색 (trail 19~28, exit 150~400) ─────────
    {
        "name": "광역 재탐색 (trail 19~28, exit 150~400)",
        "hypothesis": "trail 고범위 × exit 넓은 범위 전체 재탐색. 88% 넘을 새 조합 발굴",
        "breakout_space": {
            "entry_window":       [400, 450, 500, 520, 550, 600, 650],
            "exit_window":        [150, 180, 200, 220, 250, 280, 320, 380],
            "trail_mult":         [17.0, 19.0, 21.0, 23.0, 25.0, 28.0],
            "profit_target_mult": [0.3, 0.35, 0.38, 0.4, 0.45, 0.5],
            "volume_ratio":       [1.0, 1.2, 1.4, 1.5, 1.7, 2.0],
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
