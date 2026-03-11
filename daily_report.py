"""
평일 오전 8:30 자동 리포트 - 신호 스캔 + 백테스팅 결과를 카카오톡으로 전송

실행:
    python daily_report.py                    # 스케줄 모드 (평일 8:30 자동)
    python daily_report.py --now              # 즉시 실행 (테스트)
    python daily_report.py --now --market nasdaq100
"""

import sys
import os
import argparse
import time
import contextlib
import io as _io
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent))

from signals import evaluate
from notifications.kakao_bot import KakaoBot, REST_API_KEY, ACCESS_TOKEN, REFRESH_TOKEN, CLIENT_SECRET
from reports.history import save_report

MAX_WORKERS   = 5
REQUEST_DELAY = 0.3
BT_DAYS       = 365      # 백테스팅 기간 (1년)
CAPITAL       = 10_000_000
TOP_SCAN      = 5        # 매수 추천 전송 개수
TOP_BT        = 3        # 백테스팅 실행 종목 수


# ── 스캔 ─────────────────────────────────────────────────────────────────

def _fetch_kr(args):
    ticker, name, start, end, crawler = args
    try:
        df = crawler.get_ohlcv(ticker, start, end, use_cache=False)
        if df is None or df.empty or len(df) < 10:
            return None
        r = evaluate(df)
        return {
            "ticker": ticker, "name": name,
            "signal": r["signal"], "score": r["score"], "details": r["details"],
            "close": df["Close"].iloc[-1],
            "ret5":  (df["Close"].iloc[-1] / df["Close"].iloc[-5]  - 1) * 100 if len(df) >= 5  else 0,
            "ret20": (df["Close"].iloc[-1] / df["Close"].iloc[-20] - 1) * 100 if len(df) >= 20 else 0,
        }
    except Exception:
        return None


def _fetch_us(args):
    ticker, name, start, end = args
    try:
        from data.us_fetcher import get_ohlcv_us
        df = get_ohlcv_us(ticker, start, end, use_cache=False)
        if df is None or df.empty or len(df) < 10:
            return None
        r = evaluate(df)
        return {
            "ticker": ticker, "name": name,
            "signal": r["signal"], "score": r["score"], "details": r["details"],
            "close": df["Close"].iloc[-1],
            "ret5":  (df["Close"].iloc[-1] / df["Close"].iloc[-5]  - 1) * 100 if len(df) >= 5  else 0,
            "ret20": (df["Close"].iloc[-1] / df["Close"].iloc[-20] - 1) * 100 if len(df) >= 20 else 0,
        }
    except Exception:
        return None


def run_scan(market: str):
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=90)).strftime("%Y-%m-%d")

    if market == "kospi200":
        from data.fetcher import get_kospi200_tickers
        from data.crawler import NaverFinanceCrawler
        stocks  = get_kospi200_tickers(use_cache=True)
        crawler = NaverFinanceCrawler(request_delay=REQUEST_DELAY, verify_ssl=False)
        args    = [(r["Code"], r["Name"], start, end, crawler) for _, r in stocks.iterrows()]
        worker  = _fetch_kr
    else:
        from data.us_fetcher import get_nasdaq100_tickers
        stocks = get_nasdaq100_tickers(use_cache=True)
        args   = [(r["Code"], r["Name"], start, end) for _, r in stocks.iterrows()]
        worker = _fetch_us

    results = []
    total, done = len(args), 0
    print(f"[스캔] {market} {total}개 종목 분석 중...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(worker, a): a for a in args}
        for f in as_completed(futures):
            done += 1
            res = f.result()
            if res:
                results.append(res)
            if done % 30 == 0 or done == total:
                print(f"  진행 {done}/{total} (성공 {len(results)})")

    import pandas as pd
    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results).sort_values("score", ascending=False).reset_index(drop=True)


# ── 백테스팅 ─────────────────────────────────────────────────────────────

