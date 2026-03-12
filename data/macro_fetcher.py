"""
매크로 지표 데이터 수집 모듈

- DXY, Gold, WTI, VIX : Yahoo Finance v8 Chart API
"""

import ssl
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
        "Accept": "application/json, */*",
    })
    return s


# ── 지표 정의 ────────────────────────────────────────────────────────────
#  up_bad: True → 상승이 주식시장에 부정적 (VIX) → 빨간색

INSTRUMENTS = {
    "dxy":  {"name": "DXY",  "unit": "",  "id": "DX-Y.NYB", "up_bad": False},
    "gold": {"name": "Gold", "unit": "$", "id": "GC=F",     "up_bad": False},
    "wti":  {"name": "WTI",  "unit": "$", "id": "CL=F",     "up_bad": False},
    "vix":  {"name": "VIX",  "unit": "",  "id": "^VIX",     "up_bad": True},
}


# ── 수집 ─────────────────────────────────────────────────────────────────

FETCH_TIMEOUT = 10  # seconds


def _fetch_yf(ticker: str, days: int = 60) -> pd.Series:
    """Yahoo Finance v8 Chart API → Close 시계열"""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?interval=1d&range={days}d"
    )
    resp = _session().get(url, verify=False, timeout=FETCH_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]

    dates  = pd.to_datetime(timestamps, unit="s", utc=True).tz_convert("Asia/Seoul").tz_localize(None)
    series = pd.Series(closes, index=dates, dtype=float).dropna()
    series.index = series.index.normalize()
    return series.tail(days)


def _empty(cfg: dict, unavailable: bool = False) -> dict:
    return {
        "name": cfg["name"], "unit": cfg["unit"], "up_bad": cfg["up_bad"],
        "value": None, "prev": None, "change": None, "change_pct": None,
        "series": pd.Series(dtype=float),
        "unavailable": unavailable,
    }


def fetch_all(days: int = 60) -> dict:
    """
    모든 매크로 지표 수집 (DXY, Gold, WTI, VIX)

    Returns:
        {
          "dxy": {
            "name": str, "unit": str, "up_bad": bool,
            "value": float, "prev": float,
            "change": float, "change_pct": float,
            "series": pd.Series
          }, ...  (gold, wti, vix 동일)
        }
    """
    result = {}
    for key, cfg in INSTRUMENTS.items():
        try:
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
        except requests.exceptions.Timeout:
            print(f"[매크로] {key} 타임아웃 - 건너뜀")
            result[key] = _empty(cfg, unavailable=True)
        except Exception as e:
            print(f"[매크로] {key} 조회 실패: {e}")
            result[key] = _empty(cfg)

    return result
