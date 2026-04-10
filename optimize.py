"""
백테스팅 자동 최적화 실행 스크립트

사용법:
    python optimize.py                        # 삼성전자, 목표 승률 60%
    python optimize.py --ticker 000660        # SK하이닉스
    python optimize.py --ticker AAPL --us     # 미국 주식
    python optimize.py --target 65 --iter 300 # 목표 65%, 300회
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))


def fetch_kr(ticker: str, start: str, end: str):
    from data.fetcher import get_ohlcv
    df = get_ohlcv(ticker, start, end)
    return df


def fetch_us(ticker: str, start: str, end: str):
    from data.us_fetcher import get_ohlcv_us
    df = get_ohlcv_us(ticker, start, end, use_cache=True)
    return df


def main():
    parser = argparse.ArgumentParser(description="백테스팅 파라미터 자동 최적화")
    parser.add_argument("--ticker",  default="005930",    help="종목코드 (기본: 005930 삼성전자)")
    parser.add_argument("--us",      action="store_true", help="미국 주식 여부")
    parser.add_argument("--start",   default="2018-01-01")
    parser.add_argument("--end",     default="2024-12-31")
    parser.add_argument("--capital", type=float, default=10_000_000)
    parser.add_argument("--target",  type=float, default=60.0,  help="목표 승률 (%)")
    parser.add_argument("--iter",    type=int,   default=500,   help="최대 반복 횟수")
    parser.add_argument("--trades",  type=int,   default=10,    help="최소 유효 거래 횟수")
    parser.add_argument("--seed",    type=int,   default=None,  help="랜덤 시드")
    parser.add_argument("--top",     type=int,   default=10,    help="상위 결과 출력 수")
    args = parser.parse_args()

    # ── 데이터 수집 ────────────────────────────────────────────────────
    print(f"데이터 수집 중: {args.ticker} ({args.start} ~ {args.end}) ...")
    try:
        if args.us:
            df = fetch_us(args.ticker, args.start, args.end)
        else:
            df = fetch_kr(args.ticker, args.start, args.end)
    except Exception as e:
        print(f"데이터 수집 실패: {e}")
        sys.exit(1)

    if df is None or df.empty:
        print("데이터 없음. 종목코드 또는 날짜 범위를 확인하세요.")
        sys.exit(1)

    print(f"데이터 수집 완료: {len(df)}봉  ({df.index[0].date()} ~ {df.index[-1].date()})\n")

    # ── 최적화 실행 ────────────────────────────────────────────────────
    from backtest.optimizer import StrategyOptimizer

    opt = StrategyOptimizer(
        df=df,
        ticker=args.ticker,
        capital=args.capital,
        target_win_rate=args.target,
        max_iter=args.iter,
        min_trades=args.trades,
        seed=args.seed,
    )
    opt.run()

    # ── 상위 결과 출력 ─────────────────────────────────────────────────
    top_df = opt.top_results(args.top)
    if top_df.empty:
        print("\n유효한 결과가 없습니다.")
        return

    print(f"\n▶ 상위 {args.top}개 결과 (승률 순)")
    print("-" * 80)
    display_cols = ["_strategy", "승률(%)", "Profit Factor", "연환산수익률(CAGR,%)",
                    "최대낙폭(MDD,%)", "Expectancy(%)", "총거래횟수"]
    print(top_df[display_cols].to_string(index=True))

    print("\n▶ 1위 파라미터 상세")
    best = top_df.iloc[0]
    print(f"  전략: {best['_strategy']}")
    print(f"  파라미터: {best['_params']}")
    print(f"  승률:     {best['승률(%)']:.1f}%")
    print(f"  CAGR:     {best['연환산수익률(CAGR,%)']:+.2f}%")
    print(f"  MDD:      {best['최대낙폭(MDD,%)']:.1f}%")
    print(f"  PF:       {best['Profit Factor']:.2f}")
    print(f"  거래횟수: {best['총거래횟수']}")

    # ── 결과 CSV 저장 ─────────────────────────────────────────────────
    import os
    os.makedirs("results", exist_ok=True)
    out_path = f"results/optimize_{args.ticker}_{args.start[:4]}_{args.end[:4]}.csv"
    save_df = top_df.drop(columns=["_params"], errors="ignore")
    save_df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n결과 저장: {out_path}")


if __name__ == "__main__":
    main()
