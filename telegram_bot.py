"""
텔레그램 KOSPI 200 신호 알림 봇

사용법:
    python telegram_bot.py              # 즉시 스캔 후 전송
    python telegram_bot.py --test       # 연결 테스트만
    python telegram_bot.py --days 90    # 90일 기준 스캔 후 전송
    python telegram_bot.py --schedule   # 매일 오전 8시 자동 전송
"""

import sys
import os
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent))

import requests
import pandas as pd

# ── .env 로드 ─────────────────────────────────────────────────────────────────
def _load_env():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── 설정 ─────────────────────────────────────────────────────────────────────
TOP_N         = 10    # 매수/매도 각 상위 N개
DEFAULT_DAYS  = 60
MAX_WORKERS   = 5
REQUEST_DELAY = 0.3


# ── 텔레그램 API ──────────────────────────────────────────────────────────────

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class TelegramBot:
    def __init__(self, token: str, chat_id: str):
        self.token   = token
        self.chat_id = chat_id
        self.api     = f"https://api.telegram.org/bot{token}"

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """메시지 전송 (4096자 초과 시 자동 분할)"""
        chunks = _split_message(text, 4000)
        ok = True
        for chunk in chunks:
            resp = requests.post(
                f"{self.api}/sendMessage",
                json={"chat_id": self.chat_id, "text": chunk, "parse_mode": parse_mode},
                timeout=15,
                verify=False,
            )
            if not resp.ok:
                print(f"[오류] 전송 실패: {resp.text}")
                ok = False
        return ok

    def test(self) -> bool:
        """연결 테스트"""
        resp = requests.get(f"{self.api}/getMe", timeout=10, verify=False)
        if resp.ok:
            name = resp.json()["result"].get("first_name", "Bot")
            print(f"[OK] 봇 연결 성공: {name}")
            return True
        print(f"[오류] 봇 연결 실패: {resp.text}")
        return False


