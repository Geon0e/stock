"""
미국 주식 데이터 수집 모듈 (stooq 사용)
- NASDAQ 100 구성종목 목록: Wikipedia 스크래핑
- OHLCV 데이터: stooq.com CSV
"""

import io
import ssl
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import urllib3
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DATA_DIR = Path(__file__).parent


# ── SSL 우회 세션 ─────────────────────────────────────────────────────────────

class _NoVerifyAdapter(HTTPAdapter):
    """자체서명 인증서 + 약한 DH키 환경 우회용 HTTPAdapter"""
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context(ciphers="DEFAULT:@SECLEVEL=0")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


def _make_session() -> requests.Session:
    s = requests.Session()
    s.mount("https://", _NoVerifyAdapter())
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    return s


# ── NASDAQ 100 종목 목록 ──────────────────────────────────────────────────────

def get_nasdaq100_tickers(use_cache: bool = True) -> pd.DataFrame:
    """
    NASDAQ 100 구성종목 목록 조회 (Wikipedia 스크래핑)

    Returns:
        DataFrame  columns=[Code, Name]  (100개 종목)
        Code: Yahoo Finance / stooq 티커 (예: AAPL, MSFT)
    """
    cache_path = DATA_DIR / "nasdaq100_tickers.csv"

    if use_cache and cache_path.exists():
        age_hours = (datetime.now().timestamp() - cache_path.stat().st_mtime) / 3600
        if age_hours < 24:
            df = pd.read_csv(cache_path)
            print(f"[캐시] NASDAQ 100 종목 목록 로드 ({len(df)}개)")
            return df

    print("[Wikipedia] NASDAQ 100 구성종목 조회 중...")
    session = _make_session()

    resp = session.get(
        "https://en.wikipedia.org/wiki/Nasdaq-100",
        verify=False,
        timeout=15,
    )
    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", {"id": "constituents"})

    if not table:
        raise RuntimeError("Wikipedia NASDAQ-100 테이블을 찾을 수 없습니다.")

    tickers = []
    for row in table.select("tbody tr"):
        cols = row.select("td")
        if len(cols) >= 2:
            ticker = cols[0].text.strip()  # 0번 컬럼 = 티커 심볼
            name   = cols[1].text.strip()  # 1번 컬럼 = 회사명
            if ticker:
                tickers.append({"Code": ticker, "Name": name})

    df = pd.DataFrame(tickers)
    df.to_csv(cache_path, index=False)
    print(f"[완료] NASDAQ 100 종목 {len(df)}개 수집")
    return df


# ── OHLCV 데이터 수집 ─────────────────────────────────────────────────────────

def get_ohlcv_us(
    ticker: str,
    start: str,
    end: str = None,
    use_cache: bool = True,
) -> pd.DataFrame:
    """
    미국 주식 OHLCV 데이터 수집 (stooq.com)

    Args:
        ticker: 티커 (예: 'AAPL', 'MSFT')
        start:  시작일 'YYYY-MM-DD'
        end:    종료일 'YYYY-MM-DD' (기본: 오늘)

    Returns:
        DataFrame  index=Date, columns=[Open, High, Low, Close, Volume]
    """
    if end is None:
        end = datetime.today().strftime("%Y-%m-%d")

    cache_path = DATA_DIR / f"{ticker}_{start}_{end}_stooq.csv"
    if use_cache and cache_path.exists():
        print(f"[캐시] {ticker} 데이터 로드")
        return pd.read_csv(cache_path, index_col=0, parse_dates=True)

    d1 = start.replace("-", "")
    d2 = end.replace("-", "")
    url = f"https://stooq.com/q/d/l/?s={ticker.lower()}.us&d1={d1}&d2={d2}&i=d"

    print(f"[stooq] {ticker} 데이터 다운로드 중...")
    session = _make_session()
    resp = session.get(url, verify=False, timeout=15)
    resp.raise_for_status()

    if "No data" in resp.text or len(resp.text.strip()) < 30:
        print(f"[경고] {ticker}: 데이터 없음")
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    df = pd.read_csv(io.StringIO(resp.text), parse_dates=["Date"], index_col="Date")
    df = df[["Open", "High", "Low", "Close", "Volume"]].sort_index()

    if use_cache and not df.empty:
        df.to_csv(cache_path)

    print(f"[완료] {ticker}: {len(df)}거래일 수집 ({df.index[0].date()} ~ {df.index[-1].date()})")
    return df


def get_multiple_ohlcv_us(
    tickers: list,
    start: str,
    end: str = None,
    use_cache: bool = True,
    delay: float = 0.3,
) -> dict:
    """여러 종목 OHLCV 일괄 수집"""
    result = {}
    for ticker in tickers:
        try:
            df = get_ohlcv_us(ticker, start, end, use_cache=use_cache)
            if not df.empty:
                result[ticker] = df
        except Exception as e:
            print(f"[경고] {ticker} 수집 실패: {e}")
        time.sleep(delay)
    return result
