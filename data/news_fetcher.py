"""
주식 뉴스 수집기
- 한국 주식: 네이버 금융 뉴스
- 미국 주식: Yahoo Finance RSS
"""

import requests
from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://finance.naver.com/",
}


def get_naver_news(ticker: str, max_articles: int = 5) -> list:
    """네이버 금융에서 종목 뉴스 헤드라인 수집"""
    url = (
        f"https://finance.naver.com/item/news_news.nhn"
        f"?code={ticker}&page=1&sm=title_entity_id.basic&clusterId="
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "lxml")

        articles = []
        for row in soup.select("table.type5 tr"):
            title_td = row.select_one("td.title")
            date_td  = row.select_one("td.date")
            if not title_td or not date_td:
                continue
            a = title_td.select_one("a")
            if not a:
                continue
            title = a.get_text(strip=True)
            if not title:
                continue
            articles.append({
                "title": title,
                "date":  date_td.get_text(strip=True),
            })
            if len(articles) >= max_articles:
                break
        return articles
    except Exception as e:
        print(f"[뉴스] 네이버 {ticker} 수집 실패: {e}")
        return []


def get_yahoo_news(ticker: str, max_articles: int = 5) -> list:
    """Yahoo Finance RSS에서 미국 주식 뉴스 헤드라인 수집"""
    url = (
        f"https://feeds.finance.yahoo.com/rss/2.0/headline"
        f"?s={ticker}&region=US&lang=en-US"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(resp.content, "xml")

        articles = []
        for item in soup.select("item")[:max_articles]:
            title   = item.find("title")
            pubdate = item.find("pubDate")
            if title and title.get_text(strip=True):
                articles.append({
                    "title": title.get_text(strip=True),
                    "date":  pubdate.get_text(strip=True) if pubdate else "",
                })
        return articles
    except Exception as e:
        print(f"[뉴스] Yahoo {ticker} 수집 실패: {e}")
        return []


def fetch_news(ticker: str, market: str, max_articles: int = 5) -> list:
    """종목 뉴스 수집 (시장에 따라 소스 자동 선택)"""
    if market == "kospi200":
        return get_naver_news(ticker, max_articles)
    else:
        return get_yahoo_news(ticker, max_articles)
