"""
네이버 증권 크롤러
https://finance.naver.com 에서 주식 데이터를 수집합니다.

사용 예시:
    from data.crawler import NaverFinanceCrawler

    crawler = NaverFinanceCrawler()

    # OHLCV 일별 시세
    df = crawler.get_ohlcv("005930", "2024-01-01", "2024-12-31")

    # 현재가 조회
    info = crawler.get_stock_info("005930")

    # 종목 검색
    results = crawler.search_stocks("삼성")

    # 투자자별 거래동향
    trend = crawler.get_investor_trend("005930", "2024-01-01")
"""

import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://finance.naver.com/",
}

BASE_URL = "https://finance.naver.com"
DATA_DIR = Path(__file__).parent


class NaverFinanceCrawler:
    """네이버 증권 크롤러"""

    def __init__(
        self,
        request_delay: float = 0.3,
        timeout: int = 10,
        verify_ssl: bool = True,
    ):
        """
        Args:
            request_delay: 요청 간 대기 시간(초) - 서버 부하 방지
            timeout: 요청 타임아웃(초)
            verify_ssl: SSL 인증서 검증 여부 (프록시 환경에서는 False 사용)
        """
        self.delay = request_delay
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

        if not verify_ssl:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            # pykrx 등 외부 라이브러리의 requests 호출도 SSL 검증 비활성화
            _original_send = requests.Session.send
            def _send_no_verify(self, *args, **kwargs):
                kwargs["verify"] = False
                return _original_send(self, *args, **kwargs)
            requests.Session.send = _send_no_verify

    def _get(self, url: str) -> BeautifulSoup:
        """GET 요청 후 BeautifulSoup 반환"""
        resp = self.session.get(url, timeout=self.timeout, verify=self.verify_ssl)
        resp.raise_for_status()
        return BeautifulSoup(resp.content, "html.parser", from_encoding="euc-kr")

    # ------------------------------------------------------------------ #
    # 종목 정보 / 현재가
    # ------------------------------------------------------------------ #

    def get_stock_info(self, ticker: str) -> dict:
        """
        종목 기본 정보 + 현재가 조회

        Returns:
            dict: ticker, name, current_price, change, change_pct,
                  open, high, low, volume
        """
        url = f"{BASE_URL}/item/main.nhn?code={ticker}"
        soup = self._get(url)

        info = {"ticker": ticker}

        # 종목명
        h2 = soup.select_one("div.wrap_company h2 a")
        if h2:
            info["name"] = h2.text.strip()

        # 현재가: <p class="no_today"> ... <span class="blind">187,900</span>
        no_today = soup.select_one("p.no_today")
        if no_today:
            blind = no_today.select_one("span.blind")
            if blind:
                try:
                    info["current_price"] = int(blind.text.strip().replace(",", ""))
                except ValueError:
                    pass

        # 전일 대비 / 등락률: <p class="no_exday">
        no_exday = soup.select_one("p.no_exday")
        if no_exday:
            ems = no_exday.select("em")
            # 첫 번째 em = 전일대비 변화액
            if len(ems) >= 1:
                blind = ems[0].select_one("span.blind")
                if blind:
                    direction = "+" if "no_up" in ems[0].get("class", []) else "-"
                    info["change"] = direction + blind.text.strip()
            # 두 번째 em = 등락률(%)
            if len(ems) >= 2:
                blind = ems[1].select_one("span.blind")
                if blind:
                    pct_text = blind.text.strip()
                    direction = "+" if "no_up" in ems[1].get("class", []) else "-"
                    info["change_pct"] = direction + pct_text + "%"

        # 시가/고가/저가/거래량: table.no_info에서 라벨(sptxt 클래스) 기반 파싱
        # 네이버는 CSS 스프라이트 라벨을 사용하므로 한글 텍스트가 있는 경우만 처리
        no_info = soup.select_one("table.no_info")
        if no_info:
            label_to_key = {
                "시가": "open", "고가": "high", "저가": "low",
                "거래량": "volume", "전일종가": "prev_close",
            }
            for td in no_info.select("td"):
                label_el = td.select_one("span.sptxt")
                val_el = td.select_one("em span.blind")
                if not label_el or not val_el:
                    continue
                label_text = label_el.text.strip()
                key = label_to_key.get(label_text)
                if key:
                    val = val_el.text.strip().replace(",", "")
                    try:
                        info[key] = int(val)
                    except ValueError:
                        info[key] = val

        time.sleep(self.delay)
        return info

    # ------------------------------------------------------------------ #
    # 일별 시세 (OHLCV)
    # ------------------------------------------------------------------ #

    def get_ohlcv(
        self,
        ticker: str,
        start: str,
        end: str = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        네이버 증권 일별 시세(OHLCV) 수집

        Args:
            ticker: 종목코드 (예: '005930')
            start:  시작일 'YYYY-MM-DD'
            end:    종료일 'YYYY-MM-DD' (기본: 오늘)
            use_cache: CSV 캐시 사용 여부

        Returns:
            DataFrame  index=Date(DatetimeIndex),
                       columns=[Open, High, Low, Close, Volume]
        """
        if end is None:
            end = datetime.today().strftime("%Y-%m-%d")

        # 캐시 확인
        cache_path = DATA_DIR / f"{ticker}_{start}_{end}_naver.csv"
        if use_cache and cache_path.exists():
            print(f"[캐시] {ticker} 네이버 데이터 로드")
            return pd.read_csv(cache_path, index_col=0, parse_dates=True)

        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")

        all_rows = []
        page = 1

        print(f"[네이버증권] {ticker} 일별 시세 수집 중...")

        while True:
            url = f"{BASE_URL}/item/sise_day.nhn?code={ticker}&page={page}"
            soup = self._get(url)
            rows = soup.select("table.type2 tr")

            if not rows:
                break

            page_data = []
            for row in rows:
                cols = row.select("td")
                if len(cols) < 7:
                    continue

                date_text = cols[0].text.strip()
                if not date_text or "." not in date_text:
                    continue

                try:
                    date = datetime.strptime(date_text, "%Y.%m.%d")
                    close  = int(cols[1].text.strip().replace(",", ""))
                    open_  = int(cols[3].text.strip().replace(",", ""))
                    high   = int(cols[4].text.strip().replace(",", ""))
                    low    = int(cols[5].text.strip().replace(",", ""))
                    volume = int(cols[6].text.strip().replace(",", ""))

                    page_data.append({
                        "Date": date,
                        "Open": open_,
                        "High": high,
                        "Low": low,
                        "Close": close,
                        "Volume": volume,
                    })
                except (ValueError, IndexError):
                    continue

            if not page_data:
                break

            earliest = min(r["Date"] for r in page_data)
            filtered = [r for r in page_data if start_dt <= r["Date"] <= end_dt]
            all_rows.extend(filtered)

            # 시작일보다 이전 날짜가 나오면 수집 종료
            if earliest < start_dt:
                break

            page += 1
            time.sleep(self.delay)

        if not all_rows:
            print(f"[경고] {ticker}: 해당 기간에 데이터가 없습니다.")
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

        df = pd.DataFrame(all_rows)
        df = df.sort_values("Date").set_index("Date")
        df.index = pd.to_datetime(df.index)

        # 캐시 저장
        if use_cache:
            df.to_csv(cache_path)

        print(
            f"[완료] {ticker}: {len(df)}거래일 수집 "
            f"({df.index[0].date()} ~ {df.index[-1].date()})"
        )
        return df

    # ------------------------------------------------------------------ #
    # 투자자별 거래동향
    # ------------------------------------------------------------------ #

    def get_investor_trend(
        self,
        ticker: str,
        start: str = None,
        end: str = None,
    ) -> pd.DataFrame:
        """
        투자자별 거래동향 수집 (개인 / 외국인 / 기관)

        Args:
            ticker: 종목코드
            start:  시작일 'YYYY-MM-DD' (기본: 30일 전)
            end:    종료일 'YYYY-MM-DD' (기본: 오늘)

        Returns:
            DataFrame  columns=[Close, Individual, Foreign, Institution]
        """
        if end is None:
            end = datetime.today().strftime("%Y-%m-%d")
        if start is None:
            start = (datetime.today() - timedelta(days=30)).strftime("%Y-%m-%d")

        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")

        all_rows = []
        page = 1

        print(f"[네이버증권] {ticker} 투자자별 거래동향 수집 중...")

        while True:
            url = f"{BASE_URL}/item/frgn.nhn?code={ticker}&page={page}"
            soup = self._get(url)

            # 외국인/기관 순매매 테이블 (summary 속성으로 특정)
            target_table = soup.find(
                "table",
                summary=lambda s: s and "외국인" in s and "기관" in s,
            )
            if not target_table:
                break

            rows = target_table.select("tr")
            page_data = []

            for row in rows:
                cols = row.select("td")
                if len(cols) < 6:
                    continue

                date_text = cols[0].text.strip()
                if not date_text or "." not in date_text:
                    continue

                def _parse(s: str) -> int:
                    s = s.strip().replace(",", "").replace("+", "").replace("%", "")
                    s = "".join(c for c in s if c.isdigit() or c == "-")
                    return int(s) if s and s != "-" else 0

                try:
                    date = datetime.strptime(date_text, "%Y.%m.%d")
                    page_data.append({
                        "Date":        date,
                        "Close":       _parse(cols[1].text),
                        "Volume":      _parse(cols[4].text),
                        "Institution": _parse(cols[5].text),
                        "Foreign":     _parse(cols[6].text) if len(cols) > 6 else 0,
                    })
                except (ValueError, IndexError):
                    continue

            if not page_data:
                break

            earliest = min(r["Date"] for r in page_data)
            all_rows.extend([r for r in page_data if start_dt <= r["Date"] <= end_dt])

            if earliest < start_dt:
                break

            page += 1
            time.sleep(self.delay)

        if not all_rows:
            return pd.DataFrame()

        df = pd.DataFrame(all_rows)
        df = df.sort_values("Date").set_index("Date")
        df.index = pd.to_datetime(df.index)

        print(f"[완료] {ticker}: 투자자 동향 {len(df)}거래일")
        return df

    # ------------------------------------------------------------------ #
    # 시장 지수
    # ------------------------------------------------------------------ #

    def get_market_index(self) -> dict:
        """
        KOSPI / KOSDAQ 현재 지수 조회

        Returns:
            dict: {
              "KOSPI":  {"value": ..., "change": ..., "change_pct": ...},
              "KOSDAQ": {"value": ..., "change": ..., "change_pct": ...},
            }
        """
        url = f"{BASE_URL}/sise/"
        soup = self._get(url)

        indices = {}
        for market in ("KOSPI", "KOSDAQ"):
            val = soup.select_one(f"#{market}_now")
            chg = soup.select_one(f"#{market}_change")
            pct = soup.select_one(f"#{market}_rate")

            indices[market] = {
                "value":      val.text.strip().replace(",", "") if val else None,
                "change":     chg.text.strip() if chg else None,
                "change_pct": pct.text.strip() if pct else None,
            }

        return indices

    # ------------------------------------------------------------------ #
    # 종목 검색
    # ------------------------------------------------------------------ #

    def search_stocks(self, keyword: str) -> pd.DataFrame:
        """
        종목명 또는 코드로 검색
        (네이버 증권 시세 목록에서 키워드 필터링)

        Args:
            keyword: 검색어 (예: '삼성', '005930')

        Returns:
            DataFrame  columns=[Code, Name, Market, Close]
        """
        results = []
        # sosok=0: KOSPI, sosok=1: KOSDAQ
        for sosok, market_name in (("0", "KOSPI"), ("1", "KOSDAQ")):
            page = 1
            while True:
                url = (
                    f"{BASE_URL}/sise/sise_market_sum.nhn"
                    f"?sosok={sosok}&page={page}"
                )
                soup = self._get(url)
                rows = soup.select("table.type_2 tbody tr")

                if not rows:
                    break

                found_any = False
                for row in rows:
                    cols = row.select("td")
                    if len(cols) < 2:
                        continue
                    link = cols[1].select_one("a")
                    if not link:
                        continue
                    code_match = re.search(r"code=(\d{6})", link.get("href", ""))
                    if not code_match:
                        continue
                    found_any = True
                    code = code_match.group(1)
                    name = link.text.strip()
                    if keyword in name or keyword in code:
                        close_text = cols[2].text.strip().replace(",", "") if len(cols) > 2 else ""
                        try:
                            close = int(close_text)
                        except ValueError:
                            close = close_text
                        results.append({
                            "Code":   code,
                            "Name":   name,
                            "Market": market_name,
                            "Close":  close,
                        })

                if not found_any:
                    break
                page += 1

        return pd.DataFrame(results)

    # ------------------------------------------------------------------ #
    # 편의 메서드
    # ------------------------------------------------------------------ #

    def get_multiple_ohlcv(
        self,
        tickers: list,
        start: str,
        end: str = None,
        use_cache: bool = True,
    ) -> dict:
        """
        여러 종목 OHLCV 일괄 수집

        Returns:
            dict: {ticker: DataFrame, ...}
        """
        result = {}
        for ticker in tickers:
            try:
                result[ticker] = self.get_ohlcv(ticker, start, end, use_cache)
            except Exception as exc:
                print(f"[경고] {ticker} 수집 실패: {exc}")
        return result


# ------------------------------------------------------------------ #
# 모듈 레벨 편의 함수 (fetcher.py 스타일)
# ------------------------------------------------------------------ #

_crawler = None


def _get_crawler() -> NaverFinanceCrawler:
    global _crawler
    if _crawler is None:
        _crawler = NaverFinanceCrawler(verify_ssl=False)
    return _crawler


def get_ohlcv_naver(ticker: str, start: str, end: str = None) -> pd.DataFrame:
    """네이버 증권에서 OHLCV 수집 (fetcher.py와 동일한 인터페이스)"""
    return _get_crawler().get_ohlcv(ticker, start, end)


def get_stock_info_naver(ticker: str) -> dict:
    """네이버 증권에서 종목 정보 조회"""
    return _get_crawler().get_stock_info(ticker)


def get_market_index_naver() -> dict:
    """KOSPI/KOSDAQ 현재 지수 조회"""
    return _get_crawler().get_market_index()
