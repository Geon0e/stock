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
import subprocess
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

def _liquidity_ok(df, min_krw=1_000_000_000, min_price=2000):
    if len(df) < 20 or df["Close"].iloc[-1] < min_price:
        return df["Close"].iloc[-1] >= min_price if not df.empty else False
    return (df["Close"].tail(20) * df["Volume"].tail(20)).mean() >= min_krw


def _fetch_kr(args):
    ticker, name, start, end, crawler, macro = args
    try:
        df = crawler.get_ohlcv(ticker, start, end, use_cache=False)
        if df is None or df.empty or len(df) < 10:
            return None
        if not _liquidity_ok(df):
            return None
        r = evaluate(df, macro)
        return {
            "ticker": ticker, "name": name,
            "signal": r["signal"], "score": r["score"], "details": r["details"],
            "open":  df["Open"].iloc[-1],
            "close": df["Close"].iloc[-1],
            "ret5":  (df["Close"].iloc[-1] / df["Close"].iloc[-5]  - 1) * 100 if len(df) >= 5  else 0,
            "ret20": (df["Close"].iloc[-1] / df["Close"].iloc[-20] - 1) * 100 if len(df) >= 20 else 0,
        }
    except Exception:
        return None


def _fetch_us(args):
    ticker, name, start, end, macro = args
    try:
        from data.us_fetcher import get_ohlcv_us
        df = get_ohlcv_us(ticker, start, end, use_cache=False)
        if df is None or df.empty or len(df) < 10:
            return None
        if not _liquidity_ok(df, min_krw=5_000_000, min_price=1):
            return None
        r = evaluate(df, macro)
        return {
            "ticker": ticker, "name": name,
            "signal": r["signal"], "score": r["score"], "details": r["details"],
            "open":  df["Open"].iloc[-1],
            "close": df["Close"].iloc[-1],
            "ret5":  (df["Close"].iloc[-1] / df["Close"].iloc[-5]  - 1) * 100 if len(df) >= 5  else 0,
            "ret20": (df["Close"].iloc[-1] / df["Close"].iloc[-20] - 1) * 100 if len(df) >= 20 else 0,
        }
    except Exception:
        return None


