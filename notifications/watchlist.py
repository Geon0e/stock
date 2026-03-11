"""
워치리스트 관리 모듈

BUY 신호 전송 시 자동 등록 → 매도 모니터가 5분마다 감시
저장 위치: notifications/watchlist.json
알림 이력: notifications/alert_log.csv
"""

import json
import csv
from pathlib import Path
from datetime import datetime

_DIR           = Path(__file__).parent
WATCHLIST_PATH = _DIR / "watchlist.json"
ALERT_LOG_PATH = _DIR / "alert_log.csv"

_LOG_HEADER = ["datetime", "ticker", "name", "signal", "score",
               "adx", "regime", "price", "ret5", "sell_reasons", "channel"]


# ── 워치리스트 ────────────────────────────────────────────────────────────────

def load() -> dict:
    """워치리스트 로드. {ticker: {name, score, price, added_at}}"""
    if not WATCHLIST_PATH.exists():
        return {}
    try:
        return json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict):
    WATCHLIST_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def add(ticker: str, name: str, score: int, price: float, market: str = "kospi200"):
    """종목 추가 (이미 있으면 업데이트)"""
    data = load()
    data[ticker] = {
        "name":     name,
        "score":    score,
        "price":    price,
        "market":   market,
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save(data)
    print(f"  [워치리스트] 추가: {name} ({ticker})  점수={score}")


def remove(ticker: str):
    """종목 제거"""
    data = load()
    if ticker in data:
        name = data[ticker].get("name", ticker)
        del data[ticker]
        _save(data)
        print(f"  [워치리스트] 제거: {name} ({ticker})")


def add_from_df(df, market: str = "kospi200"):
    """스캔 결과 DataFrame에서 BUY 종목 전체를 워치리스트에 추가"""
    if df is None or df.empty:
        return
    buy_df = df[df["signal"] == "BUY"]
    for _, row in buy_df.iterrows():
        add(row["ticker"], row["name"], int(row["score"]), float(row["close"]), market)
    print(f"  [워치리스트] BUY {len(buy_df)}개 종목 등록 완료")


def list_all() -> list[dict]:
    """워치리스트 전체 출력용 리스트"""
    data = load()
    return [{"ticker": k, **v} for k, v in data.items()]


# ── 알림 이력 ─────────────────────────────────────────────────────────────────

def _ensure_log():
    if not ALERT_LOG_PATH.exists():
        with open(ALERT_LOG_PATH, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerow(_LOG_HEADER)


def log_alert(ticker: str, name: str, signal: str, score: int,
              adx, regime: str, price: float, ret5: float,
              sell_reasons: list[str], channel: str):
    """알림 이력 CSV에 기록"""
    _ensure_log()
    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ticker, name, signal, score,
        round(adx, 1) if adx is not None else "",
        regime, round(price, 2), round(ret5, 2),
        " · ".join(sell_reasons),
        channel,
    ]
    with open(ALERT_LOG_PATH, "a", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerow(row)


def recently_alerted(ticker: str, within_hours: float = 4.0) -> bool:
    """최근 N시간 내에 같은 종목 알림을 보냈는지 확인"""
    if not ALERT_LOG_PATH.exists():
        return False
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(hours=within_hours)
    try:
        with open(ALERT_LOG_PATH, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("ticker") == ticker:
                    try:
                        t = datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S")
                        if t >= cutoff:
                            return True
                    except ValueError:
                        pass
    except Exception:
        pass
    return False
