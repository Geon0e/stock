"""
네이버 증권 크롤러 CLI

사용법:
    # 종목 기본 정보 + 현재가
    python naver_crawl.py info 005930

    # 일별 시세 수집 (CSV 저장)
    python naver_crawl.py ohlcv 005930 --start 2024-01-01 --end 2024-12-31

    # 여러 종목 한 번에
    python naver_crawl.py ohlcv 005930 000660 035420 --start 2024-01-01

    # 투자자별 거래동향
    python naver_crawl.py investor 005930 --start 2024-01-01

    # 시장 지수 (KOSPI/KOSDAQ)
    python naver_crawl.py index

    # 종목 검색
    python naver_crawl.py search 삼성
"""

import argparse
import io
import sys
from datetime import datetime
from pathlib import Path

# Windows 콘솔 UTF-8 출력 설정
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent))

from data.naver_crawler import NaverFinanceCrawler


def cmd_info(args):
    crawler = NaverFinanceCrawler(verify_ssl=False)
    for ticker in args.tickers:
        print(f"\n{'=' * 40}")
        info = crawler.get_stock_info(ticker)
        for k, v in info.items():
            print(f"  {k:15s}: {v}")


def cmd_ohlcv(args):
    crawler = NaverFinanceCrawler(verify_ssl=False)
    end = args.end or datetime.today().strftime("%Y-%m-%d")
    output_dir = Path("data")

    for ticker in args.tickers:
        df = crawler.get_ohlcv(ticker, args.start, end, use_cache=not args.no_cache)

        if df.empty:
            continue

        if args.save:
            out_path = output_dir / f"{ticker}_{args.start}_{end}_naver.csv"
            df.to_csv(out_path)
            print(f"  저장: {out_path}")

        if args.show:
            print(f"\n[{ticker}] 최근 5거래일")
            print(df.tail(5).to_string())


def cmd_investor(args):
    crawler = NaverFinanceCrawler(verify_ssl=False)
    end = args.end or datetime.today().strftime("%Y-%m-%d")

    for ticker in args.tickers:
        df = crawler.get_investor_trend(ticker, args.start, end)

        if df.empty:
            print(f"[{ticker}] 데이터 없음")
            continue

        print(f"\n[{ticker}] 투자자별 거래동향 최근 10거래일")
        print(df.tail(10).to_string())

        if args.save:
            out = Path("data") / f"{ticker}_investor_{args.start}_{end}.csv"
            df.to_csv(out)
            print(f"  저장: {out}")


def cmd_index(args):
    crawler = NaverFinanceCrawler(verify_ssl=False)
    indices = crawler.get_market_index()

    print("\n시장 지수 현황")
    print("-" * 40)
    for market, data in indices.items():
        val = data.get("value", "N/A")
        chg = data.get("change", "")
        pct = data.get("change_pct", "")
        print(f"  {market:8s}: {val:>10}  {chg}  ({pct})")


def cmd_search(args):
    crawler = NaverFinanceCrawler(verify_ssl=False)
    keyword = " ".join(args.keyword)
    df = crawler.search_stocks(keyword)

    if df.empty:
        print(f"'{keyword}' 검색 결과 없음")
        return

    print(f"\n'{keyword}' 검색 결과 ({len(df)}건)")
    print("-" * 60)
    print(df.to_string(index=False))


def main():
    parser = argparse.ArgumentParser(
        description="네이버 증권 크롤러",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # info
    p_info = sub.add_parser("info", help="종목 기본 정보 + 현재가")
    p_info.add_argument("tickers", nargs="+", help="종목코드 (예: 005930)")

    # ohlcv
    p_ohlcv = sub.add_parser("ohlcv", help="일별 시세(OHLCV) 수집")
    p_ohlcv.add_argument("tickers", nargs="+", help="종목코드")
    p_ohlcv.add_argument("--start", required=True, help="시작일 YYYY-MM-DD")
    p_ohlcv.add_argument("--end",   default=None,  help="종료일 YYYY-MM-DD (기본: 오늘)")
    p_ohlcv.add_argument("--save",  action="store_true", default=True,  help="CSV 저장 (기본 ON)")
    p_ohlcv.add_argument("--no-cache", action="store_true", help="캐시 무시하고 재수집")
    p_ohlcv.add_argument("--show",  action="store_true", help="터미널에 미리보기 출력")

    # investor
    p_inv = sub.add_parser("investor", help="투자자별 거래동향")
    p_inv.add_argument("tickers", nargs="+", help="종목코드")
    p_inv.add_argument("--start", default=None, help="시작일 YYYY-MM-DD (기본: 30일 전)")
    p_inv.add_argument("--end",   default=None, help="종료일 YYYY-MM-DD (기본: 오늘)")
    p_inv.add_argument("--save",  action="store_true", help="CSV 저장")

    # index
    sub.add_parser("index", help="KOSPI/KOSDAQ 현재 지수")

    # search
    p_search = sub.add_parser("search", help="종목 검색")
    p_search.add_argument("keyword", nargs="+", help="검색어")

    args = parser.parse_args()

    dispatch = {
        "info":     cmd_info,
        "ohlcv":    cmd_ohlcv,
        "investor": cmd_investor,
        "index":    cmd_index,
        "search":   cmd_search,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
