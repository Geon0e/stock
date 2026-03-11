"""
5분 간격 매도 시그널 모니터

워치리스트에 등록된 종목을 5분마다 감시하여
매도 시그널 발생 시 텔레그램 + 카카오톡으로 즉시 알림

실행:
    python sell_monitor.py              # 장중에만 동작 (09:00~15:30)
    python sell_monitor.py --all-day    # 시간 제한 없이 계속 동작
    python sell_monitor.py --interval 3 # 3분 간격으로 체크
    python sell_monitor.py --test       # 워치리스트 현황만 출력
    python sell_monitor.py --add 005930 삼성전자   # 워치리스트 직접 추가
    python sell_monitor.py --remove 005930          # 워치리스트에서 제거

매도 판단 기준:
    - 종합 점수 ≤ 35  (일반 SELL 기준 40보다 엄격)
    - SELL 전략 3개 이상 동시 발동
    - 동일 종목 4시간 내 중복 알림 방지
"""

import sys
import os
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── .env 로드 ─────────────────────────────────────────────────────────────────

def _load_env():
    env_path = ROOT_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

# ── 설정 ──────────────────────────────────────────────────────────────────────

INTERVAL_MIN     = 5        # 체크 간격 (분)
SELL_SCORE_LIMIT = 35       # 이 점수 이하면 매도 시그널 (일반 SELL 기준 40보다 엄격)
MIN_SELL_STRATS  = 3        # SELL 전략 최소 발동 개수
COOLDOWN_HOURS   = 4        # 동일 종목 알림 쿨다운 (시간)
DATA_DAYS        = 60       # OHLCV 데이터 수집 기간
MARKET_OPEN      = (9, 0)   # 장 시작 (시, 분)
MARKET_CLOSE     = (15, 30) # 장 마감


# ── 유틸 ──────────────────────────────────────────────────────────────────────

def _log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def is_market_hours() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:  # 토/일
        return False
    open_t  = now.replace(hour=MARKET_OPEN[0],  minute=MARKET_OPEN[1],  second=0, microsecond=0)
    close_t = now.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0, microsecond=0)
    return open_t <= now <= close_t


def _ret5(df) -> float:
    if len(df) >= 5:
        return (df["Close"].iloc[-1] / df["Close"].iloc[-5] - 1) * 100
    return 0.0


# ── 단일 종목 체크 ────────────────────────────────────────────────────────────

def check_one(ticker: str, name: str, market: str = "kospi200") -> dict | None:
    """
    종목 하나의 최신 데이터를 가져와 신호 평가.
    Returns None if data unavailable.
    Returns dict with signal/score/details/price/ret5/adx/regime.
    """
    from signals import evaluate

    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=DATA_DAYS + 30)).strftime("%Y-%m-%d")

    try:
        if market == "nasdaq100":
            from data.us_fetcher import get_ohlcv_us
            df = get_ohlcv_us(ticker, start, end, use_cache=False)
        else:
            from data.crawler import NaverFinanceCrawler
            crawler = NaverFinanceCrawler(request_delay=0.1, verify_ssl=False)
            df = crawler.get_ohlcv(ticker, start, end, use_cache=False)

        if df is None or df.empty or len(df) < 10:
            return None

        result = evaluate(df)
        return {
            "ticker":  ticker,
            "name":    name,
            "signal":  result["signal"],
            "score":   result["score"],
            "adx":     result.get("adx"),
            "regime":  result.get("regime", "중립"),
            "details": result["details"],
            "price":   df["Close"].iloc[-1],
            "ret5":    _ret5(df),
        }
    except Exception as e:
        _log(f"  [오류] {name}({ticker}): {e}")
        return None


# ── 매도 시그널 판단 ──────────────────────────────────────────────────────────