def _split_message(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > limit:
            chunks.append(cur)
            cur = ""
        cur += line
    if cur:
        chunks.append(cur)
    return chunks


# ── 스캔 ─────────────────────────────────────────────────────────────────────

def _fetch_one(args):
    from data.naver_crawler import NaverFinanceCrawler
    from stock_signal import evaluate
    ticker, name, start, end, crawler, use_cache = args
    try:
        df = crawler.get_ohlcv(ticker, start, end, use_cache=use_cache)
        if df is None or df.empty or len(df) < 10:
            return None
        result = evaluate(df)
        return {
            "ticker":  ticker,
            "name":    name,
            "signal":  result["signal"],
            "score":   result["score"],
            "details": result["details"],
            "close":   df["Close"].iloc[-1],
            "ret5":    (df["Close"].iloc[-1] / df["Close"].iloc[-5]  - 1) * 100 if len(df) >= 5  else 0,
            "ret20":   (df["Close"].iloc[-1] / df["Close"].iloc[-20] - 1) * 100 if len(df) >= 20 else 0,
        }
    except Exception:
        return None


def run_scan(days: int = DEFAULT_DAYS, use_cache: bool = True) -> pd.DataFrame:
    from data.fetcher import get_kospi200_tickers
    from data.naver_crawler import NaverFinanceCrawler

    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=days + 30)).strftime("%Y-%m-%d")

    kospi200 = get_kospi200_tickers(use_cache=use_cache)
    crawler  = NaverFinanceCrawler(request_delay=REQUEST_DELAY, verify_ssl=False)

    task_args = [
        (row["Code"], row["Name"], start, end, crawler, use_cache)
        for _, row in kospi200.iterrows()
    ]

    results = []
    total   = len(task_args)
    done    = 0

    print(f"[스캔] KOSPI 200 {total}개 종목 분석 중...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch_one, arg): arg for arg in task_args}
        for future in as_completed(futures):
            done += 1
            res = future.result()
            if res:
                results.append(res)
            if done % 20 == 0 or done == total:
                print(f"  진행: {done}/{total} (성공 {len(results)})")

    if not results:
        return pd.DataFrame()

    return pd.DataFrame(results).sort_values("score", ascending=False).reset_index(drop=True)


# ── 메시지 포맷 ───────────────────────────────────────────────────────────────

def _ret_emoji(v: float) -> str:
    return "🔺" if v > 0 else "🔻"


def format_summary(df: pd.DataFrame, days: int) -> str:
    """요약 메시지 (헤더 + 통계)"""
    buy_n  = len(df[df["signal"] == "BUY"])
    sell_n = len(df[df["signal"] == "SELL"])
    hold_n = len(df[df["signal"] == "HOLD"])
    total  = len(df)
    avg    = df["score"].mean()
    now    = datetime.now().strftime("%Y-%m-%d %H:%M")

    return (
        f"📈 <b>KOSPI 200 신호 스캐너</b>\n"
        f"🕐 {now}  |  분석 기간 최근 {days}일\n"
        f"{'─'*30}\n"
        f"📊 분석 종목: <b>{total}개</b>\n"
        f"🟢 매수 신호: <b>{buy_n}개</b>  ({buy_n/total*100:.0f}%)\n"
        f"🔴 매도 신호: <b>{sell_n}개</b>  ({sell_n/total*100:.0f}%)\n"
        f"⚪ 관망:      <b>{hold_n}개</b>  ({hold_n/total*100:.0f}%)\n"
        f"📈 평균 점수: <b>{avg:.1f}점</b>\n"
    )


def format_buy_list(df: pd.DataFrame, top_n: int) -> str:
    """매수 추천 메시지"""
    buy_df = df[df["signal"] == "BUY"].head(top_n)
    if buy_df.empty:
        return "🟢 <b>매수 추천</b>\n해당 종목 없음\n"

    lines = [f"🟢 <b>매수 추천 TOP {min(top_n, len(buy_df))}  (점수 높은 순)</b>\n"]
    for rank, (_, row) in enumerate(buy_df.iterrows(), 1):
        r5  = row["ret5"]
        r20 = row["ret20"]
        # 전략 요약 (BUY인 지표만)
        buy_strategies = [d["name"] for d in row["details"] if d["signal"] == "BUY"]
        strat_str = " · ".join(buy_strategies) if buy_strategies else "복합"
        lines.append(
            f"<b>{rank}. {row['name']}</b>  <code>{row['ticker']}</code>\n"
            f"   💯 점수 {row['score']}/100  |  종가 {row['close']:,.0f}원\n"
            f"   {_ret_emoji(r5)}{r5:+.1f}% (5일)  {_ret_emoji(r20)}{r20:+.1f}% (20일)\n"
            f"   📌 {strat_str}\n"
        )
    return "\n".join(lines)


def format_sell_list(df: pd.DataFrame, top_n: int) -> str:
    """매도 추천 메시지"""
    sell_df = df[df["signal"] == "SELL"].sort_values("score").head(top_n)
    if sell_df.empty:
        return "🔴 <b>매도 추천</b>\n해당 종목 없음\n"

    lines = [f"🔴 <b>매도 추천 TOP {min(top_n, len(sell_df))}  (점수 낮은 순)</b>\n"]
    for rank, (_, row) in enumerate(sell_df.iterrows(), 1):
        r5  = row["ret5"]
        r20 = row["ret20"]
        sell_strategies = [d["name"] for d in row["details"] if d["signal"] == "SELL"]
        strat_str = " · ".join(sell_strategies) if sell_strategies else "복합"
        lines.append(
            f"<b>{rank}. {row['name']}</b>  <code>{row['ticker']}</code>\n"
            f"   💯 점수 {row['score']}/100  |  종가 {row['close']:,.0f}원\n"
            f"   {_ret_emoji(r5)}{r5:+.1f}% (5일)  {_ret_emoji(r20)}{r20:+.1f}% (20일)\n"
            f"   📌 {strat_str}\n"
        )
    return "\n".join(lines)


def format_detail(row: dict) -> str:
    """단일 종목 상세 메시지"""
    sig_emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(row["signal"], "❓")
    sig_label = {"BUY": "매수", "SELL": "매도", "HOLD": "관망"}.get(row["signal"], row["signal"])

    lines = [
        f"{sig_emoji} <b>[{row['ticker']}] {row['name']}</b>\n"
        f"종합 신호: <b>{sig_label}</b>  |  점수: <b>{row['score']}/100</b>\n"
        f"종가: {row['close']:,.0f}원  |  5일 {row['ret5']:+.1f}%  |  20일 {row['ret20']:+.1f}%\n"
        f"{'─'*28}\n"
    ]
    for d in row["details"]:
        s_emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(d["signal"], "❓")
        bar_full = round(d["score"] / 10)
        bar = "█" * bar_full + "░" * (10 - bar_full)
        lines.append(
            f"{s_emoji} <b>{d['name']}</b>  [{bar}] {d['score']}점\n"
            f"   └ {d['reason']}\n"
        )
    return "\n".join(lines)


# ── 메인 전송 로직 ────────────────────────────────────────────────────────────

def send_report(days: int = DEFAULT_DAYS, top_n: int = TOP_N,
                use_cache: bool = True):
    """스캔 후 텔레그램으로 전송"""
    if not BOT_TOKEN or BOT_TOKEN.startswith("여기에"):
        print("[오류] .env 파일에 TELEGRAM_BOT_TOKEN을 설정해주세요.")
        return

    bot = TelegramBot(BOT_TOKEN, CHAT_ID)
    if not bot.test():
        return

    print("[스캔] 시작...")
    df = run_scan(days=days, use_cache=use_cache)
    if df.empty:
        bot.send("⚠️ KOSPI 200 스캔 실패: 데이터 수집 오류")
        return

    # 메시지 1: 요약
    bot.send(format_summary(df, days))
    print("[전송] 요약 완료")

    # 메시지 2: 매수 추천
    bot.send(format_buy_list(df, top_n))
    print("[전송] 매수 추천 완료")

    # 메시지 3: 매도 추천
    bot.send(format_sell_list(df, top_n))
    print("[전송] 매도 추천 완료")

    print(f"[완료] 텔레그램 전송 완료 ({datetime.now().strftime('%H:%M:%S')})")


def schedule_daily(hour: int = 8, minute: int = 0, days: int = DEFAULT_DAYS):
    """매일 지정 시간에 자동 전송"""
    import time

    print(f"[스케줄] 매일 {hour:02d}:{minute:02d}에 자동 전송 시작 (Ctrl+C로 종료)")
    while True:
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_sec = (target - now).total_seconds()
        print(f"[대기] 다음 전송: {target.strftime('%Y-%m-%d %H:%M')} ({wait_sec/3600:.1f}시간 후)")
        time.sleep(wait_sec)
        try:
            send_report(days=days, use_cache=False)
        except Exception as e:
            print(f"[오류] 전송 실패: {e}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KOSPI 200 신호를 텔레그램으로 전송")
    parser.add_argument("--test",      action="store_true", help="연결 테스트만 실행")
    parser.add_argument("--days",      type=int, default=DEFAULT_DAYS, help="분석 기간 (일)")
    parser.add_argument("--top",       type=int, default=TOP_N,        help="상위 N개 종목")
    parser.add_argument("--schedule",  action="store_true",            help="매일 자동 전송 모드")
    parser.add_argument("--hour",      type=int, default=8,            help="자동 전송 시각 (시)")
    parser.add_argument("--minute",    type=int, default=0,            help="자동 전송 시각 (분)")
    parser.add_argument("--no-cache",  action="store_true",            help="캐시 무시")
    args = parser.parse_args()

    if not BOT_TOKEN or BOT_TOKEN.startswith("여기에"):
        print("=" * 50)
        print("[설정 필요] .env 파일을 열어 아래 두 값을 입력하세요:")
        print("  TELEGRAM_BOT_TOKEN=1234567890:ABC...")
        print("  TELEGRAM_CHAT_ID=123456789")
        print()
        print("봇 토큰 발급: 텔레그램에서 @BotFather → /newbot")
        print("채팅 ID 확인: 봇과 대화 후")
        print("  https://api.telegram.org/bot<토큰>/getUpdates")
        print("=" * 50)
        sys.exit(1)

    if args.test:
        bot = TelegramBot(BOT_TOKEN, CHAT_ID)
        if bot.test():
            bot.send("✅ KOSPI 200 스캐너 연결 테스트 성공!")
    elif args.schedule:
        schedule_daily(hour=args.hour, minute=args.minute, days=args.days)
    else:
        send_report(days=args.days, top_n=args.top, use_cache=not args.no_cache)
