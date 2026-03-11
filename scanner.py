"""
KOSPI 200 전종목 매수/매도 신호 스캐너

실행:
    python scanner.py              # 기본 (최근 60일 데이터, 상위 10개 출력)
    python scanner.py --top 20     # 상위 20개 출력
    python scanner.py --days 90    # 최근 90일 데이터 사용
    python scanner.py --out results/scan.csv  # 결과 CSV 저장
    python scanner.py --no-cache   # 캐시 무시하고 새로 수집
"""

import io
import sys
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
from data.fetcher import get_kospi200_tickers
from data.crawler import NaverFinanceCrawler
from signals import evaluate

# ─── 설정 ────────────────────────────────────────────────────────────────────
DEFAULT_DAYS   = 60    # 신호 계산에 사용할 최근 거래일 수
DEFAULT_TOP    = 10    # 출력할 상위/하위 종목 수
MAX_WORKERS    = 5     # 병렬 다운로드 스레드 수 (서버 부하 고려)
REQUEST_DELAY  = 0.3   # 종목 간 딜레이(초)
# ─────────────────────────────────────────────────────────────────────────────

W = 72


def sep(ch="─"):
    print(ch * W)


def title(text):
    print()
    sep("═")
    pad = (W - len(text)) // 2
    print(" " * max(0, pad) + text)
    sep("═")


def section(text):
    print()
    sep()
    print(f"  {text}")
    sep()


def _fetch_one(args):
    """단일 종목 OHLCV 수집 + 신호 계산 (스레드 워커)"""
    ticker, name, start, end, crawler, use_cache = args
    try:
        df = crawler.get_ohlcv(ticker, start, end, use_cache=use_cache)
        if df is None or df.empty or len(df) < 10:
            return None
        result = evaluate(df)
        return {
            "ticker":  ticker,
            "name":    name,
            "signal":  result["signal"],
            "score":   result["score"],
            "details": result["details"],
            "close":   df["Close"].iloc[-1],
            "ret5":    (df["Close"].iloc[-1] / df["Close"].iloc[-5] - 1) * 100 if len(df) >= 5 else 0,
            "ret20":   (df["Close"].iloc[-1] / df["Close"].iloc[-20] - 1) * 100 if len(df) >= 20 else 0,
        }
    except Exception as e:
        return None


