"""
카카오 나에게 보내기 KOSPI 200 신호 알림

사용법:
    python kakao_bot.py              # 즉시 스캔 후 전송
    python kakao_bot.py --test       # 테스트 메시지만 전송
    python kakao_bot.py --days 90    # 90일 기준 스캔
    python kakao_bot.py --top 20     # 상위 20개 전송
    python kakao_bot.py --schedule   # 매일 오전 8시 자동 전송
    python kakao_bot.py --schedule --hour 9 --minute 30
"""

import sys
import os
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

import requests
import urllib3
import pandas as pd

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

REST_API_KEY   = os.environ.get("KAKAO_REST_API_KEY",   "")
ACCESS_TOKEN   = os.environ.get("KAKAO_ACCESS_TOKEN",   "")
REFRESH_TOKEN  = os.environ.get("KAKAO_REFRESH_TOKEN",  "")
CLIENT_SECRET  = os.environ.get("KAKAO_CLIENT_SECRET",  "")

# ── 설정 ─────────────────────────────────────────────────────────────────────
TOP_N         = 5
DEFAULT_DAYS  = 60
MAX_WORKERS   = 5
REQUEST_DELAY = 0.3
KAKAO_SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"


# ── 카카오 봇 ─────────────────────────────────────────────────────────────────

