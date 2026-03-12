"""
리포트 기록 저장/조회 모듈

전송한 리포트를 날짜별 JSON 파일로 저장하고, 나중에 날짜별로 조회할 수 있습니다.
저장 위치: reports/YYYY-MM-DD_<market>.json
"""

import json
from datetime import datetime
from pathlib import Path

HISTORY_DIR = Path(__file__).parent


def _normalize_market(market: str) -> str:
    """'KOSPI 200' / 'kospi200' 등 → 'kospi200' or 'nasdaq100'"""
    m = market.lower().replace(" ", "")
    if "kospi" in m:
        return "kospi200"
    return "nasdaq100"


def save_report(market: str, channel: str, df, top_n: int = 5) -> Path:
    """
    전송한 리포트를 날짜별 JSON 파일에 추가 저장.
    같은 날 여러 번 전송하면 records 배열에 append됨.
    """
    import pandas as pd

    market = _normalize_market(market)
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%Y-%m-%d %H:%M:%S")

    buy_df  = df[df["signal"] == "BUY"]
    sell_df = df[df["signal"] == "SELL"]

    def _rows(sub, n):
        cols = ["ticker", "name", "score", "signal", "close", "ret5", "ret20"]
        if "open" in sub.columns:
            cols.insert(4, "open")
        rows = sub.head(n)[cols].round(2).to_dict("records")
        if "open" in sub.columns:
            for r in rows:
                r["open_price"] = r.pop("open")
        return rows

    record = {
        "sent_at": time_str,
        "channel": channel,
        "market":  market,
        "total":   len(df),
        "buy_count":  len(buy_df),
        "sell_count": len(sell_df),
        "avg_score":  round(float(df["score"].mean()), 1),
        "top_buy":    _rows(buy_df, top_n),
        "top_sell":   _rows(sell_df.sort_values("score"), top_n),
    }

    path = HISTORY_DIR / f"{date_str}_{market}.json"
    records = []
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                records = json.load(f)
        except Exception:
            records = []

    records.append(record)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    return path


def update_eod_performance(market: str, date_str: str = None) -> bool:
    """
    추천 종목의 당일 시가/종가/수익률을 업데이트.
    다음 날 아침 리포트 실행 시 전날 성과를 채워 넣음.

    저장 필드 (top_buy 각 항목에 추가):
        eod_open      : 당일 시가
        eod_close     : 당일 종가
        eod_pct_change: (종가 - 시가) / 시가 * 100
    """
    market = _normalize_market(market)
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    path = HISTORY_DIR / f"{date_str}_{market}.json"
    if not path.exists():
        return False

    records = load_file(path)
    if not records:
        return False

    updated = False
    for record in records:
        for item in record.get("top_buy", []):
            ticker = item.get("ticker")
            if not ticker or "eod_close" in item:
                continue  # 이미 업데이트됨
            try:
                if market == "kospi200":
                    from data.fetcher import get_ohlcv
                    df = get_ohlcv(ticker, date_str, date_str)
                else:
                    from data.us_fetcher import get_ohlcv_us
                    df = get_ohlcv_us(ticker, date_str, date_str, use_cache=False)

                if df is None or df.empty:
                    continue

                eod_open  = float(df["Open"].iloc[0])
                eod_close = float(df["Close"].iloc[0])
                pct = (eod_close - eod_open) / eod_open * 100 if eod_open != 0 else 0.0

                item["eod_open"]       = round(eod_open, 2)
                item["eod_close"]      = round(eod_close, 2)
                item["eod_pct_change"] = round(pct, 2)
                updated = True
            except Exception as e:
                print(f"[성과] {ticker} 업데이트 실패: {e}")

    if updated:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

    return updated


def list_report_files(market: str = None) -> list[Path]:
    """저장된 리포트 파일 목록 (최신순)"""
    pattern = f"*_{market}.json" if market else "*.json"
    files = sorted(HISTORY_DIR.glob(pattern), reverse=True)
    return [f for f in files if f.name != "__init__.py"]


def load_file(path: Path) -> list[dict]:
    """단일 날짜 파일의 records 반환"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


def load_all(market: str = None) -> list[dict]:
    """전체 기록 반환 (최신순)"""
    records = []
    for f in list_report_files(market):
        records.extend(load_file(f))
    return sorted(records, key=lambda r: r.get("sent_at", ""), reverse=True)


def available_dates(market: str = None) -> list[str]:
    """저장된 날짜 목록 (최신순)"""
    dates = []
    for f in list_report_files(market):
        parts = f.stem.split("_")
        if parts:
            dates.append(parts[0])
    return dates