def is_sell_signal(result: dict) -> tuple[bool, list[str]]:
    """
    매도 판단:
      1) 종합 점수 ≤ SELL_SCORE_LIMIT (기본 35)
      2) SELL 신호 전략 수 ≥ MIN_SELL_STRATS (기본 3개)
    두 조건 모두 충족해야 알림 발송.

    Returns: (is_sell: bool, sell_reasons: list[str])
    """
    sell_details = [d for d in result["details"] if d["signal"] == "SELL"]
    sell_reasons = [f"{d['name']}({d['score']}점)" for d in sell_details]

    score_ok = result["score"] <= SELL_SCORE_LIMIT
    count_ok = len(sell_details) >= MIN_SELL_STRATS

    return (score_ok and count_ok), sell_reasons


# ── 알림 메시지 포맷 ──────────────────────────────────────────────────────────

def _sell_message_telegram(result: dict, entry_info: dict, sell_reasons: list[str]) -> str:
    entry_price = entry_info.get("price", 0)
    pnl = (result["price"] / entry_price - 1) * 100 if entry_price > 0 else 0
    pnl_str = f"{'▲' if pnl >= 0 else '▼'}{abs(pnl):.1f}%"

    adx_str = f"ADX={result['adx']:.1f}" if result["adx"] is not None else "ADX=N/A"

    lines = [
        f"🚨 <b>매도 시그널 발생</b>",
        f"",
        f"📌 <b>[{result['ticker']}] {result['name']}</b>",
        f"⚡ 종합 점수: <b>{result['score']}/100</b>",
        f"💰 현재가: <b>{result['price']:,.0f}원</b>  (등록 후 {pnl_str})",
        f"📊 시장 상태: {result['regime']} ({adx_str})",
        f"📉 5일 수익률: {result['ret5']:+.1f}%",
        f"{'─'*28}",
        f"🔴 <b>매도 신호 전략</b>",
    ]

    for d in result["details"]:
        emoji = "🔴" if d["signal"] == "SELL" else ("🟢" if d["signal"] == "BUY" else "⚪")
        bar_n = d["score"] // 10
        bar   = "█" * bar_n + "░" * (10 - bar_n)
        lines.append(f"{emoji} <b>{d['name']}</b>  [{bar}] {d['score']}점  ×{d['weight']:.1f}")
        lines.append(f"   └ {d['reason']}")

    lines += [
        f"{'─'*28}",
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"📋 등록일: {entry_info.get('added_at', '-')}",
    ]
    return "\n".join(lines)


def _sell_message_kakao(result: dict, entry_info: dict, sell_reasons: list[str]) -> str:
    entry_price = entry_info.get("price", 0)
    pnl = (result["price"] / entry_price - 1) * 100 if entry_price > 0 else 0
    pnl_str = f"{'▲' if pnl >= 0 else '▼'}{abs(pnl):.1f}%"

    adx_str = f"ADX={result['adx']:.1f}" if result["adx"] is not None else "ADX=N/A"
    reasons_short = " · ".join(d["name"] for d in result["details"] if d["signal"] == "SELL")

    return (
        f"🚨 매도 시그널 발생\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"[{result['ticker']}] {result['name']}\n"
        f"점수: {result['score']}/100  |  현재가: {result['price']:,.0f}원\n"
        f"등록 후 손익: {pnl_str}\n"
        f"5일 수익률: {result['ret5']:+.1f}%\n"
        f"시장: {result['regime']} ({adx_str})\n"
        f"매도 신호: {reasons_short}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )


# ── 알림 전송 ─────────────────────────────────────────────────────────────────

def _send_telegram(msg: str) -> bool:
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    try:
        import requests
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=15, verify=False,
        )
        return resp.ok
    except Exception as e:
        _log(f"  [텔레그램 오류] {e}")
        return False