def scan(days: int = DEFAULT_DAYS, top: int = DEFAULT_TOP,
         out: str = None, use_cache: bool = True) -> pd.DataFrame:
    """
    KOSPI 200 전종목 스캔

    Args:
        days:      최근 N일 데이터 기준
        top:       출력할 상위/하위 종목 수
        out:       결과 CSV 저장 경로 (None이면 저장 안 함)
        use_cache: OHLCV 캐시 사용 여부

    Returns:
        스캔 결과 DataFrame
    """
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=days + 30)).strftime("%Y-%m-%d")

    # ── 1. KOSPI 200 종목 목록 ──────────────────────────────────────────────
    title("KOSPI 200 매수/매도 신호 스캐너")
    print(f"  분석 기간  : 최근 {days}일  ({start} ~ {end})")
    print(f"  조회 시각  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    kospi200 = get_kospi200_tickers(use_cache=use_cache)
    total    = len(kospi200)
    print(f"  대상 종목  : KOSPI 200 ({total}개)")

    # ── 2. 병렬 데이터 수집 + 신호 계산 ────────────────────────────────────
    section(f"① 전종목 신호 계산 중... ({total}개)")

    crawler = NaverFinanceCrawler(request_delay=REQUEST_DELAY, verify_ssl=False)

    task_args = [
        (row["Code"], row["Name"], start, end, crawler, use_cache)
        for _, row in kospi200.iterrows()
    ]

    results = []
    done = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch_one, arg): arg for arg in task_args}
        for future in as_completed(futures):
            done += 1
            res = future.result()
            if res:
                results.append(res)
                sig_mark = {"BUY": "★", "SELL": "▼", "HOLD": "─"}.get(res["signal"], "?")
                print(
                    f"  [{done:3d}/{total}] {res['ticker']} {res['name']:<14}"
                    f"  {sig_mark} {res['signal']:<4}  {res['score']:3d}점"
                    f"  종가 {res['close']:>9,.0f}원"
                    f"  5일 {res['ret5']:+.1f}%"
                )
            else:
                errors += 1
                arg = futures[future]
                print(f"  [{done:3d}/{total}] {arg[0]} {arg[1]:<14}  [수집 실패]")

    print(f"\n  완료: 성공 {len(results)}개 / 실패 {errors}개")

    if not results:
        print("  [오류] 수집된 데이터가 없습니다.")
        return pd.DataFrame()

    df = pd.DataFrame(results).sort_values("score", ascending=False).reset_index(drop=True)

    # ── 3. 결과 요약 출력 ───────────────────────────────────────────────────
    section(f"② 매수 추천 상위 {top}개  (점수 높은 순)")
    buy_df = df[df["signal"] == "BUY"].head(top)
    _print_table(buy_df)

    section(f"③ 매도 추천 상위 {top}개  (점수 낮은 순)")
    sell_df = df[df["signal"] == "SELL"].sort_values("score").head(top)
    _print_table(sell_df)

    section("④ 전략별 상세 (매수 추천 TOP 5)")
    for _, row in buy_df.head(5).iterrows():
        print(f"\n  [{row['ticker']}] {row['name']}  |  점수: {row['score']}/100")
        for d in row["details"]:
            bar = "█" * (d["score"] // 10) + "░" * (10 - d["score"] // 10)
            print(f"    {d['name']:<12}  {d['signal']:<4}  [{bar}] {d['score']:3d}  {d['reason']}")

    # ── 4. 신호 분포 ────────────────────────────────────────────────────────
    section("⑤ 신호 분포")
    for sig in ("BUY", "HOLD", "SELL"):
        count = len(df[df["signal"] == sig])
        bar   = "█" * (count * 30 // total)
        print(f"  {sig:<4}  {count:>3}개  {bar}")

    # ── 5. CSV 저장 ─────────────────────────────────────────────────────────
    save_df = df[["ticker", "name", "signal", "score", "close", "ret5", "ret20"]].copy()
    save_df.columns = ["종목코드", "종목명", "신호", "점수", "종가", "5일수익률", "20일수익률"]

    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        save_df.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n  결과 저장: {out}")
    else:
        default_out = Path("results") / f"kospi200_scan_{datetime.today().strftime('%Y%m%d_%H%M')}.csv"
        default_out.parent.mkdir(parents=True, exist_ok=True)
        save_df.to_csv(default_out, index=False, encoding="utf-8-sig")
        print(f"\n  결과 저장: {default_out}")

    sep("═")
    print(f"  완료: {datetime.now().strftime('%H:%M:%S')}")
    sep("═")
    print()

    return df


def _print_table(df: pd.DataFrame):
    if df.empty:
        print("  (해당 종목 없음)")
        return
    print(f"  {'순위':>3}  {'코드':<8} {'종목명':<14} {'신호':<4} {'점수':>4}  {'종가':>10}  {'5일':>7}  {'20일':>7}")
    sep("·")
    for rank, (_, row) in enumerate(df.iterrows(), 1):
        sig_label = {"BUY": "★매수", "SELL": "▼매도", "HOLD": "─관망"}.get(row["signal"], row["signal"])
        print(
            f"  {rank:>3}  {row['ticker']:<8} {row['name']:<14}"
            f" {sig_label:<5} {row['score']:>4}"
            f"  {row['close']:>10,.0f}"
            f"  {row['ret5']:>+6.1f}%"
            f"  {row['ret20']:>+6.1f}%"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KOSPI 200 전종목 매수/매도 신호 스캐너")
    parser.add_argument("--days",     type=int,   default=DEFAULT_DAYS, help="분석 기간 (일)")
    parser.add_argument("--top",      type=int,   default=DEFAULT_TOP,  help="상위 N개 출력")
    parser.add_argument("--out",      type=str,   default=None,         help="결과 CSV 저장 경로")
    parser.add_argument("--no-cache", action="store_true",              help="캐시 무시하고 새로 수집")
    args = parser.parse_args()

    scan(
        days      = args.days,
        top       = args.top,
        out       = args.out,
        use_cache = not args.no_cache,
    )
