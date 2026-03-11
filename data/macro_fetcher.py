"""
매크로 지표 데이터 수집 모듈

- 미국 국채 금리 (2Y, 10Y) : FRED CSV API (무인증, observation_date 컬럼)
- DXY, Gold, WTI, VIX      : Yahoo Finance v8 Chart API
"""

import io
import ssl
import json
from datetime import datetime, timedelta

import pandas as pd
import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ── SSL 우회 세션 ────────────────────────────────────────────────────────

class _NoVerifyAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context(ciphers="DEFAULT:@SECLEVEL=0")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


def _session() -> requests.Session:
    s = requests.Session()
    s.mount("https://", _NoVerifyAdapter())
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/csv, */*",
    })
    return s


# ── 지표 정의 ────────────────────────────────────────────────────────────
#  source: "fred" | "yf"
#  up_bad: True → 상승이 주식시장에 부정적 (VIX·금리) → 빨간색

INSTRUMENTS = {
    "us2y":  {"name": "미국 2년물",  "unit": "%",  "source": "fred", "id": "DGS2",       "up_bad": True},
    "us10y": {"name": "미국 10년물", "unit": "%",  "source": "fred", "id": "DGS10",      "up_bad": True},
    "dxy":   {"name": "DXY",         "unit": "",   "source": "yf",   "id": "DX-Y.NYB",   "up_bad": False},
    "gold":  {"name": "Gold",        "unit": "$",  "source": "yf",   "id": "GC=F",       "up_bad": False},
    "wti":   {"name": "WTI",         "unit": "$",  "source": "yf",   "id": "CL=F",       "up_bad": False},
    "vix":   {"name": "VIX",         "unit": "",   "source": "yf",   "id": "^VIX",       "up_bad": True},
}


# ── 개별 소스 수집 ───────────────────────────────────────────────────────

def _fetch_fred(series_id: str, days: int = 60) -> pd.Series:
    """FRED 무인증 CSV API  (컬럼: observation_date, {series_id})"""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    resp = _session().get(url, verify=False, timeout=15)
    resp.raise_for_status()
    df = pd.read_csv(
        io.StringIO(resp.text),
        parse_dates=["observation_date"],
        index_col="observation_date",
    )
    series = pd.to_numeric(df.iloc[:, 0], errors="coerce").dropna()
    return series.tail(days)


def _fetch_yf(ticker: str, days: int = 60) -> pd.Series:
    """Yahoo Finance v8 Chart API → Close 시계열"""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?interval=1d&range={days}d"
    )
    resp = _session().get(url, verify=False, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]

    dates  = pd.to_datetime(timestamps, unit="s", utc=True).tz_convert("Asia/Seoul").tz_localize(None)
    series = pd.Series(closes, index=dates, dtype=float).dropna()
    series.index = series.index.normalize()
    return series.tail(days)


# ── 전체 수집 ────────────────────────────────────────────────────────────

def _empty(cfg: dict) -> dict:
    return {
        "name": cfg["name"], "unit": cfg["unit"], "up_bad": cfg["up_bad"],
        "value": None, "prev": None, "change": None, "change_pct": None,
        "series": pd.Series(dtype=float),
    }


def fetch_all(days: int = 60) -> dict:
    """
    모든 매크로 지표 수집

    Returns:
        {
          "us2y": {
            "name": str, "unit": str, "up_bad": bool,
            "value": float, "prev": float,
            "change": float, "change_pct": float,
            "series": pd.Series
          }, ...
        }
    """
    result = {}
    for key, cfg in INSTRUMENTS.items():
        try:
            if cfg["source"] == "fred":
                series = _fetch_fred(cfg["id"], days)
            else:
                series = _fetch_yf(cfg["id"], days)

            if series.empty or len(series) < 2:
                result[key] = _empty(cfg)
                continue

            value = float(series.iloc[-1])
            prev  = float(series.iloc[-2])
            chg   = value - prev
            chg_pct = (chg / prev * 100) if prev != 0 else 0.0

            result[key] = {
                "name":       cfg["name"],
                "unit":       cfg["unit"],
                "up_bad":     cfg["up_bad"],
                "value":      value,
                "prev":       prev,
                "change":     chg,
                "change_pct": chg_pct,
                "series":     series,
            }
        except Exception as e:
            print(f"[매크로] {key} 조회 실패: {e}")
            result[key] = _empty(cfg)

    return result