def _send_kakao(msg: str) -> bool:
    import json as _json
    import requests

    access_token  = os.environ.get("KAKAO_ACCESS_TOKEN",  "")
    refresh_token = os.environ.get("KAKAO_REFRESH_TOKEN", "")
    rest_api_key  = os.environ.get("KAKAO_REST_API_KEY",  "")
    client_secret = os.environ.get("KAKAO_CLIENT_SECRET", "")

    if not access_token:
        return False

    template = {
        "object_type": "text",
        "text": msg[:1900],
        "link": {"web_url": "https://finance.naver.com",
                 "mobile_web_url": "https://finance.naver.com"},
    }

    for attempt in range(2):
        try:
            resp = requests.post(
                "https://kapi.kakao.com/v2/api/talk/memo/default/send",
                headers={"Authorization": f"Bearer {access_token}"},
                data={"template_object": _json.dumps(template, ensure_ascii=False)},
                verify=False, timeout=15,
            )
            data = resp.json()
            if data.get("result_code") == 0:
                return True
            if resp.status_code in (401, 403) and attempt == 0 and refresh_token:
                # 토큰 갱신 시도
                ref_data = {"grant_type": "refresh_token",
                            "client_id": rest_api_key,
                            "refresh_token": refresh_token}
                if client_secret:
                    ref_data["client_secret"] = client_secret
                r2 = requests.post("https://kauth.kakao.com/oauth/token",
                                   data=ref_data, verify=False, timeout=15)
                r2j = r2.json()
                if "access_token" in r2j:
                    access_token = r2j["access_token"]
                    os.environ["KAKAO_ACCESS_TOKEN"] = access_token
                    # .env 업데이트
                    _update_env_key("KAKAO_ACCESS_TOKEN", access_token)
                    if "refresh_token" in r2j:
                        _update_env_key("KAKAO_REFRESH_TOKEN", r2j["refresh_token"])
                    continue
        except Exception as e:
            _log(f"  [카카오 오류] {e}")
            return False
    return False


def _update_env_key(key: str, value: str):
    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        return
    lines = env_path.read_text(encoding="utf-8").splitlines()
    new_lines, found = [], False
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s and s.split("=", 1)[0].strip() == key:
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def send_alert(result: dict, entry_info: dict, sell_reasons: list[str]) -> str:
    """
    텔레그램 + 카카오톡 동시 전송.
    Returns: 'both' | 'telegram' | 'kakao' | 'none'
    """
    tg_ok = _send_telegram(_sell_message_telegram(result, entry_info, sell_reasons))
    kk_ok = _send_kakao(_sell_message_kakao(result, entry_info, sell_reasons))

    if tg_ok and kk_ok:
        return "both"
    elif tg_ok:
        return "telegram"
    elif kk_ok:
        return "kakao"
    return "none"


# ── 메인 체크 루프 ────────────────────────────────────────────────────────────

def run_check():
    """워치리스트 전체 1회 체크"""
    from notifications.watchlist import load, recently_alerted, log_alert

    watchlist = load()
    if not watchlist:
        _log("워치리스트가 비어 있습니다. BUY 신호 전송 후 자동 등록됩니다.")
        return

    _log(f"워치리스트 {len(watchlist)}개 종목 체크 시작")
    alerted = 0

    for ticker, info in watchlist.items():
        name   = info.get("name", ticker)
        market = info.get("market", "kospi200")
        result = check_one(ticker, name, market)
        if result is None:
            _log(f"  {name}({ticker}): 데이터 수집 실패")
            continue

        is_sell, sell_reasons = is_sell_signal(result)
        status = f"점수={result['score']}  신호={result['signal']}  SELL전략={len([d for d in result['details'] if d['signal']=='SELL'])}개"
        _log(f"  {name}({ticker}): {status}")

        if not is_sell:
            continue

        if recently_alerted(ticker, COOLDOWN_HOURS):
            _log(f"  ↳ {name}: 쿨다운 중 ({COOLDOWN_HOURS}시간 내 알림 발송됨, 스킵)")
            continue

        _log(f"  ↳ 🚨 매도 시그널! {name} 점수={result['score']}  알림 전송 중...")
        channel = send_alert(result, info, sell_reasons)

        log_alert(
            ticker    = ticker,
            name      = name,
            signal    = result["signal"],
            score     = result["score"],
            adx       = result["adx"],
            regime    = result["regime"],
            price     = result["price"],
            ret5      = result["ret5"],
            sell_reasons = sell_reasons,
            channel   = channel,
        )
        _log(f"  ↳ 전송 완료 ({channel})")
        alerted += 1

    _log(f"체크 완료. 알림 발송: {alerted}건")