def run_backtest_for_ticker(ticker: str, market: str):
    """단일 종목 MA5/20 전략 1년 백테스팅"""
    from backtest.engine import BacktestEngine
    from backtest.strategies import MovingAverageCrossStrategy

    end_dt   = datetime.today()
    start_dt = end_dt - timedelta(days=BT_DAYS)
    start    = start_dt.strftime("%Y-%m-%d")
    end      = end_dt.strftime("%Y-%m-%d")

    try:
        if market == "kospi200":
            from data.fetcher import get_ohlcv
            df = get_ohlcv(ticker, start, end)
        else:
            from data.us_fetcher import get_ohlcv_us
            df = get_ohlcv_us(ticker, start, end, use_cache=True)

        if df is None or df.empty or len(df) < 60:
            return None

        strategy = MovingAverageCrossStrategy(ticker, short_window=5, long_window=20)
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            engine = BacktestEngine(data={ticker: df}, initial_capital=CAPITAL)
            engine.run(strategy)
            metrics = engine.report()

        return metrics
    except Exception as e:
        print(f"[백테스팅] {ticker} 실패: {e}")
        return None


# ── 카카오 메시지 포맷 ────────────────────────────────────────────────────

def _arrow(v):
    return "▲" if v >= 0 else "▼"


def _naver_link(ticker, market):
    if market == "nasdaq100":
        return f"https://finance.yahoo.com/quote/{ticker}"
    return f"https://finance.naver.com/item/main.nhn?code={ticker}"


def send_daily_report(bot: KakaoBot, df, market: str):
    import pandas as pd

    market_label = "KOSPI 200" if market == "kospi200" else "NASDAQ 100"
    currency     = "원" if market == "kospi200" else "$"
    now          = datetime.now().strftime("%Y-%m-%d %H:%M")
    buy_df       = df[df["signal"] == "BUY"]
    sell_df      = df[df["signal"] == "SELL"]

    # ── 메시지 1: 요약 ───────────────────────────────────────────────────
    summary = (
        f"[{market_label} 아침 리포트]\n"
        f"{now}\n"
        f"{'─'*22}\n"
        f"분석 종목 : {len(df)}개\n"
        f"매수 신호 : {len(buy_df)}개 ({len(buy_df)/len(df)*100:.0f}%)\n"
        f"매도 신호 : {len(sell_df)}개 ({len(sell_df)/len(df)*100:.0f}%)\n"
        f"평균 점수 : {df['score'].mean():.1f}점\n"
        f"{'─'*22}\n"
        f"※ 이동평균·RSI·볼린저·MACD·모멘텀 앙상블"
    )
    bot.send_text(summary)
    print("[전송] 요약 완료")

    # ── 메시지 2~3: 매수 추천 (5개씩 리스트형) ───────────────────────────
    top_buy = buy_df.head(TOP_SCAN)
    if not top_buy.empty:
        for i in range(0, len(top_buy), 5):
            chunk = top_buy.iloc[i:i+5]
            items = []
            for rank, (_, row) in enumerate(chunk.iterrows(), i + 1):
                strats = [d["name"] for d in row["details"] if d["signal"] == "BUY"]
                close_str = f"${row['close']:,.2f}" if currency == "$" else f"{row['close']:,.0f}원"
                items.append({
                    "title": f"{rank}. {row['name']} ({row['ticker']})",
                    "description": (
                        f"점수 {row['score']}/100  |  {close_str}\n"
                        f"{_arrow(row['ret5'])}{row['ret5']:+.1f}%(5일) "
                        f"{_arrow(row['ret20'])}{row['ret20']:+.1f}%(20일)\n"
                        f"{' · '.join(strats) if strats else '복합신호'}"
                    ),
                    "link": _naver_link(row["ticker"], market),
                })
            bot.send_list(f"★ 매수 추천 TOP{TOP_SCAN} ({i+1}~{i+len(chunk)}위)", items)
        print("[전송] 매수 추천 완료")
    else:
        bot.send_text("★ 매수 추천 종목 없음")

    # ── 메시지 4: 백테스팅 결과 (TOP 3 매수 종목) ────────────────────────
    bt_tickers = list(buy_df.head(TOP_BT)[["ticker", "name"]].itertuples(index=False, name=None))
    if bt_tickers:
        bt_lines = [f"[백테스팅] MA5/20 전략 최근 {BT_DAYS//365}년\n{'─'*22}"]
        for ticker, name in bt_tickers:
            m = run_backtest_for_ticker(ticker, market)
            if m:
                ret    = m.get("총수익률(%)", 0)
                cagr   = m.get("연환산수익률(CAGR,%)", 0)
                mdd    = m.get("최대낙폭(MDD,%)", 0)
                sharpe = m.get("샤프비율", 0)
                wr     = m.get("승률(%)", 0)
                bt_lines.append(
                    f"\n▶ {name} ({ticker})\n"
                    f"  수익률  {ret:+.1f}%  CAGR {cagr:+.1f}%\n"
                    f"  MDD {mdd:.1f}%  샤프 {sharpe:.2f}  승률 {wr:.0f}%"
                )
            else:
                bt_lines.append(f"\n▶ {name} ({ticker}): 데이터 부족")

        bot.send_text("\n".join(bt_lines))
        print("[전송] 백테스팅 완료")

    # ── 기록 저장 ──────────────────────────────────────────────────────────
    try:
        path = save_report(market, "kakao", df, top_n=TOP_SCAN)
        print(f"[기록] 저장 완료 → {path}")
    except Exception as e:
        print(f"[기록] 저장 실패: {e}")