def run_scan(market: str):
    from data.macro_fetcher import fetch_all as _fetch_macro
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=90)).strftime("%Y-%m-%d")
    macro = _fetch_macro(days=30)
    print(f"[매크로] VIX={macro.get('vix', {}).get('value', 'N/A'):.1f}" if macro.get("vix", {}).get("value") else "[매크로] 수집 완료")

    if market == "kospi200":
        from data.fetcher import get_kospi200_tickers
        from data.crawler import NaverFinanceCrawler
        stocks  = get_kospi200_tickers(use_cache=True)
        crawler = NaverFinanceCrawler(request_delay=REQUEST_DELAY, verify_ssl=False)
        args    = [(r["Code"], r["Name"], start, end, crawler, macro) for _, r in stocks.iterrows()]
        worker  = _fetch_kr
    else:
        from data.us_fetcher import get_nasdaq100_tickers
        stocks = get_nasdaq100_tickers(use_cache=True)
        args   = [(r["Code"], r["Name"], start, end, macro) for _, r in stocks.iterrows()]
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
    """단일 종목 MA5/20/60+RSI 전략 1년 백테스팅"""
    from backtest.engine import BacktestEngine
    from backtest.strategies import MovingAverageCrossV2Strategy

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

        strategy = MovingAverageCrossV2Strategy(ticker, short_window=5, long_window=20, trend_window=60)
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

    # ── 메시지 2: 매수 추천 (텍스트형 — 전체 노출) ──────────────────────
    top_buy = buy_df.head(TOP_SCAN)
    if not top_buy.empty:
        lines = [f"★ 매수 추천 TOP{TOP_SCAN}\n{'─'*22}"]
        for rank, (_, row) in enumerate(top_buy.iterrows(), 1):
            strats = [d["name"] for d in row["details"] if d["signal"] == "BUY"]
            close_str = f"${row['close']:,.2f}" if currency == "$" else f"{row['close']:,.0f}원"
            lines.append(
                f"\n{rank}. {row['name']} ({row['ticker']})\n"
                f"  점수 {row['score']}/100  |  {close_str}\n"
                f"  {_arrow(row['ret5'])}{row['ret5']:+.1f}%(5일) "
                f"{_arrow(row['ret20'])}{row['ret20']:+.1f}%(20일)\n"
                f"  {' · '.join(strats) if strats else '복합신호'}"
            )
        bot.send_text("\n".join(lines))
        print("[전송] 매수 추천 완료")
    else:
        bot.send_text("★ 매수 추천 종목 없음")

    # ── 메시지 3: 뉴스 요약 (TOP 매수 종목) ──────────────────────────────
    try:
        from data.news_fetcher import fetch_news
        from data.news_summarizer import summarize_stocks_news

        stocks_news = []
        for _, row in top_buy.iterrows():
            articles = fetch_news(row["ticker"], market, max_articles=5)
            if articles:
                stocks_news.append({
                    "ticker":   row["ticker"],
                    "name":     row["name"],
                    "articles": articles,
                })

        if stocks_news:
            summary_text = summarize_stocks_news(stocks_news, market)
            if summary_text:
                msg = f"📰 매수 추천 종목 뉴스 요약\n{'─'*22}\n{summary_text}"
                bot.send_text(msg)
                print("[전송] 뉴스 요약 완료")
    except Exception as e:
        print(f"[뉴스] 수집/요약 실패 (생략): {e}")

    # ── 메시지 4: 백테스팅 결과 (TOP 3 매수 종목) ────────────────────────
    bt_tickers = list(buy_df.head(TOP_BT)[["ticker", "name"]].itertuples(index=False, name=None))
    if bt_tickers:
        bt_lines = [f"[백테스팅] MA5/20/60+RSI 전략 최근 {BT_DAYS//365}년\n{'─'*22}"]
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

    # ── 워치리스트 등록 (BUY 종목 자동 추적) ──────────────────────────────
    try:
        from notifications.watchlist import add_from_df
        add_from_df(buy_df, market)
    except Exception as e:
        print(f"[워치리스트] 등록 실패: {e}")

    # ── 기록 저장 ──────────────────────────────────────────────────────────
    try:
        path = save_report(market, "kakao", df, top_n=TOP_SCAN)
        print(f"[기록] 저장 완료 → {path}")
    except Exception as e:
        print(f"[기록] 저장 실패: {e}")


# ── 매도 모니터 자동 시작 ────────────────────────────────────────────────

def _start_sell_monitor():
    """
    리포트 전송 완료 후 sell_monitor.py를 백그라운드로 실행.
    이미 실행 중이면 중복 실행하지 않음.
    """
    monitor_path = Path(__file__).parent / "notifications" / "sell_monitor.py"
    if not monitor_path.exists():
        print("[모니터] sell_monitor.py 파일을 찾을 수 없습니다.")
        return

    # 이미 실행 중인지 확인
    try:
        import psutil
        for proc in psutil.process_iter(["cmdline"]):
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if "sell_monitor.py" in cmdline:
                print("[모니터] sell_monitor가 이미 실행 중입니다. 중복 실행 생략.")
                return
    except ImportError:
        pass  # psutil 없으면 중복 체크 생략

    try:
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_CONSOLE
        subprocess.Popen(
            [sys.executable, str(monitor_path)],
            creationflags=flags,
        )
        print("[모니터] 매도 모니터 자동 시작 완료 (장 시작 09:00까지 자동 대기)")
    except Exception as e:
        print(f"[모니터] 자동 시작 실패: {e}")


# ── 스케줄러 ─────────────────────────────────────────────────────────────

def job(market: str):
    """매일 실행되는 리포트 작업"""
    print(f"\n{'='*50}")
    print(f"[리포트] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 시작")
    print(f"{'='*50}")

    # 전날 추천 종목 EOD 성과 업데이트
    try:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        from reports.history import update_eod_performance
        if update_eod_performance(market, yesterday):
            print(f"[성과] {yesterday} EOD 성과 업데이트 완료")
    except Exception as e:
        print(f"[성과] EOD 업데이트 실패 (무시): {e}")

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

        # ── 매도 모니터 자동 시작 ────────────────────────────────────────────
        _start_sell_monitor()

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