def _wait_for_market_open():
    """장 시작(09:00) 전이면 대기"""
    now    = datetime.now()
    open_t = now.replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0, microsecond=0)
    if now < open_t:
        wait_sec = (open_t - now).total_seconds()
        _log(f"장 시작까지 대기 중... ({open_t.strftime('%H:%M')} 개장, {wait_sec/60:.0f}분 후)")
        time.sleep(wait_sec)


def run_monitor(interval_min: int = INTERVAL_MIN, all_day: bool = False):
    """
    5분 간격 루프. 장마감(15:30)이 되면 자동 종료.
    all_day=True 이면 시간 제한 없이 계속 동작.
    장 시작 전 실행되면 09:00까지 자동 대기.
    """
    if not all_day:
        _wait_for_market_open()

    if all_day:
        _log(f"매도 모니터 시작 (간격={interval_min}분 | 시간제한 없음)")
    else:
        _log(f"매도 모니터 시작 (간격={interval_min}분 | 장마감 15:30 자동 종료)")
    _log(f"매도 기준: 점수≤{SELL_SCORE_LIMIT} AND SELL전략≥{MIN_SELL_STRATS}개")
    _log(f"알림 쿨다운: {COOLDOWN_HOURS}시간 | Ctrl+C로 강제 종료")
    print()

    while True:
        now     = datetime.now()
        close_t = now.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1],
                              second=0, microsecond=0)

        # 장 마감 시각 도달 → 정상 종료 (all_day 아닐 때만)
        if not all_day and now >= close_t:
            _log(f"장 마감 ({MARKET_CLOSE[0]:02d}:{MARKET_CLOSE[1]:02d}). 모니터를 종료합니다.")
            break

        try:
            run_check()
        except Exception as e:
            _log(f"[오류] 체크 중 예외 발생: {e}")

        # 다음 체크 시각
        next_run = now + timedelta(minutes=interval_min)
        if not all_day:
            next_run = min(next_run, close_t)
        wait_sec = (next_run - datetime.now()).total_seconds()
        if wait_sec > 0:
            _log(f"다음 체크: {next_run.strftime('%H:%M:%S')} ({wait_sec:.0f}초 후)")
            time.sleep(wait_sec)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="5분 간격 매도 시그널 모니터")
    parser.add_argument("--interval",  type=int, default=INTERVAL_MIN,
                        help=f"체크 간격 분 (기본={INTERVAL_MIN})")
    parser.add_argument("--all-day",   action="store_true",
                        help="시간 제한 없이 계속 동작 (장 외 시간 포함)")
    parser.add_argument("--test",      action="store_true",
                        help="워치리스트 현황 출력 후 1회만 체크")
    parser.add_argument("--add",       nargs=2, metavar=("TICKER", "NAME"),
                        help="워치리스트에 종목 직접 추가  예: --add 005930 삼성전자")
    parser.add_argument("--remove",    metavar="TICKER",
                        help="워치리스트에서 종목 제거")
    parser.add_argument("--list",      action="store_true",
                        help="워치리스트 목록 출력")
    args = parser.parse_args()

    from notifications.watchlist import add, remove, list_all

    if args.add:
        add(args.add[0], args.add[1], score=0, price=0.0)
        sys.exit(0)

    if args.remove:
        remove(args.remove)
        sys.exit(0)

    if args.list or args.test:
        items = list_all()
        print(f"\n워치리스트 ({len(items)}개)")
        print("─" * 50)
        for item in items:
            print(f"  {item['ticker']}  {item['name']:<18}  "
                  f"점수={item.get('score','-')}  "
                  f"등록가={item.get('price','-'):,.0f}원  "
                  f"등록일={item.get('added_at','-')}")
        print()
        if args.test and items:
            run_check()
        sys.exit(0)

    run_monitor(interval_min=args.interval, all_day=args.all_day)