class KakaoBot:
    def __init__(self, rest_api_key: str, access_token: str, refresh_token: str,
                 client_secret: str = ""):
        self.rest_api_key  = rest_api_key
        self.access_token  = access_token
        self.refresh_token = refresh_token
        self.client_secret = client_secret

    def _refresh(self) -> bool:
        """액세스 토큰 갱신"""
        data = {
            "grant_type":    "refresh_token",
            "client_id":     self.rest_api_key,
            "refresh_token": self.refresh_token,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        resp = requests.post(
            "https://kauth.kakao.com/oauth/token",
            data=data,
            verify=False,
            timeout=15,
        )
        data = resp.json()
        if "access_token" in data:
            self.access_token = data["access_token"]
            if "refresh_token" in data:
                self.refresh_token = data["refresh_token"]
            # .env 업데이트
            _update_env("KAKAO_ACCESS_TOKEN",  self.access_token)
            if "refresh_token" in data:
                _update_env("KAKAO_REFRESH_TOKEN", self.refresh_token)
            print("[토큰] 액세스 토큰 갱신 완료")
            return True
        print(f"[오류] 토큰 갱신 실패: {data}")
        return False

    def _send_payload(self, template: dict) -> bool:
        """카카오 API 호출 (토큰 만료 시 자동 갱신 후 재시도)"""
        import json
        for attempt in range(2):
            resp = requests.post(
                KAKAO_SEND_URL,
                headers={"Authorization": f"Bearer {self.access_token}"},
                data={"template_object": json.dumps(template, ensure_ascii=False)},
                verify=False,
                timeout=15,
            )
            data = resp.json()
            if data.get("result_code") == 0:
                return True
            # 토큰 만료 (-401) 시 갱신 후 재시도
            if resp.status_code in (401, 403) and attempt == 0:
                if self._refresh():
                    continue
            print(f"[오류] 전송 실패: {data}")
            return False
        return False

    def send_text(self, text: str) -> bool:
        """텍스트 메시지 전송 (최대 2000자, 초과 시 분할)"""
        chunks = [text[i:i+1900] for i in range(0, len(text), 1900)]
        ok = True
        for chunk in chunks:
            template = {
                "object_type": "text",
                "text":        chunk,
                "link": {
                    "web_url":    "https://finance.naver.com",
                    "mobile_web_url": "https://finance.naver.com",
                },
            }
            ok &= self._send_payload(template)
        return ok

    def send_list(self, header: str, items: list[dict]) -> bool:
        """
        리스트형 메시지 전송

        items: [{"title": ..., "description": ..., "link": ...}, ...]
        최대 5개 (카카오 제한)
        """
        contents = []
        for item in items[:5]:
            contents.append({
                "title":       item.get("title", ""),
                "description": item.get("description", ""),
                "link": {
                    "web_url":        item.get("link", "https://finance.naver.com"),
                    "mobile_web_url": item.get("link", "https://finance.naver.com"),
                },
            })
        template = {
            "object_type": "list",
            "header_title": header,
            "header_link": {
                "web_url":        "https://finance.naver.com",
                "mobile_web_url": "https://finance.naver.com",
            },
            "contents": contents,
        }
        return self._send_payload(template)

    def test(self) -> bool:
        return self.send_text("✅ KOSPI 200 스캐너 카카오톡 연결 테스트 성공!")


# ── .env 업데이트 헬퍼 ────────────────────────────────────────────────────────

def _update_env(key: str, value: str):
    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        return
    lines = env_path.read_text(encoding="utf-8").splitlines()
    new_lines = []
    found = False
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k == key:
                new_lines.append(f"{key}={value}")
                found = True
                continue
        new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


# ── 스캔 (telegram_bot.py와 동일) ─────────────────────────────────────────────

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

    results, done, total = [], 0, len(task_args)
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

    return pd.DataFrame(results).sort_values("score", ascending=False).reset_index(drop=True) if results else pd.DataFrame()


# ── 메시지 포맷 ───────────────────────────────────────────────────────────────

def _naver_url(ticker: str) -> str:
    return f"https://finance.naver.com/item/main.nhn?code={ticker}"


def _ret_arrow(v: float) -> str:
    return "▲" if v >= 0 else "▼"


def send_report(bot: KakaoBot, df: pd.DataFrame, days: int, top_n: int):
    """스캔 결과를 카카오톡으로 전송"""
    buy_df  = df[df["signal"] == "BUY"]
    sell_df = df[df["signal"] == "SELL"]
    hold_df = df[df["signal"] == "HOLD"]
    total   = len(df)
    now     = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── 메시지 1: 요약 ──────────────────────────────────────────────────
    summary = (
        f"[KOSPI 200 신호 스캐너]\n"
        f"{now} | 최근 {days}일 기준\n"
        f"{'─'*22}\n"
        f"분석 종목 : {total}개\n"
        f"매수 신호 : {len(buy_df)}개 ({len(buy_df)/total*100:.0f}%)\n"
        f"매도 신호 : {len(sell_df)}개 ({len(sell_df)/total*100:.0f}%)\n"
        f"관  망    : {len(hold_df)}개 ({len(hold_df)/total*100:.0f}%)\n"
        f"평균 점수 : {df['score'].mean():.1f}점"
    )
    bot.send_text(summary)
    print("[전송] 요약 완료")

    # ── 메시지 2: 매수 추천 (리스트형, 5개씩) ────────────────────────────
    top_buy = buy_df.head(top_n)
    if not top_buy.empty:
        for chunk_start in range(0, len(top_buy), 5):
            chunk = top_buy.iloc[chunk_start:chunk_start + 5]
            items = []
            for rank, (_, row) in enumerate(chunk.iterrows(), chunk_start + 1):
                buy_strats = [d["name"] for d in row["details"] if d["signal"] == "BUY"]
                items.append({
                    "title":       f"{rank}. {row['name']} ({row['ticker']})",
                    "description": (
                        f"점수 {row['score']}/100 | {row['close']:,.0f}원\n"
                        f"{_ret_arrow(row['ret5'])}{row['ret5']:+.1f}%(5일) "
                        f"{_ret_arrow(row['ret20'])}{row['ret20']:+.1f}%(20일)\n"
                        f"{'·'.join(buy_strats) if buy_strats else '복합신호'}"
                    ),
                    "link": _naver_url(row["ticker"]),
                })
            header = f"매수 추천 TOP {len(top_buy)} ({chunk_start+1}~{chunk_start+len(chunk)}위)"
            bot.send_list(header, items)
        print("[전송] 매수 추천 완료")
    else:
        bot.send_text("매수 추천 종목이 없습니다.")

    # ── 메시지 3: 매도 추천 (리스트형, 5개씩) ────────────────────────────
    top_sell = sell_df.sort_values("score").head(top_n)
    if not top_sell.empty:
        for chunk_start in range(0, len(top_sell), 5):
            chunk = top_sell.iloc[chunk_start:chunk_start + 5]
            items = []
            for rank, (_, row) in enumerate(chunk.iterrows(), chunk_start + 1):
                sell_strats = [d["name"] for d in row["details"] if d["signal"] == "SELL"]
                items.append({
                    "title":       f"{rank}. {row['name']} ({row['ticker']})",
                    "description": (
                        f"점수 {row['score']}/100 | {row['close']:,.0f}원\n"
                        f"{_ret_arrow(row['ret5'])}{row['ret5']:+.1f}%(5일) "
                        f"{_ret_arrow(row['ret20'])}{row['ret20']:+.1f}%(20일)\n"
                        f"{'·'.join(sell_strats) if sell_strats else '복합신호'}"
                    ),
                    "link": _naver_url(row["ticker"]),
                })
            header = f"매도 추천 TOP {len(top_sell)} ({chunk_start+1}~{chunk_start+len(chunk)}위)"
            bot.send_list(header, items)
        print("[전송] 매도 추천 완료")
    else:
        bot.send_text("매도 추천 종목이 없습니다.")

    print(f"[완료] 카카오톡 전송 완료 ({datetime.now().strftime('%H:%M:%S')})")


def schedule_daily(bot: KakaoBot, hour: int, minute: int, days: int, top_n: int):
    """매일 지정 시간에 자동 전송"""
    import time

    print(f"[스케줄] 매일 {hour:02d}:{minute:02d} 자동 전송 시작 (Ctrl+C로 종료)")
    while True:
        now    = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait   = (target - now).total_seconds()
        print(f"[대기] 다음 전송: {target.strftime('%Y-%m-%d %H:%M')} ({wait/3600:.1f}시간 후)")
        time.sleep(wait)
        try:
            df = run_scan(days=days, use_cache=False)
            if not df.empty:
                send_report(bot, df, days, top_n)
            else:
                bot.send_text("⚠️ KOSPI 200 스캔 실패: 데이터 수집 오류")
        except Exception as e:
            print(f"[오류] {e}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KOSPI 200 신호를 카카오톡으로 전송")
    parser.add_argument("--test",     action="store_true", help="테스트 메시지만 전송")
    parser.add_argument("--days",     type=int, default=DEFAULT_DAYS)
    parser.add_argument("--top",      type=int, default=TOP_N)
    parser.add_argument("--schedule", action="store_true",  help="매일 자동 전송")
    parser.add_argument("--hour",     type=int, default=8)
    parser.add_argument("--minute",   type=int, default=0)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    if not REST_API_KEY or not ACCESS_TOKEN:
        print("=" * 50)
        print("[설정 필요] 먼저 아래 명령어로 초기 설정을 완료하세요:")
        print("  python kakao_setup.py")
        print("=" * 50)
        sys.exit(1)

    bot = KakaoBot(REST_API_KEY, ACCESS_TOKEN, REFRESH_TOKEN, CLIENT_SECRET)

    if args.test:
        ok = bot.test()
        print("[OK] 테스트 메시지 전송 완료!" if ok else "[오류] 전송 실패")

    elif args.schedule:
        schedule_daily(bot, hour=args.hour, minute=args.minute,
                       days=args.days, top_n=args.top)
    else:
        df = run_scan(days=args.days, use_cache=not args.no_cache)
        if df.empty:
            bot.send_text("⚠️ KOSPI 200 스캔 실패")
        else:
            send_report(bot, df, args.days, args.top)
