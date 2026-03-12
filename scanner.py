"""
전종목 매수/매도 신호 스캐너

실행:
    # KOSPI 200 (기본)
    python scanner.py
    python scanner.py --market kospi200

    # NASDAQ 100
    python scanner.py --market nasdaq100

    # 공통 옵션
    python scanner.py --market nasdaq100 --days 90 --top 20
    python scanner.py --market kospi200  --out results/scan.csv
    python scanner.py --no-cache
"""

import io
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
from signals import evaluate

# ── 설정 ─────────────────────────────────────────────────────────────────────
DEFAULT_DAYS  = 60
DEFAULT_TOP   = 10
MAX_WORKERS   = 5
REQUEST_DELAY = 0.3
W = 72


# ── 출력 헬퍼 ─────────────────────────────────────────────────────────────────

def sep(ch="─"):    print(ch * W)
def title(text):
    print(); sep("═")
    print(" " * max(0, (W - len(text)) // 2) + text); sep("═")
def section(text):
    print(); sep(); print(f"  {text}"); sep()


# ── 워커 함수 ─────────────────────────────────────────────────────────────────

def _fetch_kr(args):
    """한국 주식 단일 종목 수집 + 신호 계산"""
    ticker, name, start, end, crawler, use_cache = args
    try:
        df = crawler.get_ohlcv(ticker, start, end, use_cache=use_cache)
        if df is None or df.empty or len(df) < 10:
            return None
        result = evaluate(df)
        return _build_row(ticker, name, df, result)
    except Exception:
        return None


def _fetch_us(args):
    """미국 주식 단일 종목 수집 + 신호 계산"""
    ticker, name, start, end, use_cache = args
    try:
        from data.us_fetcher import get_ohlcv_us
        df = get_ohlcv_us(ticker, start, end, use_cache=use_cache)
        if df is None or df.empty or len(df) < 10:
            return None
        result = evaluate(df)
        return _build_row(ticker, name, df, result)
    except Exception:
        return None


def _build_row(ticker, name, df, result):
    return {
        "ticker":  ticker,
        "name":    name,
        "signal":  result["signal"],
        "score":   result["score"],
        "regime":  result.get("regime", "중립"),
        "adx":     result.get("adx"),
        "details": result["details"],
        "close":   df["Close"].iloc[-1],
        "ret5":    (df["Close"].iloc[-1] / df["Close"].iloc[-5]  - 1) * 100 if len(df) >= 5  else 0,
        "ret20":   (df["Close"].iloc[-1] / df["Close"].iloc[-20] - 1) * 100 if len(df) >= 20 else 0,
    }


# ── 스캔 공통 로직 ────────────────────────────────────────────────────────────

def _run_parallel(task_args, worker_fn, total) -> list:
    results, done, errors = [], 0, 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(worker_fn, arg): arg for arg in task_args}
        for future in as_completed(futures):
            done += 1
            res = future.result()
            arg = futures[future]
            if res:
                results.append(res)
                sig_mark = {"BUY": "★", "SELL": "▼", "HOLD": "─"}.get(res["signal"], "?")
                print(
                    f"  [{done:3d}/{total}] {res['ticker']:<6} {res['name']:<18}"
                    f"  {sig_mark} {res['signal']:<4}  {res['score']:3d}점"
                    f"  {res['ret5']:+.1f}%"
                )
            else:
                errors += 1
                print(f"  [{done:3d}/{total}] {arg[0]:<6} {arg[1]:<18}  [수집 실패]")
    print(f"\n  완료: 성공 {len(results)}개 / 실패 {errors}개")
    return results


def _print_results(df, top, market_label, currency):
    buy_df  = df[df["signal"] == "BUY"]
    sell_df = df[df["signal"] == "SELL"]
    total   = len(df)

    section(f"② 매수 추천 상위 {top}개  (점수 높은 순)")
    _print_table(buy_df.head(top), currency)

    section(f"③ 매도 추천 상위 {top}개  (점수 낮은 순)")
    _print_table(sell_df.sort_values("score").head(top), currency)

    section("④ 전략별 상세 (전체 종목)")
    for _, row in df.iterrows():
        regime_str = row.get("regime", "중립")
        adx_str    = f"ADX={row['adx']:.1f}" if row.get("adx") is not None else "ADX=N/A"
        sig_label  = {"BUY": "★매수", "SELL": "▼매도", "HOLD": "─관망"}.get(row["signal"], row["signal"])
        print(f"\n  [{row['ticker']}] {row['name']}  |  {sig_label}  점수: {row['score']}/100  |  시장: {regime_str} ({adx_str})")
        for d in row["details"]:
            bar    = "█" * (d["score"] // 10) + "░" * (10 - d["score"] // 10)
            w_str  = f"×{d['weight']:.1f}"
            print(f"    {d['name']:<12}  {d['signal']:<4}  [{bar}] {d['score']:3d}  {w_str}  {d['reason']}")

    section("⑤ 신호 분포 / 시장 상태")
    for sig in ("BUY", "HOLD", "SELL"):
        count = len(df[df["signal"] == sig])
        bar   = "█" * (count * 30 // max(total, 1))
        print(f"  {sig:<4}  {count:>3}개  {bar}")
    print()
    for regime in ("추세장", "횡보장", "중립"):
        if "regime" in df.columns:
            count = len(df[df["regime"] == regime])
            print(f"  {regime:<4}  {count:>3}개")


def _save_csv(df, market, out):
    cols = ["ticker", "name", "signal", "score", "regime", "adx", "close", "ret5", "ret20"]
    cols = [c for c in cols if c in df.columns]
    save_df = df[cols].copy()
    col_names = {"ticker": "종목코드", "name": "종목명", "signal": "신호", "score": "점수",
                 "regime": "시장상태", "adx": "ADX", "close": "종가",
                 "ret5": "5일수익률", "ret20": "20일수익률"}
    save_df.columns = [col_names.get(c, c) for c in cols]
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        path = out
    else:
        Path("results").mkdir(exist_ok=True)
        path = f"results/{market}_scan_{datetime.today().strftime('%Y%m%d_%H%M')}.csv"
    save_df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"\n  결과 저장: {path}")


def _print_table(df: pd.DataFrame, currency: str = "원"):
    if df.empty:
        print("  (해당 종목 없음)")
        return
    print(f"  {'순위':>3}  {'코드':<6} {'종목명':<18} {'신호':<5} {'점수':>4}  {'종가':>12}  {'5일':>7}  {'20일':>7}")
    sep("·")
    for rank, (_, row) in enumerate(df.iterrows(), 1):
        sig_label = {"BUY": "★매수", "SELL": "▼매도", "HOLD": "─관망"}.get(row["signal"], row["signal"])
        close_str = f"{row['close']:>10,.2f}{currency}" if currency == "$" else f"{row['close']:>10,.0f}{currency}"
        print(
            f"  {rank:>3}  {row['ticker']:<6} {row['name']:<18}"
            f" {sig_label:<5} {row['score']:>4}"
            f"  {close_str}"
            f"  {row['ret5']:>+6.1f}%"
            f"  {row['ret20']:>+6.1f}%"
        )


# ── KOSPI 200 스캔 ────────────────────────────────────────────────────────────

def scan_kospi200(days=DEFAULT_DAYS, top=DEFAULT_TOP, out=None, use_cache=True) -> pd.DataFrame:
    from data.fetcher import get_kospi200_tickers
    from data.crawler import NaverFinanceCrawler

    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=days + 30)).strftime("%Y-%m-%d")

    title("KOSPI 200 매수/매도 신호 스캐너")
    print(f"  분석 기간  : 최근 {days}일  ({start} ~ {end})")
    print(f"  조회 시각  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    stocks = get_kospi200_tickers(use_cache=use_cache)
    total  = len(stocks)
    print(f"  대상 종목  : KOSPI 200 ({total}개)")

    section(f"① 전종목 신호 계산 중... ({total}개)")
    crawler   = NaverFinanceCrawler(request_delay=REQUEST_DELAY, verify_ssl=False)
    task_args = [
        (row["Code"], row["Name"], start, end, crawler, use_cache)
        for _, row in stocks.iterrows()
    ]
    results = _run_parallel(task_args, _fetch_kr, total)

    if not results:
        print("  [오류] 수집된 데이터가 없습니다.")
        return pd.DataFrame()

    df = pd.DataFrame(results).sort_values("score", ascending=False).reset_index(drop=True)
    _print_results(df, top, "KOSPI 200", "원")
    _save_csv(df, "kospi200", out)
    sep("═"); print(f"  완료: {datetime.now().strftime('%H:%M:%S')}"); sep("═"); print()
    return df


# ── NASDAQ 100 스캔 ───────────────────────────────────────────────────────────

def scan_nasdaq100(days=DEFAULT_DAYS, top=DEFAULT_TOP, out=None, use_cache=True) -> pd.DataFrame:
    from data.us_fetcher import get_nasdaq100_tickers

    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=days + 30)).strftime("%Y-%m-%d")

    title("NASDAQ 100 매수/매도 신호 스캐너")
    print(f"  분석 기간  : 최근 {days}일  ({start} ~ {end})")
    print(f"  조회 시각  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    stocks = get_nasdaq100_tickers(use_cache=use_cache)
    total  = len(stocks)
    print(f"  대상 종목  : NASDAQ 100 ({total}개)")

    section(f"① 전종목 신호 계산 중... ({total}개)")
    task_args = [
        (row["Code"], row["Name"], start, end, use_cache)
        for _, row in stocks.iterrows()
    ]
    results = _run_parallel(task_args, _fetch_us, total)

    if not results:
        print("  [오류] 수집된 데이터가 없습니다.")
        return pd.DataFrame()

    df = pd.DataFrame(results).sort_values("score", ascending=False).reset_index(drop=True)
    _print_results(df, top, "NASDAQ 100", "$")
    _save_csv(df, "nasdaq100", out)
    sep("═"); print(f"  완료: {datetime.now().strftime('%H:%M:%S')}"); sep("═"); print()
    return df


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="전종목 매수/매도 신호 스캐너")
    parser.add_argument("--market",   choices=["kospi200", "nasdaq100"], default="kospi200",
                        help="스캔 대상 시장 (기본: kospi200)")
    parser.add_argument("--days",     type=int, default=DEFAULT_DAYS, help="분석 기간 (일)")
    parser.add_argument("--top",      type=int, default=DEFAULT_TOP,  help="상위 N개 출력")
    parser.add_argument("--out",      type=str, default=None,         help="결과 CSV 저장 경로")
    parser.add_argument("--no-cache", action="store_true",            help="캐시 무시하고 새로 수집")
    args = parser.parse_args()

    kwargs = dict(days=args.days, top=args.top, out=args.out, use_cache=not args.no_cache)

    if args.market == "nasdaq100":
        scan_nasdaq100(**kwargs)
    else:
        scan_kospi200(**kwargs)
