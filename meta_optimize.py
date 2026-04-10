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
    # ── Round 1: 기본 Breakout ─────────────────────────────────────
    {
        "name": "기본 Breakout + 이익목표",
        "hypothesis": "이익 목표를 추가하면 수익 구간에서 빠르게 청산 → 승률 증가",
        "breakout_space": {
            "entry_window":       [10, 15, 20, 25, 30, 40, 50],
            "exit_window":        [5, 7, 10, 15, 20],
            "trail_mult":         [2.0, 2.5, 3.0, 3.5, 4.0],
            "profit_target_mult": [0.0, 1.5, 2.0, 2.5, 3.0],
            "volume_ratio":       [1.0, 1.2, 1.5, 2.0],
            "invest_pct":         [0.3, 0.4, 0.5, 0.6],
            "rsi_filter":         [0],
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 2: RSI 필터 추가 ─────────────────────────────────────
    {
        "name": "RSI 진입 필터",
        "hypothesis": "과매수 구간(RSI > N) 돌파는 반전 위험 → RSI 필터로 품질 개선",
        "breakout_space": {
            "entry_window":       [20, 25, 30, 40, 50],
            "exit_window":        [10, 15, 20],
            "trail_mult":         [3.0, 3.5, 4.0],
            "profit_target_mult": [1.5, 2.0, 2.5],
            "volume_ratio":       [1.0, 1.2, 1.5],
            "invest_pct":         [0.3, 0.4, 0.5],
            "rsi_filter":         [60, 65, 70, 75],   # ← 핵심 변화
            "adx_filter":         [0],
            "trend_filter":       [0],
        },
    },

    # ── Round 3: ADX 추세 강도 필터 ──────────────────────────────
    {
        "name": "ADX 추세 강도 필터",
        "hypothesis": "추세가 약한 구간(ADX < N) 돌파는 가짜 신호 → ADX 필터 추가",
        "breakout_space": {
            "entry_window":       [20, 25, 30, 40, 50],
            "exit_window":        [10, 15, 20],
            "trail_mult":         [3.0, 3.5, 4.0],
            "profit_target_mult": [1.5, 2.0, 2.5],
            "volume_ratio":       [1.0, 1.2, 1.5],
            "invest_pct":         [0.3, 0.4, 0.5],
            "rsi_filter":         [65, 70],
            "adx_filter":         [15, 20, 25, 30],  # ← 핵심 변화
            "trend_filter":       [0],
        },
    },

    # ── Round 4: 추세 MA 필터 ─────────────────────────────────────
    {
        "name": "MA 추세 방향 필터",
        "hypothesis": "대세 상승장에서만 돌파 진입 → MA(N)선 위에서만 매수",
        "breakout_space": {
            "entry_window":       [20, 25, 30, 40, 50],
            "exit_window":        [10, 15, 20],
            "trail_mult":         [3.0, 3.5, 4.0],
            "profit_target_mult": [1.5, 2.0, 2.5],
            "volume_ratio":       [1.0, 1.2, 1.5],
            "invest_pct":         [0.3, 0.4, 0.5],
            "rsi_filter":         [65, 70],
            "adx_filter":         [0, 20],
            "trend_filter":       [50, 100, 150, 200],  # ← 핵심 변화
        },
    },

    # ── Round 5: 복합 필터 조합 ──────────────────────────────────
    {
        "name": "RSI + ADX + 추세 복합",
        "hypothesis": "세 필터 동시 적용으로 최고 품질 신호만 선별",
        "breakout_space": {
            "entry_window":       [20, 30, 40, 50, 60, 80],
            "exit_window":        [10, 15, 20, 25],
            "trail_mult":         [3.0, 3.5, 4.0, 4.5],
            "profit_target_mult": [1.5, 2.0, 2.5, 3.0],
            "volume_ratio":       [1.0, 1.2, 1.5],
            "invest_pct":         [0.3, 0.4, 0.5],
            "rsi_filter":         [60, 65, 70],
            "adx_filter":         [15, 20, 25],
            "trend_filter":       [0, 100, 200],
        },
    },

    # ── Round 6: 더 긴 윈도우 ────────────────────────────────────
    {
        "name": "장기 채널 돌파",
        "hypothesis": "더 긴 기간 최고가 돌파는 더 강한 신호 → 60~120일 윈도우",
        "breakout_space": {
            "entry_window":       [60, 70, 80, 90, 100, 120],
            "exit_window":        [20, 25, 30, 40],
            "trail_mult":         [3.0, 4.0, 5.0],
            "profit_target_mult": [1.5, 2.0, 2.5, 3.0],
            "volume_ratio":       [1.0, 1.2, 1.5],
            "invest_pct":         [0.3, 0.4, 0.5],
            "rsi_filter":         [65, 70, 75],
            "adx_filter":         [0, 20],
            "trend_filter":       [0, 200],
        },
    },

    # ── Round 7: 촘촘한 이익 목표 ────────────────────────────────
    {
        "name": "세밀한 이익 목표 탐색",
        "hypothesis": "1.0x~1.5x 짧은 이익 목표로 더 빠른 수익 실현 → 승률 극대화",
        "breakout_space": {
            "entry_window":       [20, 30, 40, 50],
            "exit_window":        [10, 15, 20],
            "trail_mult":         [3.0, 4.0],
            "profit_target_mult": [0.8, 1.0, 1.2, 1.5, 1.8],   # ← 세밀한 목표
            "volume_ratio":       [1.0, 1.2, 1.5],
            "invest_pct":         [0.3, 0.4, 0.5],
            "rsi_filter":         [65, 70],
            "adx_filter":         [0, 20],
            "trend_filter":       [0],
        },
    },

    # ── Round 8: V2 전략 최적화 ──────────────────────────────────
    {
        "name": "MA 크로스 V2 집중 탐색",
        "hypothesis": "V2 전략의 파라미터 공간을 넓혀 더 좋은 조합 탐색",
        "v2_space": {
            "short_window":  [3, 5, 7, 10, 12],
            "long_window":   [15, 20, 25, 30, 35],
            "trend_window":  [40, 50, 60, 80, 100, 120, 150],
            "rsi_entry_max": [55, 60, 65, 70, 75, 80],
            "trail_mult":    [0.0, 2.0, 2.5, 3.0, 3.5, 4.0],
            "invest_pct":    [0.3, 0.4, 0.5, 0.6, 0.7],
        },
        "breakout_space": None,
    },

    # ── Round 9: 전략 재조합 ─────────────────────────────────────
    {
        "name": "전체 파라미터 재랜덤화",
        "hypothesis": "모든 파라미터 공간 재탐색 — 이전 라운드에서 놓친 영역 탐색",
        "breakout_space": {
            "entry_window":       [10, 15, 20, 25, 30, 40, 50, 60, 80],
            "exit_window":        [5, 7, 10, 15, 20, 25, 30],
            "trail_mult":         [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0],
            "profit_target_mult": [0.0, 0.8, 1.0, 1.5, 2.0, 2.5, 3.0],
            "volume_ratio":       [1.0, 1.2, 1.5, 2.0],
            "invest_pct":         [0.2, 0.3, 0.4, 0.5, 0.6],
            "rsi_filter":         [0, 60, 65, 70, 75],
            "adx_filter":         [0, 15, 20, 25, 30],
            "trend_filter":       [0, 50, 100, 150, 200],
        },
    },
]


# ── 메인 루프 ──────────────────────────────────────────────────────────────

def run_meta_loop(df, ticker, target_win_rate, iter_per_round, min_trades):
    from backtest.optimizer import StrategyOptimizer, PARAM_SPACE_V2, PARAM_SPACE_BREAKOUT

    best_wr        = 0.0
    best_params    = {}
    best_strategy  = ""
    global_round   = 0
    history        = []   # [(라운드, 전략명, 승률, 파라미터)]

    print("\n" + "█" * 65)
    print("  자동 승률 개선 무한 루프 시작")
    print(f"  종목: {ticker}  |  목표: {target_win_rate}%  |  라운드당 반복: {iter_per_round}회")
    print("█" * 65)

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
                tag = f"✅ +{improvement:.1f}%p 개선!"
            else:
                tag = f"❌ 개선 없음 (이 라운드 최고: {round_best_wr:.1f}%)"

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
                print(f"\n  🎯 목표 달성! 새 목표: {target_win_rate:.0f}%로 상향")

            time.sleep(0.1)  # 약간의 텀

        print(f"\n{'='*65}")
        print(f"  Cycle {cycle} 완료.  최고 승률: {best_wr:.1f}%")
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
        run_meta_loop(df, args.ticker, args.target, args.iter, args.trades)
    except KeyboardInterrupt:
        print("\n\n사용자가 종료했습니다.")


if __name__ == "__main__":
    main()
