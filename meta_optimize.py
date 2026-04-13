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
    # ── Cycle4 확정 파라미터 ──────────────────────────────────────
    # best: entry=100, exit=43, trail=7.0, profit=0.6, volume=1.2
    #       rsi=0, adx=30, trend=0 -> 75.74%, 186종목, 3433거래

    # ── Cycle12 확정 (80종목+ 기준) ──────────────────────────────
    # best: entry=520, exit=170, trail=15.0, profit=0.3, volume=1.3
    #       rsi=0, adx=0, trend=0 -> 85.79%, 150종목, 1773거래
    # 개선 폭 둔화: C10→C12 +0.25%p. 85~86% 수렴 가능성.
    # exit=170(C12) vs exit=250(C10) 비슷한 성능 -> exit 민감도 낮음.
    # C13: entry/exit/trail 초정밀 + 더 넓은 격자로 새 조합 탐색.

    # ── Round 1: entry 정밀 (450~650) ────────────────────────────
    {
        "name": "진입윈도우 정밀 (450~650)",
        "hypothesis": "entry=520이 C12 최고. 450~650 정밀 탐색. exit=170/250 두 경우 모두 포함",
        "breakout_space": {
            "entry_window":       [450, 470, 490, 510, 520, 530, 550, 570, 590, 620, 650],
            "exit_window":        [160, 170, 180, 200, 220, 250],
            "trail_mult":         [14.0, 15.0, 16.0],
            "profit_target_mult": [0.25, 0.3, 0.35],
            "volume_ratio":       [1.2, 1.3, 1.4],
            "invest_pct":         [0.4, 0.45, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 2: exit 정밀 (130~280) ─────────────────────────────
    {
        "name": "청산윈도우 정밀 (130~280)",
        "hypothesis": "exit=170(C12)과 exit=250(C10) 둘 다 좋음. 130~280 세밀하게",
        "breakout_space": {
            "entry_window":       [510, 520, 530],
            "exit_window":        [130, 145, 160, 170, 180, 195, 210, 230, 250, 270],
            "trail_mult":         [14.0, 15.0, 16.0],
            "profit_target_mult": [0.25, 0.3, 0.35],
            "volume_ratio":       [1.2, 1.3, 1.4],
            "invest_pct":         [0.4, 0.45, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 3: trail 정밀 (13~19) ──────────────────────────────
    {
        "name": "ATR 손절폭 정밀 (13~19)",
        "hypothesis": "trail=15.0 확정. 13~19 초정밀 탐색으로 최적값 고정",
        "breakout_space": {
            "entry_window":       [510, 520, 530],
            "exit_window":        [165, 170, 175],
            "trail_mult":         [13.0, 13.5, 14.0, 14.5, 15.0, 15.5, 16.0, 17.0, 18.0, 19.0],
            "profit_target_mult": [0.25, 0.3, 0.35],
            "volume_ratio":       [1.2, 1.3, 1.4],
            "invest_pct":         [0.4, 0.45, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 4: profit 정밀 (0.2~0.45) ──────────────────────────
    {
        "name": "이익목표 정밀 (0.2~0.45)",
        "hypothesis": "profit=0.3 확정. 0.2~0.45 초정밀 탐색",
        "breakout_space": {
            "entry_window":       [510, 520, 530],
            "exit_window":        [165, 170, 175],
            "trail_mult":         [15.0, 15.5, 16.0],
            "profit_target_mult": [0.2, 0.22, 0.25, 0.28, 0.3, 0.32, 0.35, 0.38, 0.4, 0.45],
            "volume_ratio":       [1.2, 1.3, 1.4],
            "invest_pct":         [0.4, 0.45, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 5: volume + invest 초정밀 ──────────────────────────
    {
        "name": "volume+invest 초정밀",
        "hypothesis": "volume=1.3, invest=0.45가 C12 최고. 조합 격자 초정밀 탐색",
        "breakout_space": {
            "entry_window":       [510, 520, 530],
            "exit_window":        [165, 170, 175],
            "trail_mult":         [15.0, 15.5, 16.0],
            "profit_target_mult": [0.28, 0.3, 0.32],
            "volume_ratio":       [1.1, 1.15, 1.2, 1.25, 1.3, 1.35, 1.4, 1.5],
            "invest_pct":         [0.35, 0.38, 0.4, 0.43, 0.45, 0.48, 0.5, 0.55],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 6: 고커버리지 탐색 (120종목+) ──────────────────────
    {
        "name": "고커버리지 탐색 (120종목+)",
        "hypothesis": "entry=400~500에서 120종목+ 확보. 더 안정적인 전략 탐색",
        "breakout_space": {
            "entry_window":       [380, 400, 420, 450, 480, 500],
            "exit_window":        [150, 170, 190, 210, 230],
            "trail_mult":         [13.0, 14.0, 15.0, 16.0],
            "profit_target_mult": [0.25, 0.3, 0.35],
            "volume_ratio":       [1.0, 1.1, 1.2, 1.3],
            "invest_pct":         [0.4, 0.45, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 7: 전방위 격자 탐색 ────────────────────────────────
    {
        "name": "전방위 격자 탐색",
        "hypothesis": "entry 400~700 × exit 150~300 × trail 13~17 격자. 놓친 최적점 발굴",
        "breakout_space": {
            "entry_window":       [400, 450, 500, 520, 550, 600, 650, 700],
            "exit_window":        [150, 170, 200, 220, 250, 280, 300],
            "trail_mult":         [13.0, 14.0, 15.0, 16.0, 17.0],
            "profit_target_mult": [0.25, 0.3, 0.35, 0.4],
            "volume_ratio":       [1.1, 1.2, 1.3, 1.5],
            "invest_pct":         [0.35, 0.4, 0.45, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 8: C12 최적 주변 Local Search ──────────────────────
    {
        "name": "C12 최적 주변 Local Search",
        "hypothesis": "entry=520, exit=170, trail=15, profit=0.3, vol=1.3 초촘촘",
        "breakout_space": {
            "entry_window":       [500, 505, 510, 515, 520, 525, 530, 535, 540, 550],
            "exit_window":        [155, 160, 163, 167, 170, 173, 177, 180, 185, 190],
            "trail_mult":         [14.0, 14.5, 15.0, 15.5, 16.0, 16.5],
            "profit_target_mult": [0.25, 0.28, 0.3, 0.32, 0.35],
            "volume_ratio":       [1.2, 1.25, 1.3, 1.35, 1.4],
            "invest_pct":         [0.4, 0.43, 0.45, 0.48, 0.5],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 9: 광역 재탐색 (실용 범위 200~700) ──────────────────
    {
        "name": "광역 재탐색 (200~700)",
        "hypothesis": "80종목+ 조건에서 entry 200~700 전체 재탐색. 새 최고점 발굴",
        "breakout_space": {
            "entry_window":       [200, 300, 400, 450, 500, 520, 550, 600, 650, 700],
            "exit_window":        [100, 140, 170, 200, 230, 260, 300],
            "trail_mult":         [12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0],
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
    parser.add_argument("--end",     default="2024-12-31")
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
