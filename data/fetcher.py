"""
한국 주식 데이터 수집 모듈
pykrx, FinanceDataReader 사용
"""

import os
import time
import urllib3
import pandas as pd
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from pathlib import Path

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

try:
    from pykrx import stock as krx
    PYKRX_AVAILABLE = True
except ImportError:
    PYKRX_AVAILABLE = False

try:
    import FinanceDataReader as fdr
    FDR_AVAILABLE = True
except ImportError:
    FDR_AVAILABLE = False

try:
    from data.naver_crawler import NaverFinanceCrawler as _NaverCrawler
    NAVER_AVAILABLE = True
except ImportError:
    NAVER_AVAILABLE = False


DATA_DIR = Path(__file__).parent


def get_ohlcv(ticker: str, start: str, end: str = None, source: str = "auto") -> pd.DataFrame:
    """
    주식 OHLCV 데이터 가져오기

    Args:
        ticker: 종목코드 (예: '005930' 삼성전자)
        start: 시작일 'YYYY-MM-DD'
        end: 종료일 'YYYY-MM-DD' (기본: 오늘)
        source: 'pykrx' | 'fdr' | 'naver' | 'auto'

    Returns:
        DataFrame with columns: Open, High, Low, Close, Volume
    """
    if end is None:
        end = datetime.today().strftime("%Y-%m-%d")

    # CSV 캐시 확인
    cache_path = DATA_DIR / f"{ticker}_{start}_{end}.csv"
    if cache_path.exists():
        print(f"[캐시] {ticker} 데이터 로드")
        df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
        return df

    if source == "auto":
        if PYKRX_AVAILABLE:
            source = "pykrx"
        elif FDR_AVAILABLE:
            source = "fdr"
        elif NAVER_AVAILABLE:
            source = "naver"

    if source == "pykrx" and PYKRX_AVAILABLE:
        df = _fetch_pykrx(ticker, start, end)
    elif source == "fdr" and FDR_AVAILABLE:
        df = _fetch_fdr(ticker, start, end)
    elif source == "naver":
        df = _fetch_naver(ticker, start, end)
    else:
        raise ImportError(
            "pykrx, FinanceDataReader, 또는 requests/beautifulsoup4가 필요합니다.\n"
            "pip install pykrx FinanceDataReader\n"
            "pip install requests beautifulsoup4"
        )

    if df is not None and not df.empty:
        df.to_csv(cache_path)

    return df


def _fetch_pykrx(ticker: str, start: str, end: str) -> pd.DataFrame:
    """pykrx로 데이터 가져오기"""
    print(f"[pykrx] {ticker} 데이터 다운로드 중...")
    start_fmt = start.replace("-", "")
    end_fmt = end.replace("-", "")

    df = krx.get_market_ohlcv_by_date(start_fmt, end_fmt, ticker)
    if df.empty:
        raise ValueError(f"종목 {ticker}의 데이터가 없습니다.")

    df.index = pd.to_datetime(df.index)
    # pykrx 버전에 따라 컬럼 수가 다름 (6개 또는 7개)
    col_map = {
        6: ["Open", "High", "Low", "Close", "Volume", "Changes"],
        7: ["Open", "High", "Low", "Close", "Volume", "Turnover", "Changes"],
    }
    df.columns = col_map.get(len(df.columns), df.columns)
    df = df[["Open", "High", "Low", "Close", "Volume"]]
    return df


def _fetch_naver(ticker: str, start: str, end: str) -> pd.DataFrame:
    """네이버 증권으로 데이터 가져오기"""
    crawler = _NaverCrawler()
    return crawler.get_ohlcv(ticker, start, end, use_cache=False)


def _fetch_fdr(ticker: str, start: str, end: str) -> pd.DataFrame:
    """FinanceDataReader로 데이터 가져오기"""
    print(f"[FDR] {ticker} 데이터 다운로드 중...")
    df = fdr.DataReader(ticker, start, end)
    if df.empty:
        raise ValueError(f"종목 {ticker}의 데이터가 없습니다.")

    df.index = pd.to_datetime(df.index)
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col not in df.columns:
            raise ValueError(f"컬럼 {col}이 없습니다.")
    return df[["Open", "High", "Low", "Close", "Volume"]]


def get_kospi200_tickers(use_cache: bool = True) -> pd.DataFrame:
    """
    KOSPI 200 구성 종목 목록 조회 (네이버 증권 크롤링)

    Returns:
        DataFrame  columns=[Code, Name]  (200개 종목)
    """
    cache_path = DATA_DIR / "kospi200_tickers.csv"

    if use_cache and cache_path.exists():
        age_hours = (datetime.now().timestamp() - cache_path.stat().st_mtime) / 3600
        if age_hours < 24:
            df = pd.read_csv(cache_path, dtype={"Code": str})
            print(f"[캐시] KOSPI 200 종목 목록 로드 ({len(df)}개)")
            return df

    print("[네이버증권] KOSPI 200 구성종목 조회 중...")
    tickers = []

    for page in range(1, 25):
        url = f"https://finance.naver.com/sise/entryJongmok.nhn?code=KP200&page={page}"
        try:
            resp = requests.get(url, headers=_NAVER_HEADERS, verify=False, timeout=15)
            soup = BeautifulSoup(resp.content, "html.parser", from_encoding="euc-kr")
            links = soup.select("table.type_1 td a")
            if not links:
                break
            for a in links:
                href = a.get("href", "")
                if "code=" in href:
                    code = href.split("code=")[-1][:6]
                    name = a.text.strip()
                    if code and (code, name) not in tickers:
                        tickers.append((code, name))
            time.sleep(0.2)
        except Exception as e:
            print(f"[경고] page {page} 수집 실패: {e}")
            break

    df = pd.DataFrame(tickers, columns=["Code", "Name"])
    df.to_csv(cache_path, index=False)
    print(f"[완료] KOSPI 200 종목 {len(df)}개 수집")
    return df


def get_stock_list(market: str = "KOSPI") -> pd.DataFrame:
    """
    상장 종목 목록 가져오기

    Args:
        market: 'KOSPI' | 'KOSDAQ' | 'ALL'
    """
    if FDR_AVAILABLE:
        if market == "ALL":
            kospi = fdr.StockListing("KOSPI")[["Code", "Name"]]
            kosdaq = fdr.StockListing("KOSDAQ")[["Code", "Name"]]
            return pd.concat([kospi, kosdaq], ignore_index=True)
        return fdr.StockListing(market)[["Code", "Name"]]

    if PYKRX_AVAILABLE:
        today = datetime.today().strftime("%Y%m%d")
        if market in ("KOSPI", "KOSDAQ"):
            tickers = krx.get_market_ticker_list(today, market=market)
        else:
            tickers = krx.get_market_ticker_list(today, market="KOSPI") + \
                      krx.get_market_ticker_list(today, market="KOSDAQ")

        names = [krx.get_market_ticker_name(t) for t in tickers]
        return pd.DataFrame({"Code": tickers, "Name": names})

    raise ImportError("pykrx 또는 FinanceDataReader가 필요합니다.")


def get_multiple_ohlcv(tickers: list, start: str, end: str = None) -> dict:
    """여러 종목 데이터 일괄 수집"""
    result = {}
    for ticker in tickers:
        try:
            result[ticker] = get_ohlcv(ticker, start, end)
        except Exception as e:
            print(f"[경고] {ticker} 데이터 수집 실패: {e}")
    return result


def clear_cache():
    """캐시 파일 삭제"""
    for f in DATA_DIR.glob("*.csv"):
        f.unlink()
    print("캐시 삭제 완료")
