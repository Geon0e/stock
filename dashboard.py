"""
네이버 증권 대시보드
python dashboard.py 로 실행하면 시장 현황을 한눈에 확인
"""

import io
import sys
from datetime import datetime, timedelta
from pathlib import Path

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

from data.naver_crawler import NaverFinanceCrawler
import importlib.util as _ilu, pathlib as _pl
_spec = _ilu.spec_from_file_location("signal_mod", _pl.Path(__file__).parent / "stock_signal.py")
_sig_mod = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_sig_mod)
evaluate_signal = _sig_mod.evaluate

# ── 설정 ────────────────────────────────────────────────────────────────────
WATCH_TICKERS = [
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    ("035420", "NAVER"),
    ("005380", "현대차"),
    ("051910", "LG화학"),
    ("035720", "카카오"),
]
OHLCV_TICKERS  = ["005930", "000660"]          # 최근 시세 보여줄 종목
INVESTOR_TICKERS = ["005930", "000660"]         # 투자자 동향 보여줄 종목
OHLCV_DAYS     = 10                             # 최근 N거래일 시세
INVESTOR_DAYS  = 5                              # 최근 N거래일 투자자 동향
# ────────────────────────────────────────────────────────────────────────────

W = 64  # 출력 너비


def sep(char="─", width=W):
    print(char * width)


def title(text):
    print()
    sep("═")
    pad = (W - len(text)) // 2
    print(" " * pad + text)
    sep("═")


def section(text):
    print()
    sep("─")
    print(f"  {text}")
    sep("─")


def run():
    crawler = NaverFinanceCrawler(verify_ssl=False)
    today   = datetime.today().strftime("%Y-%m-%d")
    start30 = (datetime.today() - timedelta(days=60)).strftime("%Y-%m-%d")

    title("📊 네이버 증권 대시보드")
    print(f"  조회 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # ── 1. 시장 지수 ──────────────────────────────────────────────────────
    section("① 시장 지수 (KOSPI / KOSDAQ)")
    try:
        indices = crawler.get_market_index()
        for market, d in indices.items():
            val = d.get("value", "-")
            chg = d.get("change", "")
            pct = d.get("change_pct", "")
            arrow = "▲" if chg and "+" in str(chg) else "▼"
            print(f"  {market:<8}  {val:>10}  {arrow} {chg}  ({pct})")
    except Exception as e:
        print(f"  [오류] {e}")

    # ── 2. 관심 종목 현재가 ───────────────────────────────────────────────
    section("② 관심 종목 현재가")
    print(f"  {'종목코드':<8} {'종목명':<12} {'현재가':>9} {'등락':>8} {'등락률':>7}  {'거래량':>12}")
    sep()
    for ticker, name in WATCH_TICKERS:
        try:
            info = crawler.get_stock_info(ticker)
            price  = f"{info.get('current_price', '-'):,}" if isinstance(info.get('current_price'), int) else "-"
            change = info.get('change', '-')
            pct    = info.get('change_pct', '-')
            vol    = f"{info.get('volume', '-'):,}"  if isinstance(info.get('volume'), int) else "-"
            arrow  = "▲" if change and "+" in str(change) else "▼"
            print(f"  {ticker:<8} {name:<12} {price:>9} {arrow}{change:>7} {pct:>7}  {vol:>12}")
        except Exception as e:
            print(f"  {ticker:<8} {name:<12}  [오류] {e}")

    # ── 3. 최근 시세 (OHLCV) ─────────────────────────────────────────────
    section(f"③ 최근 {OHLCV_DAYS}거래일 시세")
    for ticker in OHLCV_TICKERS:
        name = next((n for t, n in WATCH_TICKERS if t == ticker), ticker)
        try:
            df = crawler.get_ohlcv(ticker, start30, today)
            if df.empty:
                print(f"  [{ticker}] 데이터 없음")
                continue
            recent = df.tail(OHLCV_DAYS)
            print(f"\n  [{ticker}] {name}")
            print(f"  {'날짜':<12} {'시가':>9} {'고가':>9} {'저가':>9} {'종가':>9} {'거래량':>13}")
            sep("·")
            for date, row in recent.iterrows():
                print(
                    f"  {str(date.date()):<12}"
                    f" {row['Open']:>9,}"
                    f" {row['High']:>9,}"
                    f" {row['Low']:>9,}"
                    f" {row['Close']:>9,}"
                    f" {row['Volume']:>13,}"
                )
        except Exception as e:
            print(f"  [{ticker}] [오류] {e}")

    # ── 4. 투자자별 거래동향 ──────────────────────────────────────────────
    section(f"④ 최근 {INVESTOR_DAYS}거래일 투자자 동향")
    for ticker in INVESTOR_TICKERS:
        name = next((n for t, n in WATCH_TICKERS if t == ticker), ticker)
        try:
            df = crawler.get_investor_trend(ticker, start30, today)
            if df.empty:
                print(f"  [{ticker}] 데이터 없음")
                continue
            recent = df.tail(INVESTOR_DAYS)
            print(f"\n  [{ticker}] {name}")
            print(f"  {'날짜':<12} {'종가':>9} {'기관순매수':>13} {'외국인순매수':>14}")
            sep("·")
            for date, row in recent.iterrows():
                inst = row.get("Institution", 0)
                frgn = row.get("Foreign", 0)
                inst_arrow = "▲" if inst > 0 else "▼"
                frgn_arrow = "▲" if frgn > 0 else "▼"
                print(
                    f"  {str(date.date()):<12}"
                    f" {row['Close']:>9,}"
                    f" {inst_arrow}{abs(inst):>12,}"
                    f" {frgn_arrow}{abs(frgn):>13,}"
                )
        except Exception as e:
            print(f"  [{ticker}] [오류] {e}")

    # ── 5. 매수/매도 판단 ─────────────────────────────────────────────────
    section("⑤ 매수/매도 판단")
    signal_tickers = list({t for t in OHLCV_TICKERS + INVESTOR_TICKERS})
    for ticker in signal_tickers:
        name = next((n for t, n in WATCH_TICKERS if t == ticker), ticker)
        try:
            df = crawler.get_ohlcv(ticker, start30, today)
            result = evaluate_signal(df)

            sig   = result["signal"]
            score = result["score"]
            sig_label = {"BUY": "★ 매수", "SELL": "▼ 매도", "HOLD": "─ 관망"}.get(sig, sig)
            bar   = "█" * (score // 10) + "░" * (10 - score // 10)

            print(f"\n  [{ticker}] {name}")
            print(f"  판단: {sig_label}   점수: {score:3d}/100  [{bar}]")

            for d in result["details"]:
                print(f"    · {d['name']:<10}  {d['signal']:<4}  {d['reason']}")
        except Exception as e:
            print(f"  [{ticker}] [오류] {e}")

    # ── 마무리 ────────────────────────────────────────────────────────────
    print()
    sep("═")
    print(f"  완료: {datetime.now().strftime('%H:%M:%S')}")
    sep("═")
    print()


if __name__ == "__main__":
    run()
