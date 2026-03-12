"""
주식 신호 스캐너 대시보드
KOSPI 200 / NASDAQ 100
실행: streamlit run app.py
"""

import io
import sys
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent))

try:
    if sys.stdout and getattr(sys.stdout, "encoding", None) and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if sys.stderr and getattr(sys.stderr, "encoding", None) and sys.stderr.encoding.lower() != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from signals import evaluate
from reports.history import save_report, list_report_files, load_file, available_dates

# ── .env / 봇 설정 ────────────────────────────────────────────────────────
from notifications.telegram_bot import TelegramBot, BOT_TOKEN, CHAT_ID, TOP_N as _TG_TOP
from notifications.kakao_bot import (
    KakaoBot, send_report as _kakao_send_report,
    REST_API_KEY, ACCESS_TOKEN, REFRESH_TOKEN, CLIENT_SECRET, TOP_N as _KK_TOP,
)

# ── 페이지 설정 ───────────────────────────────────────────────────────────
st.set_page_config(
    page_title="주식 신호 스캐너",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

* { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }
.stApp { background-color: #080c14; }

/* 사이드바 */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0a0f1e 0%, #080c14 100%);
    border-right: 1px solid #1d2d44;
}
[data-testid="stSidebar"] .stButton button {
    background: linear-gradient(135deg, #3d72eb 0%, #2952c7 100%);
    color: #fff; border: none; border-radius: 8px;
    font-weight: 600; letter-spacing: 0.3px;
    transition: all 0.2s;
}
[data-testid="stSidebar"] .stButton button:hover {
    box-shadow: 0 0 20px rgba(61,114,235,0.4);
    transform: translateY(-1px);
}

/* 메트릭 카드 */
[data-testid="metric-container"] {
    background: linear-gradient(135deg, #0e1623 0%, #0b1020 100%);
    border: 1px solid #1d2d44;
    border-radius: 12px; padding: 16px 20px;
    transition: border-color 0.2s;
}
[data-testid="metric-container"]:hover { border-color: #2d4a6e; }
[data-testid="stMetricValue"] { font-size: 1.75rem !important; font-weight: 700; color: #edf2f8 !important; }
[data-testid="stMetricLabel"] { color: #8898aa !important; font-size: 0.8rem !important; }

/* 탭 */
[data-testid="stTabs"] [data-baseweb="tab-list"] {
    background: #0a0f1e; border-bottom: 1px solid #1d2d44; gap: 4px;
}
[data-testid="stTabs"] [data-baseweb="tab"] {
    background: transparent; color: #8898aa;
    border-radius: 8px 8px 0 0; font-weight: 500;
    padding: 8px 16px;
}
[data-testid="stTabs"] [aria-selected="true"] {
    background: linear-gradient(180deg, #1a2a4a 0%, #0e1623 100%);
    color: #edf2f8 !important; border-bottom: 2px solid #3d72eb;
}

/* 버튼 primary */
.stButton button[kind="primary"] {
    background: linear-gradient(135deg, #3d72eb 0%, #2952c7 100%);
    border: none; border-radius: 8px; font-weight: 600;
    letter-spacing: 0.3px; transition: all 0.2s;
}
.stButton button[kind="primary"]:hover {
    box-shadow: 0 0 24px rgba(61,114,235,0.45);
    transform: translateY(-1px);
}

/* 입력 요소 */
[data-testid="stSelectbox"] > div, [data-testid="stTextInput"] > div > div {
    background: #0e1623 !important; border-color: #1d2d44 !important;
    border-radius: 8px !important; color: #edf2f8 !important;
}
.stSlider [data-baseweb="slider"] { padding: 4px 0; }

/* 구분선 */
hr { border-color: #1d2d44 !important; }

/* 텍스트 */
h1, h2, h3, h4 { color: #edf2f8 !important; }
p, label { color: #8898aa; }
.stCaption { color: #5a6a85 !important; }

/* 데이터프레임 */
[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }
[data-testid="stDataFrame"] thead tr th {
    background: #131c2e !important; color: #8898aa !important;
    font-size: 0.78rem; font-weight: 600; letter-spacing: 0.5px;
}
[data-testid="stDataFrame"] tbody tr:nth-child(odd) { background: #0e1623 !important; }
[data-testid="stDataFrame"] tbody tr:nth-child(even) { background: #0b1020 !important; }

/* 배지 */
.badge-buy  { background: linear-gradient(135deg,#00c896,#00a07a); color:#000; padding:3px 11px; border-radius:6px; font-weight:700; font-size:0.8rem; }
.badge-sell { background: linear-gradient(135deg,#ff3d5a,#cc2040); color:#fff; padding:3px 11px; border-radius:6px; font-weight:700; font-size:0.8rem; }
.badge-hold { background: #1d2d44; color:#8898aa; padding:3px 11px; border-radius:6px; font-weight:600; font-size:0.8rem; }

/* 알림 */
[data-testid="stAlert"] { border-radius: 10px; border: none; }

/* 진행바 */
[data-testid="stProgress"] > div > div { background: #3d72eb; border-radius: 4px; }

/* expander */
[data-testid="stExpander"] {
    background: #0e1623; border: 1px solid #1d2d44; border-radius: 10px;
}
</style>
""", unsafe_allow_html=True)

# ── 상수 ──────────────────────────────────────────────────────────────────
MAX_WORKERS   = 5
REQUEST_DELAY = 0.3
SIG_COLOR = {"BUY": "#00c896", "SELL": "#ff3d5a", "HOLD": "#5a6a85"}
SIG_LABEL = {"BUY": "▲ 매수", "SELL": "▼ 매도", "HOLD": "─ 관망"}


# ══════════════════════════════════════════════════════════════════════════
# 데이터 수집
# ══════════════════════════════════════════════════════════════════════════

def _build_row(ticker, name, df, result):
    return {
        "ticker":  ticker,
        "name":    name,
        "signal":  result["signal"],
        "score":   result["score"],
        "details": result["details"],
        "close":   df["Close"].iloc[-1],
        "ret5":    (df["Close"].iloc[-1] / df["Close"].iloc[-5]  - 1) * 100 if len(df) >= 5  else 0,
        "ret20":   (df["Close"].iloc[-1] / df["Close"].iloc[-20] - 1) * 100 if len(df) >= 20 else 0,
        "ohlcv":   df,
    }


def _liquidity_ok_kr(df: pd.DataFrame) -> bool:
    """유동성 필터: 20일 평균 거래대금 10억 이상, 주가 2000원 이상"""
    from config import LIQUIDITY_MIN_KRW, PRICE_MIN_KRW
    if len(df) < 20:
        return True  # 데이터 부족 시 통과
    if df["Close"].iloc[-1] < PRICE_MIN_KRW:
        return False  # 동전주 제거
    avg_val = (df["Close"].tail(20) * df["Volume"].tail(20)).mean()
    return avg_val >= LIQUIDITY_MIN_KRW


def _liquidity_ok_us(df: pd.DataFrame) -> bool:
    """유동성 필터: 20일 평균 거래대금 $5M 이상"""
    from config import LIQUIDITY_MIN_USD
    if len(df) < 20:
        return True
    avg_val = (df["Close"].tail(20) * df["Volume"].tail(20)).mean()
    return avg_val >= LIQUIDITY_MIN_USD


def _fetch_kr(args):
    ticker, name, start, end, crawler, use_cache, macro = args
    try:
        df = crawler.get_ohlcv(ticker, start, end, use_cache=use_cache)
        if df is None or df.empty or len(df) < 10:
            return None
        if not _liquidity_ok_kr(df):
            return None
        return _build_row(ticker, name, df, evaluate(df, macro))
    except Exception:
        return None


def _fetch_us(args):
    ticker, name, start, end, use_cache, macro = args
    try:
        from data.us_fetcher import get_ohlcv_us
        df = get_ohlcv_us(ticker, start, end, use_cache=use_cache)
        if df is None or df.empty or len(df) < 10:
            return None
        if not _liquidity_ok_us(df):
            return None
        return _build_row(ticker, name, df, evaluate(df, macro))
    except Exception:
        return None


def _run_scan_parallel(task_args, worker_fn, total, label):
    results = []
    pb  = st.progress(0, text=f"{label} 데이터 수집 중...")
    box = st.empty()
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(worker_fn, arg): arg for arg in task_args}
        for future in as_completed(futures):
            done += 1
            res = future.result()
            if res:
                results.append(res)
            arg = futures[future]
            pb.progress(done / total, text=f"[{done}/{total}] {arg[0]} {arg[1]} — {res['signal'] if res else '실패'}")
            box.caption(f"완료 {done}/{total}  |  성공 {len(results)}")
    pb.empty(); box.empty()
    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results).sort_values("score", ascending=False).reset_index(drop=True)


def run_scan_kospi(days, use_cache):
    from data.fetcher import get_kospi200_tickers
    from data.crawler import NaverFinanceCrawler
    from data.macro_fetcher import fetch_all as _fetch_macro
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=days + 30)).strftime("%Y-%m-%d")
    stocks  = get_kospi200_tickers(use_cache=use_cache)
    crawler = NaverFinanceCrawler(request_delay=REQUEST_DELAY, verify_ssl=False)
    macro   = _fetch_macro(days=30)
    args = [(r["Code"], r["Name"], start, end, crawler, use_cache, macro) for _, r in stocks.iterrows()]
    return _run_scan_parallel(args, _fetch_kr, len(args), "KOSPI 200")


def run_scan_nasdaq(days, use_cache):
    from data.us_fetcher import get_nasdaq100_tickers
    from data.macro_fetcher import fetch_all as _fetch_macro
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=days + 30)).strftime("%Y-%m-%d")
    stocks = get_nasdaq100_tickers(use_cache=use_cache)
    macro  = _fetch_macro(days=30)
    args = [(r["Code"], r["Name"], start, end, use_cache, macro) for _, r in stocks.iterrows()]
    return _run_scan_parallel(args, _fetch_us, len(args), "NASDAQ 100")


# ══════════════════════════════════════════════════════════════════════════
# 백테스팅
# ══════════════════════════════════════════════════════════════════════════

STRATEGY_CHOICES = [
    "이동평균 크로스 (5/20/60+RSI)",
    "RSI (14)",
    "볼린저밴드 (20,2σ)",
    "모멘텀 (60일)",
]


def _make_strategy(name, ticker):
    from backtest.strategies import (
        MovingAverageCrossV2Strategy,
        RSIStrategy, MomentumStrategy, BollingerBandStrategy,
    )
    return {
        "이동평균 크로스 (5/20/60+RSI)": MovingAverageCrossV2Strategy(ticker, 5, 20, 60),
        "RSI (14)":                     RSIStrategy(ticker, period=14),
        "볼린저밴드 (20,2σ)":            BollingerBandStrategy(ticker, window=20),
        "모멘텀 (60일)":                 MomentumStrategy(ticker, lookback=60),
    }[name]


def run_backtest(ticker, market, strategy_name, start_date, end_date, capital):
    from backtest.engine import BacktestEngine

    with st.spinner(f"{ticker} 데이터 불러오는 중..."):
        if market == "KOSPI 200":
            from data.fetcher import get_ohlcv
            df = get_ohlcv(ticker, start_date, end_date)
        else:
            from data.us_fetcher import get_ohlcv_us
            df = get_ohlcv_us(ticker, start_date, end_date, use_cache=True)

    if df is None or df.empty:
        st.error("데이터 수집 실패")
        return None, None, None

    strategy = _make_strategy(strategy_name, ticker)
    with st.spinner("백테스트 실행 중..."):
        # suppress print output
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            engine = BacktestEngine(data={ticker: df}, initial_capital=capital)
            engine.run(strategy)
            metrics = engine.report()

    return engine, metrics, df


# ══════════════════════════════════════════════════════════════════════════
# UI 컴포넌트
# ══════════════════════════════════════════════════════════════════════════

_BADGE_STYLE = {
    "BUY":  "background:linear-gradient(135deg,#00c896,#00a07a);color:#000;padding:3px 12px;border-radius:6px;font-weight:700;font-size:0.8rem;white-space:nowrap;",
    "SELL": "background:linear-gradient(135deg,#ff3d5a,#cc2040);color:#fff;padding:3px 12px;border-radius:6px;font-weight:700;font-size:0.8rem;white-space:nowrap;",
    "HOLD": "background:#1d2d44;color:#8898aa;padding:3px 12px;border-radius:6px;font-weight:600;font-size:0.8rem;white-space:nowrap;",
}

def _badge(signal):
    style = _BADGE_STYLE.get(signal, _BADGE_STYLE["HOLD"])
    return f'<span style="{style}">{SIG_LABEL.get(signal, signal)}</span>'


def stock_table(df: pd.DataFrame, currency: str = "원"):
    if df.empty:
        st.markdown(
            '<div style="background:#0e1623;border:1px solid #1d2d44;border-radius:12px;padding:32px;text-align:center;color:#5a6a85;">해당 종목이 없습니다.</div>',
            unsafe_allow_html=True,
        )
        return
    fmt_close = (lambda v: f"{v:,.2f} {currency}") if currency == "$" else (lambda v: f"{v:,.0f} {currency}")
    rows_html = ""
    for rank, (_, row) in enumerate(df.iterrows(), 1):
        s    = row["score"]
        bc   = "#00c896" if s >= 60 else "#ff3d5a" if s <= 40 else "#f5a623"
        r5c  = "#00c896" if row["ret5"]  >= 0 else "#ff3d5a"
        r20c = "#00c896" if row["ret20"] >= 0 else "#ff3d5a"
        bg   = "#0b1020" if rank % 2 == 0 else "#0e1623"
        badge = _badge(row["signal"])
        rows_html += (
            f'<tr style="border-bottom:1px solid #1d2d44;background:{bg};transition:background 0.15s;">'
            f'<td style="padding:11px 10px;text-align:center;color:#5a6a85;font-size:0.82rem;">{rank}</td>'
            f'<td style="padding:11px 10px;">'
            f'  <div style="font-weight:600;color:#edf2f8;font-size:0.9rem;">{row["name"]}</div>'
            f'  <div style="color:#5a6a85;font-size:0.73rem;margin-top:2px;font-family:monospace;">{row["ticker"]}</div>'
            f'</td>'
            f'<td style="padding:11px 10px;text-align:center;">{badge}</td>'
            f'<td style="padding:11px 16px;min-width:110px;">'
            f'  <div style="background:#131c2e;border-radius:4px;height:4px;margin-bottom:5px;overflow:hidden;">'
            f'    <div style="width:{s}%;background:linear-gradient(90deg,{bc}88,{bc});border-radius:4px;height:4px;"></div>'
            f'  </div>'
            f'  <div style="text-align:center;color:{bc};font-size:0.8rem;font-weight:600;">{s}<span style="color:#5a6a85;font-size:0.7rem;">/100</span></div>'
            f'</td>'
            f'<td style="padding:11px 10px;text-align:right;color:#edf2f8;font-weight:500;font-variant-numeric:tabular-nums;">{fmt_close(row["close"])}</td>'
            f'<td style="padding:11px 10px;text-align:right;color:{r5c};font-weight:600;">{row["ret5"]:+.1f}%</td>'
            f'<td style="padding:11px 10px;text-align:right;color:{r20c};font-weight:600;">{row["ret20"]:+.1f}%</td>'
            f'</tr>'
        )

    html = (
        '<div style="overflow-x:auto;border-radius:12px;border:1px solid #1d2d44;">'
        '<table style="width:100%;border-collapse:collapse;font-size:0.86rem;">'
        '<thead>'
        '<tr style="background:#131c2e;border-bottom:1px solid #1d2d44;">'
        '<th style="padding:10px 10px;text-align:center;color:#5a6a85;font-size:0.75rem;font-weight:600;letter-spacing:0.5px;">순위</th>'
        '<th style="padding:10px 10px;text-align:left;color:#5a6a85;font-size:0.75rem;font-weight:600;letter-spacing:0.5px;">종목</th>'
        '<th style="padding:10px 10px;text-align:center;color:#5a6a85;font-size:0.75rem;font-weight:600;letter-spacing:0.5px;">신호</th>'
        '<th style="padding:10px 10px;text-align:center;color:#5a6a85;font-size:0.75rem;font-weight:600;letter-spacing:0.5px;">점수</th>'
        '<th style="padding:10px 10px;text-align:right;color:#5a6a85;font-size:0.75rem;font-weight:600;letter-spacing:0.5px;">종가</th>'
        '<th style="padding:10px 10px;text-align:right;color:#5a6a85;font-size:0.75rem;font-weight:600;letter-spacing:0.5px;">5일</th>'
        '<th style="padding:10px 10px;text-align:right;color:#5a6a85;font-size:0.75rem;font-weight:600;letter-spacing:0.5px;">20일</th>'
        '</tr>'
        '</thead>'
        f'<tbody>{rows_html}</tbody>'
        '</table>'
        '</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def summary_metrics(df: pd.DataFrame):
    buy_df  = df[df["signal"] == "BUY"]
    sell_df = df[df["signal"] == "SELL"]
    hold_df = df[df["signal"] == "HOLD"]
    avg_score = df['score'].mean()
    buy_pct  = len(buy_df) / len(df) * 100
    sell_pct = len(sell_df) / len(df) * 100

    def _card(label, value, sub, color="#edf2f8", accent="#3d72eb"):
        return (
            f'<div style="background:linear-gradient(135deg,#0e1623 0%,#0b1020 100%);'
            f'border:1px solid #1d2d44;border-radius:12px;padding:18px 20px;'
            f'border-top:3px solid {accent};">'
            f'<div style="color:#8898aa;font-size:0.75rem;font-weight:600;letter-spacing:0.5px;margin-bottom:8px;">{label}</div>'
            f'<div style="color:{color};font-size:1.8rem;font-weight:700;line-height:1;">{value}</div>'
            f'<div style="color:#5a6a85;font-size:0.78rem;margin-top:6px;">{sub}</div>'
            f'</div>'
        )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.markdown(_card("분석 종목", f"{len(df)}", "개", accent="#3d72eb"), unsafe_allow_html=True)
    c2.markdown(_card("매수 신호", f"{len(buy_df)}", f"개  ({buy_pct:.0f}%)", color="#00c896", accent="#00c896"), unsafe_allow_html=True)
    c3.markdown(_card("매도 신호", f"{len(sell_df)}", f"개  ({sell_pct:.0f}%)", color="#ff3d5a", accent="#ff3d5a"), unsafe_allow_html=True)
    c4.markdown(_card("관망", f"{len(hold_df)}", "개", color="#5a6a85", accent="#5a6a85"), unsafe_allow_html=True)
    c5.markdown(_card("평균 점수", f"{avg_score:.1f}", "/ 100점", color="#f5a623", accent="#f5a623"), unsafe_allow_html=True)


def distribution_charts(df: pd.DataFrame):
    buy_df  = df[df["signal"] == "BUY"]
    sell_df = df[df["signal"] == "SELL"]
    hold_df = df[df["signal"] == "HOLD"]
    ch1, ch2 = st.columns([1, 2])
    _bg = "#080c14"
    with ch1:
        st.markdown("#### 신호 분포")
        pie = px.pie(
            values=[len(buy_df), len(hold_df), len(sell_df)],
            names=["매수", "관망", "매도"],
            color_discrete_sequence=["#00c896", "#5a6a85", "#ff3d5a"],
            hole=0.55,
        )
        pie.update_layout(paper_bgcolor=_bg, plot_bgcolor=_bg, font=dict(color="#a0aec0"),
                          margin=dict(l=0,r=0,t=0,b=0), height=240,
                          legend=dict(orientation="h", y=-0.1, x=0.1))
        pie.update_traces(textinfo="percent+label", textfont_size=12)
        st.plotly_chart(pie, use_container_width=True)
    with ch2:
        st.markdown("#### 점수 분포")
        hist = px.histogram(df, x="score", nbins=20, color="signal",
                            color_discrete_map={"BUY":"#00c896","HOLD":"#5a6a85","SELL":"#ff3d5a"},
                            labels={"score":"점수","signal":"신호"},
                            category_orders={"signal":["BUY","HOLD","SELL"]})
        hist.update_layout(paper_bgcolor=_bg, plot_bgcolor=_bg, font=dict(color="#a0aec0"),
                           xaxis=dict(gridcolor="#1d2d44"), yaxis=dict(gridcolor="#1d2d44"),
                           margin=dict(l=0,r=0,t=0,b=0), height=240, bargap=0.1,
                           legend=dict(title="", orientation="h", y=1.08))
        st.plotly_chart(hist, use_container_width=True)


def detail_panel(row: dict, currency: str = "원"):
    fmt = (lambda v: f"${v:,.2f}") if currency == "$" else (lambda v: f"{v:,.0f}원")
    st.markdown(f"### [{row['ticker']}] {row['name']}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("종합 신호",  SIG_LABEL.get(row["signal"], row["signal"]))
    c2.metric("종합 점수",  f"{row['score']}/100")
    c3.metric("5일 수익률",  f"{row['ret5']:+.2f}%")
    c4.metric("20일 수익률", f"{row['ret20']:+.2f}%")
    st.divider()
    st.markdown("**전략별 분석**")
    for d in row["details"]:
        color = SIG_COLOR.get(d["signal"], "#5a6a85")
        bg_color = {"BUY": "rgba(0,200,150,0.06)", "SELL": "rgba(255,61,90,0.06)"}.get(d["signal"], "rgba(29,45,68,0.3)")
        st.markdown(
            f'<div style="background:{bg_color};border:1px solid #1d2d44;border-left:3px solid {color};'
            f'border-radius:10px;padding:12px 16px;margin-bottom:8px;">'
            f'  <div style="display:flex;justify-content:space-between;align-items:center;">'
            f'    <span style="font-weight:600;color:#edf2f8;font-size:0.88rem;">{d["name"]}</span>'
            f'    <span style="color:{color};font-weight:700;font-size:0.88rem;background:rgba(0,0,0,0.2);'
            f'    padding:2px 10px;border-radius:6px;">{SIG_LABEL.get(d["signal"],d["signal"])}  {d["score"]}점</span>'
            f'  </div>'
            f'  <div style="background:#131c2e;border-radius:4px;height:3px;margin:9px 0;overflow:hidden;">'
            f'    <div style="width:{d["score"]}%;background:linear-gradient(90deg,{color}66,{color});border-radius:4px;height:3px;"></div>'
            f'  </div>'
            f'  <div style="color:#8898aa;font-size:0.78rem;">{d["reason"]}</div>'
            f'</div>',
            unsafe_allow_html=True
        )
    ohlcv = row.get("ohlcv")
    if ohlcv is not None and not ohlcv.empty:
        st.markdown("**최근 가격 추이**")
        _bg = "#080c14"
        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=ohlcv.index, open=ohlcv["Open"], high=ohlcv["High"],
            low=ohlcv["Low"], close=ohlcv["Close"],
            increasing_line_color="#00c896", decreasing_line_color="#ff3d5a", name="OHLCV",
        ))
        if len(ohlcv) >= 5:
            fig.add_trace(go.Scatter(x=ohlcv.index, y=ohlcv["Close"].rolling(5).mean(),
                                     line=dict(color="#f6c90e", width=1.2), name="MA5"))
        if len(ohlcv) >= 20:
            fig.add_trace(go.Scatter(x=ohlcv.index, y=ohlcv["Close"].rolling(20).mean(),
                                     line=dict(color="#3b82f6", width=1.2), name="MA20"))
        fig.update_layout(paper_bgcolor=_bg, plot_bgcolor=_bg, font=dict(color="#a0aec0"),
                          xaxis=dict(gridcolor="#1d2d44", rangeslider_visible=False),
                          yaxis=dict(gridcolor="#1d2d44"),
                          margin=dict(l=0,r=0,t=20,b=0), height=300,
                          legend=dict(orientation="h", y=1.08))
        st.plotly_chart(fig, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════
# 백테스트 UI
# ══════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=1800, show_spinner=False)
def _load_market_context(market: str) -> dict:
    """시장 뉴스 맥락 로드 (30분 캐시)"""
    from data.news_fetcher import fetch_market_news
    from data.news_summarizer import summarize_market_context
    news = fetch_market_news(max_articles=15)
    return summarize_market_context(news, market)


def render_market_context(market: str):
    """백테스팅 탭 상단 - 네이버 경제/세계 뉴스 기반 시장 맥락 패널"""
    mkey = "kospi200" if "KOSPI" in market else "nasdaq100"

    col_title, col_btn = st.columns([5, 1])
    with col_title:
        st.markdown("#### 📰 현재 시장 맥락 (네이버 경제·세계)")
    with col_btn:
        if st.button("🔄", key="ctx_refresh", help="뉴스 새로고침"):
            _load_market_context.clear()

    ctx = _load_market_context(mkey)

    if not ctx["summary"]:
        st.caption("ANTHROPIC_API_KEY 미설정 또는 뉴스 수집 실패 — 맥락 분석 생략")
        return

    # ── 센티멘트 게이지 ───────────────────────────────────────────────────
    s = ctx["sentiment"]
    label = ctx["label"]
    s_color = "#00c896" if s > 0.2 else "#ff3d5a" if s < -0.2 else "#f5a623"
    s_pct   = int((s + 1) / 2 * 100)   # -1~1 → 0~100%

    gauge_html = (
        f'<div style="background:#0e1623;border:1px solid #1d2d44;border-radius:12px;padding:16px 20px;margin-bottom:12px;">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">'
        f'  <span style="color:#8898aa;font-size:0.78rem;font-weight:600;letter-spacing:0.5px;">시장 센티멘트</span>'
        f'  <span style="background:{s_color}22;color:{s_color};border:1px solid {s_color}44;'
        f'  padding:3px 12px;border-radius:6px;font-weight:700;font-size:0.85rem;">{label}  {s:+.2f}</span>'
        f'</div>'
        f'<div style="background:#131c2e;border-radius:6px;height:8px;overflow:hidden;">'
        f'  <div style="width:{s_pct}%;background:linear-gradient(90deg,#ff3d5a,#f5a623,#00c896);'
        f'  height:8px;border-radius:6px;transition:width 0.5s;"></div>'
        f'</div>'
        f'<div style="display:flex;justify-content:space-between;margin-top:4px;">'
        f'  <span style="color:#5a6a85;font-size:0.68rem;">약세</span>'
        f'  <span style="color:#5a6a85;font-size:0.68rem;">중립</span>'
        f'  <span style="color:#5a6a85;font-size:0.68rem;">강세</span>'
        f'</div>'
        f'</div>'
    )
    st.markdown(gauge_html, unsafe_allow_html=True)

    # ── 요약 + 리스크/기회 ────────────────────────────────────────────────
    col_l, col_r = st.columns([3, 2])

    with col_l:
        st.markdown(
            f'<div style="background:#0e1623;border:1px solid #1d2d44;border-radius:12px;padding:16px 20px;height:100%;">'
            f'<div style="color:#8898aa;font-size:0.72rem;font-weight:600;letter-spacing:0.5px;margin-bottom:8px;">시장 요약</div>'
            f'<div style="color:#edf2f8;font-size:0.88rem;line-height:1.6;">{ctx["summary"]}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    with col_r:
        risks_html = "".join(
            f'<div style="display:flex;align-items:flex-start;gap:8px;margin-bottom:6px;">'
            f'<span style="color:#ff3d5a;margin-top:2px;">▼</span>'
            f'<span style="color:#cbd5e0;font-size:0.82rem;">{r}</span></div>'
            for r in ctx["risks"]
        )
        opps_html = "".join(
            f'<div style="display:flex;align-items:flex-start;gap:8px;margin-bottom:6px;">'
            f'<span style="color:#00c896;margin-top:2px;">▲</span>'
            f'<span style="color:#cbd5e0;font-size:0.82rem;">{o}</span></div>'
            for o in ctx["opportunities"]
        )
        st.markdown(
            f'<div style="background:#0e1623;border:1px solid #1d2d44;border-radius:12px;padding:16px 20px;">'
            f'<div style="color:#ff3d5a;font-size:0.72rem;font-weight:600;letter-spacing:0.5px;margin-bottom:6px;">주요 리스크</div>'
            f'{risks_html or "<div style=color:#5a6a85;font-size:0.82rem;>—</div>"}'
            f'<div style="color:#00c896;font-size:0.72rem;font-weight:600;letter-spacing:0.5px;margin:10px 0 6px;">기회 요인</div>'
            f'{opps_html or "<div style=color:#5a6a85;font-size:0.82rem;>—</div>"}'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── 전략 적합성 ───────────────────────────────────────────────────────
    if ctx["strategy_fit"]:
        fit_color = "#00c896" if s > 0.1 else "#ff3d5a" if s < -0.1 else "#f5a623"
        st.markdown(
            f'<div style="background:{fit_color}0d;border:1px solid {fit_color}33;'
            f'border-radius:10px;padding:12px 16px;margin-top:10px;">'
            f'<span style="color:{fit_color};font-size:0.72rem;font-weight:700;letter-spacing:0.5px;">전략 적합성 분석</span>'
            f'<div style="color:#edf2f8;font-size:0.85rem;margin-top:6px;line-height:1.6;">{ctx["strategy_fit"]}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown(f'<div style="color:#3d4d63;font-size:0.7rem;margin-top:8px;">네이버 경제·세계 뉴스 {ctx["raw_articles"]}건 분석 · 30분 캐시</div>', unsafe_allow_html=True)
    st.divider()


def render_backtest_tab(df: pd.DataFrame, market: str, currency: str):
    st.markdown("### 📈 백테스팅")
    if df.empty:
        st.info("먼저 스캔을 실행하세요.")
        return

    # ── 시장 뉴스 맥락 패널 ────────────────────────────────────────────────
    render_market_context(market)

    # ── 종목 선택 ──────────────────────────────────────────────────────────
    buy_tickers   = df[df["signal"] == "BUY"][["ticker", "name", "score"]].copy()
    other_tickers = df[df["signal"] != "BUY"][["ticker", "name", "score"]].copy()
    all_tickers   = pd.concat([buy_tickers, other_tickers]).reset_index(drop=True)
    ticker_map, options = {}, []
    for _, r in all_tickers.iterrows():
        opt = f"[{r['ticker']}] {r['name']} (점수:{r['score']})"
        options.append(opt)
        ticker_map[opt] = r["ticker"]

    selected_opt    = st.selectbox("종목 선택", options, help="매수 추천 종목이 위에 표시됩니다")
    selected_ticker = ticker_map[selected_opt]

    st.divider()

    today = datetime.today()

    col_l, col_r = st.columns([2, 2])
    with col_l:
        strategy_name = st.selectbox("전략", STRATEGY_CHOICES)
    with col_r:
        start_date = st.date_input("시작일", value=today - timedelta(days=3*365),
                                   max_value=today - timedelta(days=90))
        end_date   = st.date_input("종료일", value=today, max_value=today)
        capital    = st.number_input("초기 자본금", min_value=100_000,
                                     max_value=1_000_000_000, value=10_000_000,
                                     step=1_000_000, format="%d")

    run_btn = st.button("🚀 백테스트 실행", type="primary", use_container_width=True)

    bt_key = f"{market}_bt_result"
    if run_btn:
        engine, metrics, ohlcv_df = run_backtest(
            selected_ticker, market, strategy_name,
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d"),
            capital,
        )
        if engine and metrics:
            st.session_state[bt_key] = {
                "engine": engine, "metrics": metrics, "ohlcv": ohlcv_df,
                "ticker": selected_ticker, "strategy": strategy_name,
                "market": market, "currency": currency,
            }

    bt = st.session_state.get(bt_key)
    if not bt:
        st.caption("종목과 전략을 선택한 뒤 '백테스트 실행'을 눌러주세요.")
        return
    _render_bt_results(bt)


def _render_wfa(wf: dict, ticker: str, _bg: str):
    """Walk-Forward Analysis 결과 렌더링"""
    summary  = wf.get("summary", {})
    periods  = wf.get("periods", [])
    oos_eq   = wf.get("oos_equity", pd.Series(dtype=float))

    # 요약 지표
    s = st.columns(5)
    s[0].metric("검증 기간 수",   f"{summary.get('검증 기간 수', 0)}개")
    s[1].metric("수익 기간 비율", f"{summary.get('수익 기간 비율(%)', 0):.1f}%")
    s[2].metric("평균 CAGR",      f"{summary.get('평균 CAGR(%)', 0):+.2f}%")
    s[3].metric("평균 샤프",      f"{summary.get('평균 샤프비율', 0):.2f}")
    s[4].metric("평균 PF",        f"{summary.get('평균 PF', 0):.2f}")

    # OOS 자산 곡선
    if not oos_eq.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=oos_eq.index, y=oos_eq,
            name="OOS 누적 자산", line=dict(color="#00c896", width=2),
            fill="tozeroy", fillcolor="rgba(0,200,150,0.06)",
        ))
        fig.add_hline(y=float(oos_eq.iloc[0]), line_color="#4a5568", line_dash="dash")
        fig.update_layout(
            title=dict(text=f"[{ticker}] Walk-Forward OOS 자산 곡선", font=dict(color="#e2e8f0")),
            paper_bgcolor=_bg, plot_bgcolor=_bg, font=dict(color="#a0aec0"),
            xaxis=dict(gridcolor="#1d2d44"), yaxis=dict(gridcolor="#1d2d44"),
            margin=dict(l=0, r=0, t=36, b=0), height=240,
            hovermode="x unified",
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # 기간별 성과 테이블
    if periods:
        pf = pd.DataFrame(periods)
        st.dataframe(
            pf.style.format({
                "CAGR(%)":       "{:+.1f}%",
                "MDD(%)":        "{:.1f}%",
                "샤프비율":       "{:.2f}",
                "Profit Factor": "{:.2f}",
                "승률(%)":       "{:.1f}%",
                "거래횟수":       "{:.0f}",
            }, na_rep="—").applymap(
                lambda v: "color:#00c896" if isinstance(v, (int, float)) and v > 0
                          else ("color:#ff3d5a" if isinstance(v, (int, float)) and v < 0 else ""),
                subset=["CAGR(%)"]
            ),
            use_container_width=True, hide_index=True,
        )


def _render_bt_results(bt: dict):
    engine   = bt["engine"]
    metrics  = bt["metrics"]
    ohlcv    = bt["ohlcv"]
    ticker   = bt["ticker"]
    strategy = bt["strategy"]
    cur      = bt["currency"]
    market   = bt.get("market", "KOSPI 200")
    _bg = "#080c14"

    st.markdown(f"#### 결과: [{ticker}] — {strategy}")
    st.caption("⚡ T+1 Open 체결  |  거래량 기반 슬리피지  |  ATR 손절  |  포지션 50%")

    # ── 핵심 지표 (2행 × 5열) ─────────────────────────────────────────────
    def _dc(v, positive_good=True):
        if v > 0:
            return "normal" if positive_good else "inverse"
        return "inverse" if positive_good else "normal"

    row1 = st.columns(5)
    row1[0].metric("총수익률",   f"{metrics.get('총수익률(%)', 0):+.2f}%",
                   delta_color=_dc(metrics.get("총수익률(%)", 0)))
    row1[1].metric("CAGR",       f"{metrics.get('연환산수익률(CAGR,%)', 0):+.2f}%",
                   delta_color=_dc(metrics.get("연환산수익률(CAGR,%)", 0)))
    row1[2].metric("MDD",        f"{metrics.get('최대낙폭(MDD,%)', 0):.2f}%",
                   delta_color="inverse")
    row1[3].metric("샤프비율",   f"{metrics.get('샤프비율', 0):.2f}")
    row1[4].metric("소르티노",   f"{metrics.get('소르티노비율', 0):.2f}")

    row2 = st.columns(5)
    row2[0].metric("칼마비율",   f"{metrics.get('칼마비율', 0):.2f}")
    row2[1].metric("Profit Factor", f"{metrics.get('Profit Factor', 0):.2f}")
    row2[2].metric("Expectancy", f"{metrics.get('Expectancy(%)', 0):+.2f}%")
    row2[3].metric("승률",       f"{metrics.get('승률(%)', 0):.1f}%")
    row2[4].metric("총거래횟수", f"{metrics.get('총거래횟수', 0)}회")

    st.divider()

    # ── Walk-Forward Analysis ──────────────────────────────────────────────
    wf_key = f"{ticker}_wf"
    wf_col1, wf_col2 = st.columns([3, 1])
    with wf_col1:
        st.markdown("##### Walk-Forward Analysis (과최적화 검증)")
    with wf_col2:
        wf_btn = st.button("📊 WFA 실행 (2Y train / 1Y test)", use_container_width=True,
                           key=f"wf_btn_{ticker}")

    if wf_btn:
        with st.spinner("Walk-Forward 분석 중 (종목당 30초 내외)..."):
            import contextlib as _cl
            import io as _io
            from backtest.comparison import walk_forward_test
            _mk = "kospi200" if "KOSPI" in market else "nasdaq100"
            buf = _io.StringIO()
            with _cl.redirect_stdout(buf):
                wf = walk_forward_test(ohlcv, ticker, capital=metrics.get("초기자본", 10_000_000))
            st.session_state[wf_key] = wf

    wf = st.session_state.get(wf_key)
    if wf:
        if "error" in wf:
            st.warning(f"WFA 오류: {wf['error']}")
        else:
            _render_wfa(wf, ticker, _bg)

    st.divider()

    # 자본금 추이
    equity = engine.results["total_value"]
    # Buy & Hold 계산
    first_price = ohlcv["Close"].iloc[0]
    bah = (ohlcv["Close"] / first_price * metrics["초기자본"]).reindex(equity.index, method="ffill")
    bah_return = (bah.iloc[-1] / metrics["초기자본"] - 1) * 100

    fig_eq = go.Figure()
    fig_eq.add_trace(go.Scatter(
        x=equity.index, y=equity, name="전략 포트폴리오",
        line=dict(color="#3d72eb", width=2), fill="tozeroy",
        fillcolor="rgba(61,114,235,0.06)",
    ))
    fig_eq.add_trace(go.Scatter(
        x=bah.index, y=bah, name=f"Buy & Hold ({bah_return:+.1f}%)",
        line=dict(color="#718096", width=1.5, dash="dot"),
    ))
    fig_eq.add_hline(y=metrics["초기자본"], line_color="#4a5568", line_dash="dash",
                     annotation_text="초기자본", annotation_position="bottom right")
    fig_eq.update_layout(
        title=dict(text="자본금 추이", font=dict(color="#e2e8f0")),
        paper_bgcolor=_bg, plot_bgcolor=_bg, font=dict(color="#a0aec0"),
        xaxis=dict(gridcolor="#1d2d44"), yaxis=dict(gridcolor="#1d2d44"),
        margin=dict(l=0,r=0,t=40,b=0), height=320,
        legend=dict(orientation="h", y=1.12),
        hovermode="x unified",
    )
    st.plotly_chart(fig_eq, use_container_width=True)

    # 낙폭(Drawdown) 차트
    roll_max  = equity.cummax()
    drawdown  = (equity - roll_max) / roll_max * 100
    fig_dd = go.Figure()
    fig_dd.add_trace(go.Scatter(
        x=drawdown.index, y=drawdown, name="낙폭",
        fill="tozeroy", line=dict(color="#f6465d", width=1),
        fillcolor="rgba(246,70,93,0.15)",
    ))
    fig_dd.update_layout(
        title=dict(text="낙폭 (Drawdown)", font=dict(color="#e2e8f0")),
        paper_bgcolor=_bg, plot_bgcolor=_bg, font=dict(color="#a0aec0"),
        xaxis=dict(gridcolor="#1d2d44"), yaxis=dict(gridcolor="#1d2d44", ticksuffix="%"),
        margin=dict(l=0,r=0,t=40,b=0), height=220,
    )
    st.plotly_chart(fig_dd, use_container_width=True)

    # 가격 + 매수/매도 시그널
    orders_df = engine.get_orders()
    fig_price = go.Figure()
    fig_price.add_trace(go.Candlestick(
        x=ohlcv.index, open=ohlcv["Open"], high=ohlcv["High"],
        low=ohlcv["Low"],  close=ohlcv["Close"],
        increasing_line_color="#0ecb81", decreasing_line_color="#f6465d",
        name="가격", showlegend=False,
    ))
    if not orders_df.empty:
        buys  = orders_df[orders_df["action"] == "BUY"]
        sells = orders_df[orders_df["action"] == "SELL"]
        if not buys.empty:
            fig_price.add_trace(go.Scatter(
                x=buys["date"], y=buys["price"],
                mode="markers", name="매수",
                marker=dict(symbol="triangle-up", color="#0ecb81", size=12,
                            line=dict(color="#fff", width=1)),
            ))
        if not sells.empty:
            fig_price.add_trace(go.Scatter(
                x=sells["date"], y=sells["price"],
                mode="markers", name="매도",
                marker=dict(symbol="triangle-down", color="#f6465d", size=12,
                            line=dict(color="#fff", width=1)),
            ))
    fig_price.update_layout(
        title=dict(text="가격 & 매매 시그널", font=dict(color="#e2e8f0")),
        paper_bgcolor=_bg, plot_bgcolor=_bg, font=dict(color="#a0aec0"),
        xaxis=dict(gridcolor="#1d2d44", rangeslider_visible=False),
        yaxis=dict(gridcolor="#1d2d44"),
        margin=dict(l=0,r=0,t=40,b=0), height=340,
        legend=dict(orientation="h", y=1.1),
    )
    st.plotly_chart(fig_price, use_container_width=True)

    # 월별 수익률 바차트
    monthly = equity.resample("ME").last().pct_change().dropna() * 100
    if not monthly.empty:
        colors = ["#0ecb81" if v >= 0 else "#f6465d" for v in monthly.values]
        fig_mo = go.Figure(go.Bar(
            x=[d.strftime("%Y-%m") for d in monthly.index],
            y=monthly.values, name="월별 수익률",
            marker_color=colors,
            text=[f"{v:+.1f}%" for v in monthly.values],
            textposition="outside",
        ))
        fig_mo.update_layout(
            title=dict(text="월별 수익률", font=dict(color="#e2e8f0")),
            paper_bgcolor=_bg, plot_bgcolor=_bg, font=dict(color="#a0aec0", size=11),
            xaxis=dict(gridcolor="#1d2d44"), yaxis=dict(gridcolor="#1d2d44", ticksuffix="%"),
            margin=dict(l=0,r=0,t=40,b=0), height=260, bargap=0.15,
        )
        st.plotly_chart(fig_mo, use_container_width=True)

    # 거래 내역
    if not orders_df.empty:
        with st.expander("📋 거래 내역"):
            show_df = orders_df[["date","action","quantity","price","commission","tax"]].copy()
            show_df.columns = ["일자", "구분", "수량", "체결가", "수수료", "세금"]
            show_df["구분"] = show_df["구분"].map({"BUY":"매수","SELL":"매도"})
            st.dataframe(show_df, use_container_width=True, height=250)


# ══════════════════════════════════════════════════════════════════════════
# 알림 전송
# ══════════════════════════════════════════════════════════════════════════

def _fmt_summary(df: pd.DataFrame, market: str, days: int, currency: str) -> str:
    buy_n  = len(df[df["signal"] == "BUY"])
    sell_n = len(df[df["signal"] == "SELL"])
    hold_n = len(df[df["signal"] == "HOLD"])
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        f"📈 <b>{market} 신호 스캐너</b>\n"
        f"🕐 {now}  |  최근 {days}일 기준\n"
        f"{'─'*30}\n"
        f"📊 분석 종목: <b>{len(df)}개</b>\n"
        f"🟢 매수 신호: <b>{buy_n}개</b>  ({buy_n/len(df)*100:.0f}%)\n"
        f"🔴 매도 신호: <b>{sell_n}개</b>  ({sell_n/len(df)*100:.0f}%)\n"
        f"⚪ 관망:      <b>{hold_n}개</b>  ({hold_n/len(df)*100:.0f}%)\n"
        f"📈 평균 점수: <b>{df['score'].mean():.1f}점</b>\n"
    )


def _fmt_buy_list(df: pd.DataFrame, top_n: int, currency: str) -> str:
    buy_df = df[df["signal"] == "BUY"].head(top_n)
    if buy_df.empty:
        return "🟢 <b>매수 추천</b>\n해당 종목 없음\n"
    sym = "$" if currency == "$" else "원"
    lines = [f"🟢 <b>매수 추천 TOP {min(top_n, len(buy_df))}</b>\n"]
    for rank, (_, row) in enumerate(buy_df.iterrows(), 1):
        strats = [d["name"] for d in row["details"] if d["signal"] == "BUY"]
        close_str = f"${row['close']:,.2f}" if currency == "$" else f"{row['close']:,.0f}원"
        lines.append(
            f"<b>{rank}. {row['name']}</b>  <code>{row['ticker']}</code>\n"
            f"   💯 {row['score']}/100  |  종가 {close_str}\n"
            f"   {'🔺' if row['ret5']>=0 else '🔻'}{row['ret5']:+.1f}%(5일)  "
            f"{'🔺' if row['ret20']>=0 else '🔻'}{row['ret20']:+.1f}%(20일)\n"
            f"   📌 {' · '.join(strats) if strats else '복합신호'}\n"
        )
    return "\n".join(lines)


def _fmt_sell_list(df: pd.DataFrame, top_n: int, currency: str) -> str:
    sell_df = df[df["signal"] == "SELL"].sort_values("score").head(top_n)
    if sell_df.empty:
        return "🔴 <b>매도 추천</b>\n해당 종목 없음\n"
    lines = [f"🔴 <b>매도 추천 TOP {min(top_n, len(sell_df))}</b>\n"]
    for rank, (_, row) in enumerate(sell_df.iterrows(), 1):
        strats = [d["name"] for d in row["details"] if d["signal"] == "SELL"]
        close_str = f"${row['close']:,.2f}" if currency == "$" else f"{row['close']:,.0f}원"
        lines.append(
            f"<b>{rank}. {row['name']}</b>  <code>{row['ticker']}</code>\n"
            f"   💯 {row['score']}/100  |  종가 {close_str}\n"
            f"   {'🔺' if row['ret5']>=0 else '🔻'}{row['ret5']:+.1f}%(5일)  "
            f"{'🔺' if row['ret20']>=0 else '🔻'}{row['ret20']:+.1f}%(20일)\n"
            f"   📌 {' · '.join(strats) if strats else '복합신호'}\n"
        )
    return "\n".join(lines)


def send_telegram(df: pd.DataFrame, market: str, days: int, currency: str, top_n: int):
    bot = TelegramBot(BOT_TOKEN, CHAT_ID)
    ok  = bot.send(_fmt_summary(df, market, days, currency))
    ok &= bot.send(_fmt_buy_list(df, top_n, currency))
    ok &= bot.send(_fmt_sell_list(df, top_n, currency))
    if ok:
        try:
            save_report(market, "telegram", df, top_n=top_n)
        except Exception:
            pass
    return ok


def send_kakao(df: pd.DataFrame, market: str, days: int, currency: str, top_n: int):
    bot = KakaoBot(REST_API_KEY, ACCESS_TOKEN, REFRESH_TOKEN, CLIENT_SECRET)
    # summary
    buy_df  = df[df["signal"] == "BUY"]
    sell_df = df[df["signal"] == "SELL"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    summary = (
        f"[{market} 신호 스캐너]\n"
        f"{now} | 최근 {days}일\n"
        f"{'─'*22}\n"
        f"분석 종목 : {len(df)}개\n"
        f"매수 신호 : {len(buy_df)}개 ({len(buy_df)/len(df)*100:.0f}%)\n"
        f"매도 신호 : {len(sell_df)}개 ({len(sell_df)/len(df)*100:.0f}%)\n"
        f"평균 점수 : {df['score'].mean():.1f}점"
    )
    bot.send_text(summary)

    def _arrow(v):
        return "▲" if v >= 0 else "▼"

    def _fmt_block(label, rows, signal_key):
        lines = [f"{label}\n{'─'*22}"]
        for rank, (_, row) in enumerate(rows.iterrows(), 1):
            strats = [d["name"] for d in row["details"] if d["signal"] == signal_key]
            close_str = f"${row['close']:,.2f}" if currency == "$" else f"{row['close']:,.0f}원"
            lines.append(
                f"\n{rank}. {row['name']} ({row['ticker']})\n"
                f"  점수 {row['score']}/100  |  {close_str}\n"
                f"  {_arrow(row['ret5'])}{row['ret5']:+.1f}%(5일) "
                f"{_arrow(row['ret20'])}{row['ret20']:+.1f}%(20일)\n"
                f"  {' · '.join(strats) if strats else '복합신호'}"
            )
        return "\n".join(lines)

    top_buy = buy_df.head(top_n)
    if not top_buy.empty:
        bot.send_text(_fmt_block(f"★ 매수 추천 TOP{top_n}", top_buy, "BUY"))

    top_sell = sell_df.sort_values("score").head(top_n)
    if not top_sell.empty:
        bot.send_text(_fmt_block(f"★ 매도 추천 TOP{top_n}", top_sell, "SELL"))
    try:
        save_report(market, "kakao", df, top_n=top_n)
    except Exception:
        pass
    return True


# ══════════════════════════════════════════════════════════════════════════
# 스캔 탭 렌더링
# ══════════════════════════════════════════════════════════════════════════

def render_scan_tab(df: pd.DataFrame, top_n: int, currency: str):
    if df.empty:
        st.info("사이드바에서 '🔍 스캔 시작'을 눌러주세요.")
        return

    summary_metrics(df)
    st.divider()
    distribution_charts(df)
    st.divider()

    buy_df  = df[df["signal"] == "BUY"]
    sell_df = df[df["signal"] == "SELL"]

    tab_buy, tab_sell, tab_all, tab_detail = st.tabs([
        f"🟢 매수 추천 ({len(buy_df)})",
        f"🔴 매도 추천 ({len(sell_df)})",
        f"📋 전체 종목 ({len(df)})",
        "🔎 종목 상세",
    ])

    with tab_buy:
        st.markdown(f"#### 매수 추천 상위 {top_n}개")
        stock_table(buy_df.head(top_n), currency)

    with tab_sell:
        st.markdown(f"#### 매도 추천 상위 {top_n}개 (점수 낮은 순)")
        stock_table(sell_df.sort_values("score").head(top_n), currency)

    with tab_all:
        st.markdown("#### 전체 종목")
        search = st.text_input("종목명/코드 검색", placeholder="예: 삼성, 005930, AAPL")
        sig_filter = st.multiselect("신호 필터", ["BUY", "HOLD", "SELL"], default=["BUY", "HOLD", "SELL"])
        filtered = df[df["signal"].isin(sig_filter)]
        if search:
            mask = (filtered["name"].str.contains(search, case=False, na=False) |
                    filtered["ticker"].str.contains(search, case=False, na=False))
            filtered = filtered[mask]
        stock_table(filtered, currency)

    with tab_detail:
        st.markdown("#### 종목 상세 분석")
        opts = [f"[{r['ticker']}] {r['name']}" for _, r in df.iterrows()]
        sel = st.selectbox("종목 선택", opts)
        if sel:
            idx = opts.index(sel)
            detail_panel(df.iloc[idx].to_dict(), currency)


# ══════════════════════════════════════════════════════════════════════════
# 매크로 패널
# ══════════════════════════════════════════════════════════════════════════

_MACRO_ORDER = ["dxy", "gold", "wti", "vix"]

_MACRO_LABEL = {
    "dxy":  "💵 DXY",
    "gold": "🥇 Gold",
    "wti":  "🛢️ WTI",
    "vix":  "😨 VIX",
}


def _load_macro() -> dict:
    """세션에서 로드하거나 새로 수집 (30분 캐시)"""
    cache = st.session_state.get("macro_cache")
    if cache:
        age = (datetime.now() - cache["ts"]).total_seconds()
        if age < 1800:
            return cache["data"]

    from data.macro_fetcher import fetch_all
    data = fetch_all(days=60)
    st.session_state["macro_cache"] = {"data": data, "ts": datetime.now()}
    return data


def _hex_to_rgba(hex_color: str, alpha: float = 0.12) -> str:
    """#rrggbb → rgba(r,g,b,a)"""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _sparkline(series: pd.Series, color: str) -> go.Figure:
    fill_color = _hex_to_rgba(color) if color.startswith("#") else color
    fig = go.Figure(go.Scatter(
        x=series.index, y=series.values,
        mode="lines",
        line=dict(color=color, width=1.5),
        fill="tozeroy",
        fillcolor=fill_color,
    ))
    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        height=55,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        showlegend=False,
    )
    return fig


# ── 뉴스 탭 ──────────────────────────────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_news_cached(ticker: str, market: str) -> list:
    """뉴스 수집 (30분 캐시)"""
    from data.news_fetcher import fetch_news
    return fetch_news(ticker, market, max_articles=5)


def _has_anthropic_key() -> bool:
    """ANTHROPIC_API_KEY 설정 여부 확인 (.env 포함)"""
    import os
    from pathlib import Path as _Path
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return True
    env_path = _Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                val = line.split("=", 1)[1].strip()
                if val and not val.startswith("#"):
                    return True
    return False


def render_news_tab(df: pd.DataFrame, market_key: str, top_n: int):
    st.markdown("### 📰 매수 추천 종목 뉴스")

    if df.empty:
        st.info("먼저 사이드바에서 '🔍 스캔 시작'을 눌러 데이터를 수집하세요.")
        return

    # BUY 신호, score 높은 순으로 top_n개 선택
    buy_df = (
        df[df["signal"] == "BUY"]
        .sort_values("score", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )

    if buy_df.empty:
        st.warning("현재 매수 추천 종목이 없습니다.")
        return

    market_label = "KOSPI 200" if market_key == "kospi200" else "NASDAQ 100"
    st.caption(
        f"{market_label} 기준 매수(BUY) 신호 상위 {len(buy_df)}개 종목 | "
        f"뉴스는 최대 5건씩 표시됩니다 (30분 캐시)"
    )
    st.divider()

    # 종목별 뉴스 수집 및 표시
    stocks_news = []  # AI 요약에 사용할 데이터

    for idx, row in buy_df.iterrows():
        ticker = row["ticker"]
        name   = row["name"]
        score  = row["score"]

        # 종목 헤더
        score_color = "#0ecb81" if score >= 60 else "#ecc94b"
        st.markdown(
            f'<div style="background:#1a1d27;border:1px solid #2d3748;border-radius:10px;'
            f'padding:14px 18px;margin-bottom:4px;">'
            f'<span style="font-weight:700;color:#e2e8f0;font-size:1rem;">{name}</span>'
            f'<span style="color:#4a5568;font-size:0.8rem;margin-left:8px;">{ticker}</span>'
            f'<span style="float:right;color:{score_color};font-weight:700;font-size:0.88rem;">'
            f'★ {score}/100</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # 뉴스 수집
        with st.spinner(f"{name} 뉴스 수집 중..."):
            articles = _fetch_news_cached(ticker, market_key)

        stocks_news.append({"ticker": ticker, "name": name, "articles": articles})

        if not articles:
            st.caption("  뉴스를 가져오지 못했습니다.")
        else:
            for i, art in enumerate(articles, 1):
                date_str = f"  `{art['date']}`" if art.get("date") else ""
                st.markdown(
                    f'<div style="padding:6px 16px;border-left:3px solid #2d3748;margin:4px 0;">'
                    f'<span style="color:#a0aec0;font-size:0.84rem;">{i}. {art["title"]}</span>'
                    f'<span style="color:#4a5568;font-size:0.76rem;margin-left:8px;">{art.get("date","")}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        st.markdown("<div style='margin-bottom:12px;'></div>", unsafe_allow_html=True)

    st.divider()

    # AI 뉴스 요약
    st.markdown("#### 🤖 AI 뉴스 요약")

    has_key = _has_anthropic_key()
    if not has_key:
        st.info(
            "AI 요약 기능을 사용하려면 `.env` 파일에 `ANTHROPIC_API_KEY`를 설정하세요.\n\n"
            "```\nANTHROPIC_API_KEY=sk-ant-...\n```"
        )

    ai_btn = st.button(
        "🤖 AI 뉴스 요약 생성",
        type="primary",
        use_container_width=True,
        disabled=not has_key,
        help="" if has_key else ".env에 ANTHROPIC_API_KEY 설정 필요",
    )

    if ai_btn:
        with st.spinner("Claude AI가 뉴스를 분석하는 중..."):
            from data.news_summarizer import summarize_stocks_news
            summary = summarize_stocks_news(stocks_news, market_key)

        if summary:
            st.markdown(
                f'<div style="background:#1a1d27;border:1px solid #2d3748;border-radius:10px;'
                f'padding:18px 20px;white-space:pre-wrap;color:#e2e8f0;font-size:0.88rem;'
                f'line-height:1.65;">{summary}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.warning("요약 결과를 가져오지 못했습니다. API 키나 anthropic 패키지 설치 상태를 확인하세요.")


# ── 기록 탭 ──────────────────────────────────────────────────────────────────

def render_monitor_tab():
    st.markdown("### 🔍 매도 모니터 현황")

    from notifications.watchlist import load, ALERT_LOG_PATH

    # ── 워치리스트 ────────────────────────────────────────────────────────
    st.markdown("#### 📋 워치리스트 (매수 추천 추적 중)")

    watchlist = load()
    if not watchlist:
        st.info("워치리스트가 비어 있습니다. 아침 리포트 실행 후 BUY 종목이 자동 등록됩니다.")
    else:
        rows = []
        for ticker, info in watchlist.items():
            rows.append({
                "티커":    ticker,
                "종목명":  info.get("name", "-"),
                "점수":    info.get("score", "-"),
                "등록가":  info.get("price", 0),
                "시장":    "KOSPI" if info.get("market", "kospi200") == "kospi200" else "NASDAQ",
                "등록일":  info.get("added_at", "-"),
            })
        wl_df = pd.DataFrame(rows)

        col1, col2 = st.columns(2)
        col1.metric("감시 종목 수", f"{len(wl_df)}개")
        markets = wl_df["시장"].value_counts()
        col2.metric("시장", "  /  ".join([f"{k} {v}개" for k, v in markets.items()]))

        st.dataframe(
            wl_df.style.format({"등록가": "{:,.0f}"}),
            use_container_width=True,
            hide_index=True,
        )

        # 워치리스트 초기화 버튼
        if st.button("🗑️ 워치리스트 초기화", type="secondary"):
            from notifications.watchlist import WATCHLIST_PATH
            import json
            WATCHLIST_PATH.write_text("{}", encoding="utf-8")
            st.success("워치리스트를 초기화했습니다.")
            st.rerun()

    st.divider()

    # ── 매도 알림 이력 ────────────────────────────────────────────────────
    st.markdown("#### 🚨 매도 알림 이력")

    if not ALERT_LOG_PATH.exists():
        st.info("아직 매도 알림이 발송된 적 없습니다. 매도 시그널 발생 시 여기에 기록됩니다.")
    else:
        try:
            log_df = pd.read_csv(ALERT_LOG_PATH, encoding="utf-8-sig")
            if log_df.empty:
                st.info("알림 이력이 없습니다.")
            else:
                log_df["datetime"] = pd.to_datetime(log_df["datetime"])
                log_df = log_df.sort_values("datetime", ascending=False).reset_index(drop=True)

                # 요약 메트릭
                c1, c2, c3 = st.columns(3)
                c1.metric("총 알림 횟수", f"{len(log_df)}회")
                c2.metric("알림 종목 수", f"{log_df['ticker'].nunique()}개")
                c3.metric("최근 알림", log_df["datetime"].iloc[0].strftime("%m/%d %H:%M"))

                # 채널별 색상
                def _channel_badge(ch):
                    color = {"both": "#48bb78", "kakao": "#ecc94b",
                             "telegram": "#63b3ed", "none": "#fc8181"}.get(str(ch), "#a0aec0")
                    label = {"both": "카카오+텔레그램", "kakao": "카카오톡",
                             "telegram": "텔레그램", "none": "실패"}.get(str(ch), ch)
                    return f'<span style="background:{color};color:#1a1d27;padding:2px 8px;border-radius:4px;font-size:12px">{label}</span>'

                # 표 출력
                display_cols = ["datetime", "ticker", "name", "score", "price", "ret5", "regime", "sell_reasons", "channel"]
                display_cols = [c for c in display_cols if c in log_df.columns]
                show_df = log_df[display_cols].copy()
                show_df["datetime"] = show_df["datetime"].dt.strftime("%Y-%m-%d %H:%M")
                show_df.columns = ["시각", "티커", "종목명", "점수", "현재가", "5일수익률", "시장상태", "매도사유", "채널"][:len(display_cols)]

                st.dataframe(
                    show_df.style.format({
                        "현재가":   "{:,.0f}",
                        "5일수익률": "{:+.1f}%",
                        "점수":     "{:.0f}",
                    }, na_rep="-"),
                    use_container_width=True,
                    hide_index=True,
                )

                # 종목별 알림 횟수 차트
                if len(log_df) >= 2:
                    st.markdown("##### 종목별 알림 횟수")
                    cnt = log_df.groupby("name").size().sort_values(ascending=False).reset_index()
                    cnt.columns = ["종목명", "횟수"]
                    fig = px.bar(cnt, x="종목명", y="횟수", color="횟수",
                                 color_continuous_scale="Reds",
                                 template="plotly_dark", height=250)
                    fig.update_layout(margin=dict(t=10, b=10), showlegend=False,
                                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                    st.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.error(f"알림 이력 로드 실패: {e}")

    # ── 새로고침 ──────────────────────────────────────────────────────────
    if st.button("🔄 새로고침"):
        st.rerun()


def render_history_tab():
    st.markdown("### 📋 전송 기록")

    files = list_report_files()
    if not files:
        st.info("아직 저장된 기록이 없습니다. 카카오톡 또는 텔레그램으로 전송하면 자동 저장됩니다.")
        return

    # 날짜 / 시장 필터
    col_mkt, col_date = st.columns(2)
    with col_mkt:
        mkt_opt = st.selectbox("시장", ["전체", "KOSPI 200", "NASDAQ 100"])
    mkt_key = None if mkt_opt == "전체" else ("kospi200" if "KOSPI" in mkt_opt else "nasdaq100")

    filtered_files = [f for f in files if (mkt_key is None or mkt_key in f.stem)]
    date_labels = []
    for f in filtered_files:
        parts = f.stem.split("_", 1)
        mkt_label = "KOSPI200" if "kospi" in f.stem else "NASDAQ100"
        date_labels.append(f"{parts[0]}  [{mkt_label}]")

    if not date_labels:
        st.info("해당 시장의 기록이 없습니다.")
        return

    with col_date:
        selected_label = st.selectbox("날짜", date_labels)

    selected_file = filtered_files[date_labels.index(selected_label)]
    records = load_file(selected_file)

    if not records:
        st.warning("기록 파일을 읽을 수 없습니다.")
        return

    # 전송 회차 선택 (같은 날 여러 번 전송 가능)
    if len(records) > 1:
        rec_labels = [f"{r['sent_at']}  [{r.get('channel','?')}]" for r in records]
        chosen = st.selectbox("전송 시각", rec_labels)
        rec = records[rec_labels.index(chosen)]
    else:
        rec = records[0]

    # 요약 카드
    st.divider()
    mkt_disp = "KOSPI 200" if rec["market"] == "kospi200" else "NASDAQ 100"
    ch_disp  = "📱 카카오톡" if rec.get("channel") == "kakao" else "✈️ 텔레그램"
    st.markdown(
        f'<div style="background:linear-gradient(135deg,#0e1623,#0b1020);border:1px solid #1d2d44;'
        f'border-radius:10px;padding:12px 16px;margin-bottom:12px;">'
        f'<span style="color:#edf2f8;font-weight:600;">{mkt_disp}</span>'
        f'<span style="color:#1d2d44;margin:0 8px;">|</span>'
        f'<span style="color:#8898aa;">{ch_disp}</span>'
        f'<span style="color:#1d2d44;margin:0 8px;">|</span>'
        f'<span style="color:#5a6a85;font-family:monospace;font-size:0.85rem;">{rec["sent_at"]}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("분석 종목", f"{rec['total']}개")
    c2.metric("매수 신호", f"{rec['buy_count']}개")
    c3.metric("매도 신호", f"{rec['sell_count']}개")
    c4.metric("평균 점수", f"{rec['avg_score']}점")

    st.divider()

    # 매수 / 매도 테이블
    currency = "원" if rec["market"] == "kospi200" else "$"
    tab_b, tab_s = st.tabs(["🟢 매수 추천", "🔴 매도 추천"])

    def _fmt_price(v, currency):
        if v is None:
            return "—"
        return f"${v:,.2f}" if currency == "$" else f"{v:,.0f}원"

    def _hist_table(rows, currency, show_perf=False):
        if not rows:
            st.info("해당 신호 없음")
            return
        df_h = pd.DataFrame(rows)
        df_h = df_h.rename(columns={
            "ticker": "티커", "name": "종목명", "score": "점수",
            "signal": "신호", "close": "종가",
            "ret5": "5일(%)", "ret20": "20일(%)",
        })
        close_fmt = "${:,.2f}" if currency == "$" else "{:,.0f}원"
        df_h["종가"] = df_h["종가"].apply(lambda v: close_fmt.format(v))
        df_h["5일(%)"]  = df_h["5일(%)"].apply(lambda v: f"{v:+.1f}%")
        df_h["20일(%)"] = df_h["20일(%)"].apply(lambda v: f"{v:+.1f}%")

        if show_perf:
            if "open_price" in df_h.columns:
                df_h = df_h.rename(columns={"open_price": "추천시가"})
                df_h["추천시가"] = df_h["추천시가"].apply(lambda v: _fmt_price(v, currency))
            if "eod_open" in df_h.columns:
                df_h = df_h.rename(columns={"eod_open": "당일시가"})
                df_h["당일시가"] = df_h["당일시가"].apply(lambda v: _fmt_price(v, currency))
            if "eod_close" in df_h.columns:
                df_h = df_h.rename(columns={"eod_close": "당일종가"})
                df_h["당일종가"] = df_h["당일종가"].apply(lambda v: _fmt_price(v, currency))
            if "eod_pct_change" in df_h.columns:
                df_h = df_h.rename(columns={"eod_pct_change": "시가→종가"})
                df_h["시가→종가"] = df_h["시가→종가"].apply(
                    lambda v: f"{v:+.2f}%" if v is not None else "업데이트 대기"
                )

        drop_cols = ["신호"] + [c for c in df_h.columns if c not in [
            "티커", "종목명", "점수", "추천시가", "종가", "당일시가", "당일종가", "시가→종가", "5일(%)", "20일(%)"
        ]]
        st.dataframe(df_h.drop(columns=[c for c in drop_cols if c in df_h.columns]),
                     use_container_width=True, hide_index=True)

    with tab_b:
        buy_rows = rec.get("top_buy", [])
        has_perf = any("eod_pct_change" in r for r in buy_rows)
        if not has_perf and buy_rows:
            st.caption("⏳ 당일 시가/종가 데이터는 다음 날 아침 리포트 실행 시 자동 업데이트됩니다.")
        _hist_table(buy_rows, currency, show_perf=True)
    with tab_s:
        _hist_table(rec.get("top_sell", []), currency)


def render_macro_panel():
    """헤더 아래 매크로 지표 6개 카드"""
    col_title, col_btn = st.columns([6, 1])
    with col_title:
        st.markdown("#### 📊 주요 매크로 지표")
    with col_btn:
        refresh = st.button("🔄", help="매크로 데이터 새로고침", key="macro_refresh")

    if refresh:
        st.session_state.pop("macro_cache", None)

    if "macro_cache" not in st.session_state:
        with st.spinner("매크로 데이터 로딩 중..."):
            macro = _load_macro()
    else:
        macro = _load_macro()

    cols = st.columns(4)
    for col, key in zip(cols, _MACRO_ORDER):
        d = macro.get(key, {})
        label = _MACRO_LABEL.get(key, key)
        unit  = d.get("unit", "")
        val   = d.get("value")
        chg   = d.get("change")
        up_bad = d.get("up_bad", False)

        with col:
            if val is None:
                body = "불러올 수 없음" if d.get("unavailable") else "—"
                body_style = "font-size:0.78rem;" if d.get("unavailable") else "font-size:1.1rem;"
                st.markdown(
                    f'<div style="background:linear-gradient(135deg,#0e1623 0%,#0b1020 100%);'
                    f'border:1px solid #1d2d44;border-radius:10px;padding:12px 14px;text-align:center;">'
                    f'<div style="color:#8898aa;font-size:0.7rem;font-weight:600;letter-spacing:0.4px;margin-bottom:6px;text-transform:uppercase;">{label}</div>'
                    f'<div style="color:#2d3d55;{body_style}">{body}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                continue

            # 값 포맷
            if unit == "%":
                val_str = f"{val:.2f}%"
                chg_str = f"{chg:+.2f}%p" if chg is not None else ""
            elif unit == "$":
                val_str = f"${val:,.2f}"
                chg_str = f"{chg:+.2f}" if chg is not None else ""
            else:
                val_str = f"{val:,.2f}"
                chg_str = f"{chg:+.2f}" if chg is not None else ""

            # 색상: up_bad=True 이면 상승→빨강, 하락→초록
            if chg is None or chg == 0:
                chg_color = "#718096"
                arrow = "─"
            elif (chg > 0 and not up_bad) or (chg < 0 and up_bad):
                chg_color = "#0ecb81"
                arrow = "▲" if chg > 0 else "▼"
            else:
                chg_color = "#f6465d"
                arrow = "▲" if chg > 0 else "▼"

            st.markdown(
                f'<div style="background:linear-gradient(135deg,#0e1623 0%,#0b1020 100%);'
                f'border:1px solid #1d2d44;border-radius:10px;padding:12px 14px;">'
                f'<div style="color:#8898aa;font-size:0.7rem;font-weight:600;letter-spacing:0.4px;margin-bottom:6px;text-transform:uppercase;">{label}</div>'
                f'<div style="color:#edf2f8;font-size:1.1rem;font-weight:700;letter-spacing:-0.3px;">{val_str}</div>'
                f'<div style="color:{chg_color};font-size:0.76rem;margin-top:4px;font-weight:500;">{arrow} {chg_str}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # 스파크라인
            series = d.get("series", pd.Series(dtype=float))
            if not series.empty:
                line_color = chg_color if chg else "#718096"
                st.plotly_chart(
                    _sparkline(series, line_color),
                    use_container_width=True,
                    config={"displayModeBar": False},
                    key=f"spark_{key}",
                )

    st.divider()


# ══════════════════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════════════════

def main():
    # ── 사이드바 ──────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown(
            '<div style="padding:8px 0 16px;">'
            '<div style="color:#3d72eb;font-size:0.72rem;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:4px;">STOCK SCANNER</div>'
            '<div style="color:#edf2f8;font-size:1.3rem;font-weight:700;">신호 분석 시스템</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        market_choice = st.radio(
            "시장 선택",
            ["🇰🇷 KOSPI 200", "🇺🇸 NASDAQ 100"],
            horizontal=True,
        )
        is_kospi  = market_choice == "🇰🇷 KOSPI 200"
        market    = "KOSPI 200" if is_kospi else "NASDAQ 100"
        currency  = "원" if is_kospi else "$"
        state_key = "kospi_scan" if is_kospi else "nasdaq_scan"

        st.divider()
        st.markdown("### ⚙️ 스캔 설정")
        days      = st.slider("분석 기간 (일)", 30, 120, 60, 10)
        top_n     = st.slider("표시 종목 수",   5,  50,  5,   5)
        use_cache = st.toggle("캐시 사용", value=True,
                              help="OFF 시 모든 데이터를 새로 수집합니다")

        scan_btn = st.button("🔍 스캔 시작", use_container_width=True, type="primary")
        last_time = st.session_state.get(f"{state_key}_time", "없음")
        st.caption(f"마지막 조회: {last_time}")

        st.divider()

        # ── 텔레그램 ──────────────────────────────────────────────────────
        tg_ok = bool(BOT_TOKEN and not BOT_TOKEN.startswith("여기에"))
        tg_label = "### 📨 텔레그램  🟢" if tg_ok else "### 📨 텔레그램  🔴"
        st.markdown(tg_label)
        tg_top = st.slider("전송 종목 수", 3, 20, _TG_TOP, key="tg_top")
        tg_btn = st.button(
            "📨 텔레그램으로 전송", use_container_width=True,
            disabled=not tg_ok,
            help="" if tg_ok else ".env에 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 설정 필요",
        )

        st.divider()

        # ── 카카오톡 ──────────────────────────────────────────────────────
        kk_ok = bool(REST_API_KEY and ACCESS_TOKEN)
        kk_label = "### 💬 카카오톡  🟢" if kk_ok else "### 💬 카카오톡  🔴"
        st.markdown(kk_label)
        kk_top = st.slider("전송 종목 수 ", 3, 20, _KK_TOP, key="kk_top")
        kk_btn = st.button(
            "💬 카카오톡으로 전송", use_container_width=True,
            disabled=not kk_ok,
            help="" if kk_ok else "kakao_setup.py 초기 설정이 필요합니다",
        )

    # ── 헤더 ──────────────────────────────────────────────────────────────
    flag = "🇰🇷" if is_kospi else "🇺🇸"
    now_str = datetime.now().strftime("%Y.%m.%d  %H:%M")
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:4px;">'
        f'<div>'
        f'<div style="color:#3d72eb;font-size:0.72rem;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:6px;">SIGNAL SCANNER</div>'
        f'<h1 style="margin:0;font-size:2rem;font-weight:800;background:linear-gradient(135deg,#edf2f8 40%,#3d72eb 100%);'
        f'-webkit-background-clip:text;-webkit-text-fill-color:transparent;">{flag} {market}</h1>'
        f'<div style="color:#8898aa;font-size:0.82rem;margin-top:4px;">이동평균 · RSI · 볼린저밴드 · MACD · 모멘텀 앙상블</div>'
        f'</div>'
        f'<div style="color:#5a6a85;font-size:0.8rem;text-align:right;padding-bottom:4px;">{now_str}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<hr style="border:none;border-top:1px solid #1d2d44;margin:16px 0 20px;">', unsafe_allow_html=True)

    # ── 매크로 패널 ───────────────────────────────────────────────────────
    render_macro_panel()

    # ── 스캔 실행 (버튼 클릭 시에만) ─────────────────────────────────────
    if scan_btn:
        with st.spinner(f"{market} 전종목 신호 계산 중..."):
            df_new = run_scan_kospi(days, use_cache) if is_kospi else run_scan_nasdaq(days, use_cache)
        if df_new.empty:
            st.error("데이터 수집 실패")
        else:
            st.session_state[f"{state_key}_df"]   = df_new
            st.session_state[f"{state_key}_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            st.session_state[f"{state_key}_days"] = days
            st.success(f"✅ {len(df_new)}개 종목 분석 완료!")

    df = st.session_state.get(f"{state_key}_df", pd.DataFrame())
    days_cur = st.session_state.get(f"{state_key}_days", days)

    # ── 텔레그램 전송 ─────────────────────────────────────────────────────
    if tg_btn:
        if df.empty:
            st.sidebar.error("먼저 스캔을 실행하세요.")
        else:
            with st.sidebar, st.spinner("텔레그램 전송 중..."):
                ok = send_telegram(df, market, days_cur, currency, tg_top)
            if ok:
                st.sidebar.success("✅ 텔레그램 전송 완료!")
            else:
                st.sidebar.error("❌ 전송 실패 — 토큰/채팅ID 확인")

    # ── 카카오 전송 ───────────────────────────────────────────────────────
    if kk_btn:
        if df.empty:
            st.sidebar.error("먼저 스캔을 실행하세요.")
        else:
            with st.sidebar, st.spinner("카카오톡 전송 중..."):
                try:
                    send_kakao(df, market, days_cur, currency, kk_top)
                    st.sidebar.success("✅ 카카오톡 전송 완료!")
                except Exception as e:
                    st.sidebar.error(f"❌ 전송 실패: {e}")

    # ── 메인 탭 ───────────────────────────────────────────────────────────
    market_key = "kospi200" if is_kospi else "nasdaq100"
    tab_scan, tab_bt, tab_hist, tab_news, tab_monitor = st.tabs(
        ["📊 신호 스캔", "📈 백테스팅", "📋 기록", "📰 뉴스", "🔍 모니터"]
    )

    with tab_scan:
        render_scan_tab(df, top_n, currency)

    with tab_bt:
        render_backtest_tab(df, market, currency)

    with tab_hist:
        render_history_tab()

    with tab_news:
        render_news_tab(df, market_key, top_n)

    with tab_monitor:
        render_monitor_tab()


if __name__ == "__main__":
    main()