# ── 스케줄러 ─────────────────────────────────────────────────────────────

def job(market: str):
    """매일 실행되는 리포트 작업"""
    print(f"\n{'='*50}")
    print(f"[리포트] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 시작")
    print(f"{'='*50}")

    if not REST_API_KEY or not ACCESS_TOKEN:
        print("[오류] 카카오 설정이 없습니다. kakao_setup.py를 먼저 실행하세요.")
        return

    try:
        df = run_scan(market)
        if df.empty:
            bot = KakaoBot(REST_API_KEY, ACCESS_TOKEN, REFRESH_TOKEN, CLIENT_SECRET)
            bot.send_text("⚠️ 스캔 실패 - 데이터 수집 오류")
            return

        bot = KakaoBot(REST_API_KEY, ACCESS_TOKEN, REFRESH_TOKEN, CLIENT_SECRET)
        send_daily_report(bot, df, market)
        print(f"[완료] {datetime.now().strftime('%H:%M:%S')}")

    except Exception as e:
        print(f"[오류] {e}")
        try:
            bot = KakaoBot(REST_API_KEY, ACCESS_TOKEN, REFRESH_TOKEN, CLIENT_SECRET)
            bot.send_text(f"⚠️ 리포트 오류: {e}")
        except Exception:
            pass


def run_schedule(market: str, hour: int = 8, minute: int = 30):
    """평일 지정 시각 자동 실행"""
    print(f"[스케줄] 평일 {hour:02d}:{minute:02d} 카카오톡 리포트 자동 전송 시작")
    print(f"[대상 시장] {market}  |  Ctrl+C 로 종료\n")

    while True:
        now    = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # 오늘 시각이 지났으면 다음 날로
        if now >= target:
            target += timedelta(days=1)

        # 주말이면 월요일로 skip
        while target.weekday() >= 5:
            target += timedelta(days=1)

        wait = (target - now).total_seconds()
        print(f"[대기] 다음 전송: {target.strftime('%Y-%m-%d(%a) %H:%M')}  ({wait/3600:.1f}시간 후)")

        time.sleep(wait)

        # 다시 한번 평일 확인 (자정 넘어 sleep 후 확인)
        if datetime.now().weekday() < 5:
            job(market)
        else:
            print("[스킵] 주말 — 다음 평일로 넘어갑니다.")


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="평일 8:30 카카오톡 리포트 자동 전송")
    parser.add_argument("--market", choices=["kospi200", "nasdaq100"], default="kospi200")
    parser.add_argument("--now",    action="store_true", help="즉시 실행 (테스트)")
    parser.add_argument("--hour",   type=int, default=8,  help="전송 시각 (시)")
    parser.add_argument("--minute", type=int, default=30, help="전송 시각 (분)")
    args = parser.parse_args()

    if args.now:
        job(args.market)
    else:
        run_schedule(args.market, hour=args.hour, minute=args.minute)
