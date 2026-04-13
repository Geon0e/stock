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
    # ── Cycle3 핵심 발견 ─────────────────────────────────────────
    # best: entry=100, exit=50, trail=6.0, profit=0.8, volume=1.2
    #       rsi=0(없음), adx=30, trend=0(없음) -> 73.6%, 186종목, 3290거래
    # 가설: 100일 신고가 자체가 강한 추세 선택. RSI/MA 필터는 불필요.
    #       ADX>30만으로 추세 강도 충분. volume=1.2로 광범위 커버.

    # ── Round 1: entry_window 정밀 (80~150) ───────────────────────
    {
        "name": "초장기 진입윈도우 정밀 탐색",
        "hypothesis": "entry=100이 Cycle3 최고. 80~150 범위 촘촘하게 탐색",
        "breakout_space": {
            "entry_window":       [80, 85, 90, 95, 100, 105, 110, 120, 130, 150],
            "exit_window":        [40, 45, 50, 55, 60],
            "trail_mult":         [5.5, 6.0, 6.5],
            "profit_target_mult": [0.7, 0.8, 0.9],
            "volume_ratio":       [1.0, 1.2, 1.3],
            "invest_pct":         [0.5, 0.6],
            "rsi_filter":         [0],
            "adx_filter":         [28, 30, 32],
            "trend_filter":       [0],
        },
    },

    # ── Round 2: exit_window 정밀 (30~70) ────────────────────────
    {
        "name": "청산 윈도우 정밀 (30~70 집중)",
        "hypothesis": "exit=50이 Cycle3 최고. entry:exit 비율 2:1 가설. 30~70 탐색",
        "breakout_space": {
            "entry_window":       [100],
            "exit_window":        [30, 35, 40, 45, 50, 55, 60, 65, 70],
            "trail_mult":         [5.5, 6.0, 6.5],
            "profit_target_mult": [0.7, 0.8, 0.9],
            "volume_ratio":       [1.0, 1.2],
            "invest_pct":         [0.5, 0.6],
            "rsi_filter":         [0],
            "adx_filter":         [28, 30, 32],
            "trend_filter":       [0],
        },
    },

    # ── Round 3: trail_mult 정밀 (5.0~8.0) ────────────────────────
    {
        "name": "ATR 손절폭 정밀 (5.0~8.0)",
        "hypothesis": "trail=6.0이 Cycle3 최고. 5~8 범위 촘촘하게. 긴 윈도우엔 넓은 손절 필요",
        "breakout_space": {
            "entry_window":       [90, 100, 110],
            "exit_window":        [45, 50, 55],
            "trail_mult":         [5.0, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0],
            "profit_target_mult": [0.7, 0.8, 0.9],
            "volume_ratio":       [1.0, 1.2],
            "invest_pct":         [0.5, 0.6],
            "rsi_filter":         [0],
            "adx_filter":         [28, 30, 32],
            "trend_filter":       [0],
        },
    },

    # ── Round 4: ADX 정밀 (RSI/MA 없이) ──────────────────────────
    {
        "name": "ADX 단독 필터 정밀 탐색",
        "hypothesis": "RSI/MA 없이 ADX만. 20~40 범위에서 최적 ADX 임계값 탐색",
        "breakout_space": {
            "entry_window":       [90, 100, 110],
            "exit_window":        [45, 50, 55],
            "trail_mult":         [6.0, 6.5],
            "profit_target_mult": [0.7, 0.8, 0.9],
            "volume_ratio":       [1.0, 1.2],
            "invest_pct":         [0.5, 0.6],
            "rsi_filter":         [0],
            "adx_filter":         [20, 22, 25, 27, 28, 30, 32, 35, 38, 40],
            "trend_filter":       [0],
        },
    },

    # ── Round 5: profit_target 정밀 (0.5~1.2) ────────────────────
    {
        "name": "이익목표 정밀 (0.5~1.2)",
        "hypothesis": "profit=0.8이 Cycle3 최고. 0.5~1.2 미세 탐색",
        "breakout_space": {
            "entry_window":       [90, 100, 110],
            "exit_window":        [45, 50, 55],
            "trail_mult":         [6.0, 6.5],
            "profit_target_mult": [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2],
            "volume_ratio":       [1.0, 1.2],
            "invest_pct":         [0.5, 0.6],
            "rsi_filter":         [0],
            "adx_filter":         [30],
            "trend_filter":       [0],
        },
    },

    # ── Round 6: volume_ratio 탐색 ───────────────────────────────
    {
        "name": "거래량 조건 탐색 (1.0~1.5)",
        "hypothesis": "volume=1.2가 Cycle3 최고. 1.0~1.5 미세 탐색. 낮을수록 종목 커버 증가",
        "breakout_space": {
            "entry_window":       [90, 100, 110],
            "exit_window":        [45, 50, 55],
            "trail_mult":         [6.0, 6.5],
            "profit_target_mult": [0.7, 0.8, 0.9],
            "volume_ratio":       [1.0, 1.05, 1.1, 1.2, 1.3, 1.4, 1.5],
            "invest_pct":         [0.5, 0.6],
            "rsi_filter":         [0],
            "adx_filter":         [28, 30, 32],
            "trend_filter":       [0],
        },
    },

    # ── Round 7: invest_pct + ADX 없는 극단 탐색 ─────────────────
    {
        "name": "필터 최소화 극단 탐색",
        "hypothesis": "RSI=0, MA=0, ADX=0 모두 제거. 순수 돌파 신호 강도만으로 승률 달성 가능?",
        "breakout_space": {
            "entry_window":       [80, 90, 100, 110, 120],
            "exit_window":        [40, 45, 50, 55, 60],
            "trail_mult":         [5.5, 6.0, 6.5, 7.0],
            "profit_target_mult": [0.6, 0.7, 0.8, 1.0],
            "volume_ratio":       [1.0, 1.1, 1.2],
            "invest_pct":         [0.4, 0.5, 0.6],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 8: Cycle3 최적 주변 Local Search ────────────────────
    {
        "name": "Cycle3 최적 주변 Local Search",
        "hypothesis": "entry=100, exit=50, trail=6, profit=0.8, vol=1.2, adx=30 주변 촘촘하게",
        "breakout_space": {
            "entry_window":       [85, 90, 95, 100, 105, 110, 115, 120],
            "exit_window":        [40, 43, 45, 48, 50, 53, 55, 58, 60],
            "trail_mult":         [5.5, 5.8, 6.0, 6.2, 6.5, 7.0],
            "profit_target_mult": [0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
            "volume_ratio":       [1.0, 1.1, 1.2, 1.3, 1.4],
            "invest_pct":         [0.4, 0.5, 0.55, 0.6],
            "rsi_filter":         [0],
            "adx_filter":         [25, 27, 28, 30, 32, 35],
            "trend_filter":       [0],
        },
    },

    # ── Round 9: MA200 재검증 (긴 윈도우 조합에서) ────────────────
    {
        "name": "MA200 재검증 (100일 윈도우 기반)",
        "hypothesis": "entry=100에서 MA200이 도움되는지 재확인. 대세 하락장 제외 효과 검증",
        "breakout_space": {
            "entry_window":       [90, 100, 110, 120],
            "exit_window":        [45, 50, 55],
            "trail_mult":         [5.5, 6.0, 6.5],
            "profit_target_mult": [0.7, 0.8, 0.9],
            "volume_ratio":       [1.0, 1.1, 1.2],
            "invest_pct":         [0.5, 0.6],
            "rsi_filter":         [0],
            "adx_filter":         [25, 28, 30],
            "trend_filter":       [0, 200],
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
