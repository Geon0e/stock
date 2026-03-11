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
.stApp { background-color: #0e1117; }
[data-testid="metric-container"] {
    background: #1a1d27; border: 1px solid #2d3748;
    border-radius: 12px; padding: 14px 18px;
}
[data-testid="stMetricValue"] { font-size: 1.8rem !important; font-weight: 700; }
.badge-buy  { background:#0ecb81; color:#000; padding:3px 10px; border-radius:6px; font-weight:700; font-size:0.82rem; }
.badge-sell { background:#f6465d; color:#fff; padding:3px 10px; border-radius:6px; font-weight:700; font-size:0.82rem; }
.badge-hold { background:#4a5568; color:#fff; padding:3px 10px; border-radius:6px; font-weight:700; font-size:0.82rem; }
.strategy-card { background:#1a1d27; border:1px solid #2d3748; border-radius:10px; padding:12px 16px; margin-bottom:8px; }
.strategy-name { font-weight:600; color:#e2e8f0; font-size:0.88rem; }
.strategy-reason { color:#718096; font-size:0.78rem; margin-top:4px; }
.metric-card { background:#1a1d27; border:1px solid #2d3748; border-radius:10px; padding:16px; text-align:center; }
.metric-value { font-size:1.6rem; font-weight:700; color:#e2e8f0; }
.metric-label { font-size:0.78rem; color:#718096; margin-top:4px; }
h2,h3 { color:#e2e8f0 !important; }
</style>
""", unsafe_allow_html=True)

# ── 상수 ──────────────────────────────────────────────────────────────────
MAX_WORKERS   = 5
REQUEST_DELAY = 0.3
SIG_COLOR = {"BUY": "#0ecb81", "SELL": "#f6465d", "HOLD": "#718096"}
SIG_LABEL = {"BUY": "★ 매수", "SELL": "▼ 매도", "HOLD": "─ 관망"}


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


def _fetch_kr(args):
    ticker, name, start, end, crawler, use_cache = args
    try:
        df = crawler.get_ohlcv(ticker, start, end, use_cache=use_cache)
        if df is None or df.empty or len(df) < 10:
            return None
        return _build_row(ticker, name, df, evaluate(df))
    except Exception:
        return None


def _fetch_us(args):
    ticker, name, start, end, use_cache = args
    try:
        from data.us_fetcher import get_ohlcv_us
        df = get_ohlcv_us(ticker, start, end, use_cache=use_cache)
        if df is None or df.empty or len(df) < 10:
            return None
        return _build_row(ticker, name, df, evaluate(df))
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
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=days + 30)).strftime("%Y-%m-%d")
    stocks  = get_kospi200_tickers(use_cache=use_cache)
    crawler = NaverFinanceCrawler(request_delay=REQUEST_DELAY, verify_ssl=False)
    args = [(r["Code"], r["Name"], start, end, crawler, use_cache) for _, r in stocks.iterrows()]
    return _run_scan_parallel(args, _fetch_kr, len(args), "KOSPI 200")


def run_scan_nasdaq(days, use_cache):
    from data.us_fetcher import get_nasdaq100_tickers
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=days + 30)).strftime("%Y-%m-%d")
    stocks = get_nasdaq100_tickers(use_cache=use_cache)
    args = [(r["Code"], r["Name"], start, end, use_cache) for _, r in stocks.iterrows()]
    return _run_scan_parallel(args, _fetch_us, len(args), "NASDAQ 100")


# ══════════════════════════════════════════════════════════════════════════
# 백테스팅
# ══════════════════════════════════════════════════════════════════════════

STRATEGY_CHOICES = [
    "이동평균 크로스 V1 (5/20)",
    "이동평균 크로스 V2 (5/20/60+RSI)",
    "이동평균 크로스 (20/60)",
    "RSI (14)",
    "볼린저밴드 (20,2σ)",
    "모멘텀 (60일)",
]


def _make_strategy(name, ticker):
    from backtest.strategies import (
        MovingAverageCrossStrategy, MovingAverageCrossV2Strategy,
        RSIStrategy, MomentumStrategy, BollingerBandStrategy,
    )
    return {
        "이동평균 크로스 V1 (5/20)":       MovingAverageCrossStrategy(ticker, 5,  20),
        "이동평균 크로스 V2 (5/20/60+RSI)": MovingAverageCrossV2Strategy(ticker, 5, 20, 60),
        "이동평균 크로스 (20/60)":          MovingAverageCrossStrategy(ticker, 20, 60),
        "RSI (14)":                        RSIStrategy(ticker, period=14),
        "볼린저밴드 (20,2σ)":               BollingerBandStrategy(ticker, window=20),
        "모멘텀 (60일)":                    MomentumStrategy(ticker, lookback=60),
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
    "BUY":  "background:#0ecb81;color:#000;padding:3px 12px;border-radius:6px;font-weight:700;font-size:0.82rem;white-space:nowrap;",
    "SELL": "background:#f6465d;color:#fff;padding:3px 12px;border-radius:6px;font-weight:700;font-size:0.82rem;white-space:nowrap;",
    "HOLD": "background:#4a5568;color:#e2e8f0;padding:3px 12px;border-radius:6px;font-weight:700;font-size:0.82rem;white-space:nowrap;",
}

def _badge(signal):
    style = _BADGE_STYLE.get(signal, _BADGE_STYLE["HOLD"])
    return f'<span style="{style}">{SIG_LABEL.get(signal, signal)}</span>'


def stock_table(df: pd.DataFrame, currency: str = "원"):
    if df.empty:
        st.info("해당 종목이 없습니다.")
        return
    fmt_close = (lambda v: f"{v:,.2f} {currency}") if currency == "$" else (lambda v: f"{v:,.0f} {currency}")
    rows_html = ""
    for rank, (_, row) in enumerate(df.iterrows(), 1):
        s    = row["score"]
        bc   = "#0ecb81" if s >= 60 else "#f6465d" if s <= 40 else "#ecc94b"
        r5c  = "#0ecb81" if row["ret5"]  >= 0 else "#f6465d"
        r20c = "#0ecb81" if row["ret20"] >= 0 else "#f6465d"
        bg   = "#141720" if rank % 2 == 0 else "#0e1117"
        rows_html += (
            f'<tr style="border-bottom:1px solid #1e2432;background:{bg};">'
            f'<td style="padding:9px 8px;text-align:center;color:#718096;">{rank}</td>'
            f'<td style="padding:9px 8px;">'
            f'  <span style="font-weight:600;color:#e2e8f0;">{row["name"]}</span>'
            f'  <span style="color:#4a5568;font-size:0.76rem;margin-left:6px;">{row["ticker"]}</span>'
            f'</td>'
            f'<td style="padding:9px 8px;text-align:center;">{_badge(row["signal"])}</td>'
            f'<td style="padding:9px 8px;min-width:100px;">'
            f'  <div style="background:#2d3748;border-radius:4px;height:5px;margin-bottom:3px;">'
            f'    <div style="width:{s}%;background:{bc};border-radius:4px;height:5px;"></div>'
            f'  </div>'
            f'  <div style="text-align:center;color:#a0aec0;font-size:0.78rem;">{s}/100</div>'
            f'</td>'
            f'<td style="padding:9px 8px;text-align:right;color:#e2e8f0;">{fmt_close(row["close"])}</td>'
            f'<td style="padding:9px 8px;text-align:right;color:{r5c};font-weight:600;">{row["ret5"]:+.1f}%</td>'
            f'<td style="padding:9px 8px;text-align:right;color:{r20c};font-weight:600;">{row["ret20"]:+.1f}%</td>'
            f'</tr>'
        )

    html = (
        '<div style="overflow-x:auto;">'
        '<table style="width:100%;border-collapse:collapse;font-size:0.86rem;">'
        '<thead>'
        '<tr style="border-bottom:2px solid #2d3748;color:#718096;background:#0e1117;">'
        '<th style="padding:9px 8px;text-align:center;">순위</th>'
        '<th style="padding:9px 8px;text-align:left;">종목</th>'
        '<th style="padding:9px 8px;text-align:center;">신호</th>'
        '<th style="padding:9px 8px;text-align:center;">점수</th>'
        '<th style="padding:9px 8px;text-align:right;">종가</th>'
        '<th style="padding:9px 8px;text-align:right;">5일</th>'
        '<th style="padding:9px 8px;text-align:right;">20일</th>'
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
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("📊 분석 종목",  f"{len(df)}개")
    c2.metric("🟢 매수 신호",  f"{len(buy_df)}개",  delta=f"{len(buy_df)/len(df)*100:.0f}%")
    c3.metric("🔴 매도 신호",  f"{len(sell_df)}개", delta=f"-{len(sell_df)/len(df)*100:.0f}%", delta_color="inverse")
    c4.metric("⚪ 관망",       f"{len(hold_df)}개")
    c5.metric("📈 평균 점수",  f"{df['score'].mean():.1f}점")


def distribution_charts(df: pd.DataFrame):
    buy_df  = df[df["signal"] == "BUY"]
    sell_df = df[df["signal"] == "SELL"]
    hold_df = df[df["signal"] == "HOLD"]
    ch1, ch2 = st.columns([1, 2])
    _bg = "#0e1117"
    with ch1:
        st.markdown("#### 신호 분포")
        pie = px.pie(
            values=[len(buy_df), len(hold_df), len(sell_df)],
            names=["매수", "관망", "매도"],
            color_discrete_sequence=["#0ecb81", "#718096", "#f6465d"],
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
                            color_discrete_map={"BUY":"#0ecb81","HOLD":"#718096","SELL":"#f6465d"},
                            labels={"score":"점수","signal":"신호"},
                            category_orders={"signal":["BUY","HOLD","SELL"]})
        hist.update_layout(paper_bgcolor=_bg, plot_bgcolor=_bg, font=dict(color="#a0aec0"),
                           xaxis=dict(gridcolor="#1e2432"), yaxis=dict(gridcolor="#1e2432"),
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
        color = SIG_COLOR.get(d["signal"], "#718096")
        st.markdown(
            f'<div style="background:#1a1d27;border:1px solid #2d3748;border-radius:10px;padding:12px 16px;margin-bottom:8px;">'
            f'  <div style="display:flex;justify-content:space-between;align-items:center;">'
            f'    <span style="font-weight:600;color:#e2e8f0;font-size:0.88rem;">{d["name"]}</span>'
            f'    <span style="color:{color};font-weight:700;font-size:0.88rem;">{SIG_LABEL.get(d["signal"],d["signal"])}  {d["score"]}점</span>'
            f'  </div>'
            f'  <div style="background:#2d3748;border-radius:4px;height:5px;margin:7px 0;">'
            f'    <div style="width:{d["score"]}%;background:{color};border-radius:4px;height:5px;"></div>'
            f'  </div>'
            f'  <div style="color:#718096;font-size:0.78rem;margin-top:4px;">{d["reason"]}</div>'
            f'</div>',
            unsafe_allow_html=True
        )
    ohlcv = row.get("ohlcv")
    if ohlcv is not None and not ohlcv.empty:
        st.markdown("**최근 가격 추이**")
        _bg = "#0e1117"
        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=ohlcv.index, open=ohlcv["Open"], high=ohlcv["High"],
            low=ohlcv["Low"], close=ohlcv["Close"],
            increasing_line_color="#0ecb81", decreasing_line_color="#f6465d", name="OHLCV",
        ))
        if len(ohlcv) >= 5:
            fig.add_trace(go.Scatter(x=ohlcv.index, y=ohlcv["Close"].rolling(5).mean(),
                                     line=dict(color="#f6c90e", width=1.2), name="MA5"))
        if len(ohlcv) >= 20:
            fig.add_trace(go.Scatter(x=ohlcv.index, y=ohlcv["Close"].rolling(20).mean(),
                                     line=dict(color="#3b82f6", width=1.2), name="MA20"))
        fig.update_layout(paper_bgcolor=_bg, plot_bgcolor=_bg, font=dict(color="#a0aec0"),
                          xaxis=dict(gridcolor="#1e2432", rangeslider_visible=False),
                          yaxis=dict(gridcolor="#1e2432"),
                          margin=dict(l=0,r=0,t=20,b=0), height=300,
                          legend=dict(orientation="h", y=1.08))
        st.plotly_chart(fig, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════
# 백테스트 UI
# ══════════════════════════════════════════════════════════════════════════

def render_backtest_tab(df: pd.DataFrame, market: str, currency: str):
    st.markdown("### 📈 백테스팅")
    if df.empty:
        st.info("먼저 스캔을 실행하세요.")
        return

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

    # ── 모드 선택 ──────────────────────────────────────────────────────────
    mode = st.radio("모드", ["단일 전략", "V1 vs V2 비교"], horizontal=True)
    st.divider()

    today = datetime.today()

    # ════════════════════════════════════════════════════════════════════════
    # 단일 전략 모드
    # ════════════════════════════════════════════════════════════════════════
    if mode == "단일 전략":
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

    # ════════════════════════════════════════════════════════════════════════
    # V1 vs V2 비교 모드
    # ════════════════════════════════════════════════════════════════════════
    else:
        col_l, col_r = st.columns([2, 2])
        with col_l:
            start_date = st.date_input("시작일", value=today - timedelta(days=3*365),
                                       max_value=today - timedelta(days=90),
                                       key="cmp_start")
            end_date   = st.date_input("종료일", value=today, max_value=today,
                                       key="cmp_end")
        with col_r:
            capital = st.number_input("초기 자본금", min_value=100_000,
                                      max_value=1_000_000_000, value=10_000_000,
                                      step=1_000_000, format="%d", key="cmp_cap")
            slip_base = st.number_input("기준 슬리피지 (%)", min_value=0.01,
                                        max_value=1.0, value=0.1, step=0.01,
                                        format="%.2f", key="cmp_slip") / 100

        cmp_btn = st.button("🔬 V1 vs V2 비교 실행", type="primary", use_container_width=True)

        cmp_key = f"{market}_cmp_result_{selected_ticker}"
        if cmp_btn:
            with st.spinner("데이터 불러오는 중..."):
                if market == "KOSPI 200":
                    from data.fetcher import get_ohlcv
                    ohlcv = get_ohlcv(selected_ticker,
                                      start_date.strftime("%Y-%m-%d"),
                                      end_date.strftime("%Y-%m-%d"))
                else:
                    from data.us_fetcher import get_ohlcv_us
                    ohlcv = get_ohlcv_us(selected_ticker,
                                         start_date.strftime("%Y-%m-%d"),
                                         end_date.strftime("%Y-%m-%d"), use_cache=True)

            if ohlcv is None or ohlcv.empty:
                st.error("데이터 수집 실패")
            elif len(ohlcv) < 120:
                st.warning(f"데이터가 부족합니다 ({len(ohlcv)}일). 최소 120일 이상 필요합니다.")
            else:
                with st.spinner("V1 / V2 / Buy&Hold 비교 실행 중..."):
                    from backtest.comparison import run_comparison
                    import contextlib, io as _io
                    buf = _io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        cmp = run_comparison(
                            ohlcv, selected_ticker, capital=capital,
                            base_slippage=slip_base,
                        )
                    cmp["_ohlcv"] = ohlcv
                    st.session_state[cmp_key] = cmp

        cmp = st.session_state.get(cmp_key)
        if not cmp:
            st.caption("종목을 선택하고 '비교 실행'을 눌러주세요.")
            return
        _render_comparison(cmp, selected_ticker, currency)


# ── V1 vs V2 비교 렌더링 ──────────────────────────────────────────────────────

def _render_comparison(cmp: dict, ticker: str, currency: str):
    import plotly.graph_objects as go

    summary   = cmp["summary"]
    multi_df  = cmp["multi_period"]
    slip_df   = cmp["slippage_sens"]
    robust    = cmp["robustness"]
    eq_v1     = cmp["equity_full"]["v1"]
    eq_v2     = cmp["equity_full"]["v2"]
    ohlcv     = cmp.get("_ohlcv", pd.DataFrame())

    v1 = summary.get("v1", {})
    v2 = summary.get("v2", {})
    bh = summary.get("bh", {})

    # ── 요약 지표 카드 ────────────────────────────────────────────────────
    st.markdown(f"#### [{ticker}] V1 vs V2 vs Buy & Hold")

    labels = ["CAGR(%)", "MDD(%)", "샤프비율", "연간거래횟수", "승률(%)"]
    col_lbl, col_v1, col_v2, col_bh = st.columns([2, 1.5, 1.5, 1.5])
    col_lbl.markdown("**지표**")
    col_v1.markdown("**V1 (MA5/20)**")
    col_v2.markdown("**V2 (MA5/20/60)**")
    col_bh.markdown("**Buy & Hold**")

    for lbl in labels:
        v1v = v1.get(lbl)
        v2v = v2.get(lbl)
        bhv = bh.get(lbl)

        def _fmt(v):
            if v is None: return "—"
            if lbl in ("거래횟수", "연간거래횟수"): return f"{v:.0f}"
            return f"{v:+.1f}" if lbl in ("CAGR(%)", "MDD(%)") else f"{v:.2f}" if lbl == "샤프비율" else f"{v:.1f}%"

        def _color(v, ref):
            if v is None or ref is None: return ""
            better = v > ref if lbl != "MDD(%)" else v > ref  # MDD: 덜 음수가 좋음
            return "color:#0ecb81" if better else "color:#f6465d"

        col_lbl.markdown(lbl)
        col_v1.markdown(f'<span style="{_color(v1v, bhv)}">{_fmt(v1v)}</span>', unsafe_allow_html=True)
        col_v2.markdown(f'<span style="{_color(v2v, bhv)}">{_fmt(v2v)}</span>', unsafe_allow_html=True)
        col_bh.markdown(f"{_fmt(bhv)}")

    st.divider()

    # ── 자산 곡선 비교 ────────────────────────────────────────────────────
    if not eq_v1.empty or not eq_v2.empty:
        fig = go.Figure()
        capital = v1.get("최종자본", 10_000_000)
        init    = 10_000_000  # 초기 자본 (정규화)

        if not eq_v1.empty:
            fig.add_trace(go.Scatter(x=eq_v1.index, y=eq_v1 / eq_v1.iloc[0] * 100,
                                     name="V1", line=dict(color="#f7931a", width=2)))
        if not eq_v2.empty:
            fig.add_trace(go.Scatter(x=eq_v2.index, y=eq_v2 / eq_v2.iloc[0] * 100,
                                     name="V2", line=dict(color="#0ecb81", width=2)))
        if not ohlcv.empty:
            bh_norm = ohlcv["Close"] / ohlcv["Close"].iloc[0] * 100
            fig.add_trace(go.Scatter(x=bh_norm.index, y=bh_norm,
                                     name="Buy & Hold", line=dict(color="#718096", width=1.5, dash="dot")))

        fig.update_layout(
            title="자산 곡선 비교 (초기=100 기준)",
            height=360,
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#e2e8f0"),
            legend=dict(bgcolor="rgba(0,0,0,0)"),
            xaxis=dict(gridcolor="#2d3748"),
            yaxis=dict(gridcolor="#2d3748", ticksuffix=""),
            margin=dict(l=0, r=0, t=36, b=0),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # ── 다기간 성과 테이블 ────────────────────────────────────────────────
    st.markdown("#### 📅 다기간 일관성")
    if not multi_df.empty:
        st.dataframe(
            multi_df.style.format({
                "CAGR(%)": "{:+.1f}%",
                "MDD(%)":  "{:.1f}%",
                "샤프비율": "{:.2f}",
                "연간거래횟수": "{:.0f}",
                "승률(%)": "{:.1f}%",
            }, na_rep="—"),
            use_container_width=True, hide_index=True,
        )

    st.divider()

    # ── 슬리피지 민감도 ───────────────────────────────────────────────────
    st.markdown("#### 💧 슬리피지 민감도")
    if not slip_df.empty:
        st.dataframe(
            slip_df.style.format({
                "CAGR(%)": "{:+.1f}%",
                "MDD(%)":  "{:.1f}%",
                "거래횟수": "{:.0f}",
            }, na_rep="—"),
            use_container_width=True, hide_index=True,
        )

    st.divider()

    # ── 로버스트니스 체크리스트 ───────────────────────────────────────────
    st.markdown("#### ✅ 로버스트니스 체크리스트")
    if robust:
        rob_df = pd.DataFrame(robust)
        st.dataframe(rob_df, use_container_width=True, hide_index=True)

        v1_pass = sum(1 for r in robust if r["V1"].startswith("✅"))
        v2_pass = sum(1 for r in robust if r["V2"].startswith("✅"))
        total   = len(robust)
        c1, c2 = st.columns(2)
        c1.metric("V1 통과", f"{v1_pass}/{total}",
                  delta="통과" if v1_pass >= total * 0.6 else "미통과",
                  delta_color="normal" if v1_pass >= total * 0.6 else "inverse")
        c2.metric("V2 통과", f"{v2_pass}/{total}",
                  delta="통과" if v2_pass >= total * 0.6 else "미통과",
                  delta_color="normal" if v2_pass >= total * 0.6 else "inverse")


def _render_bt_results(bt: dict):
    engine   = bt["engine"]
    metrics  = bt["metrics"]
    ohlcv    = bt["ohlcv"]
    ticker   = bt["ticker"]
    strategy = bt["strategy"]
    cur      = bt["currency"]
    _bg = "#0e1117"

    st.markdown(f"#### 결과: [{ticker}] — {strategy}")

    # 지표 카드
    def _delta_color(v, positive_good=True):
        if v > 0:
            return "normal" if positive_good else "inverse"
        return "inverse" if positive_good else "normal"

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("총수익률",    f"{metrics.get('총수익률(%)', 0):+.2f}%",
              delta_color=_delta_color(metrics.get("총수익률(%)", 0)))
    c2.metric("CAGR",        f"{metrics.get('연환산수익률(CAGR,%)', 0):+.2f}%",
              delta_color=_delta_color(metrics.get("연환산수익률(CAGR,%)", 0)))
    c3.metric("MDD",         f"{metrics.get('최대낙폭(MDD,%)', 0):.2f}%",
              delta_color="inverse")
    c4.metric("샤프비율",    f"{metrics.get('샤프비율', 0):.2f}")
    c5.metric("승률",        f"{metrics.get('승률(%)', 0):.1f}%")
    c6.metric("총거래횟수",  f"{metrics.get('총거래횟수', 0)}회")

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
        line=dict(color="#0ecb81", width=2), fill="tozeroy",
        fillcolor="rgba(14,203,129,0.05)",
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
        xaxis=dict(gridcolor="#1e2432"), yaxis=dict(gridcolor="#1e2432"),
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
        xaxis=dict(gridcolor="#1e2432"), yaxis=dict(gridcolor="#1e2432", ticksuffix="%"),
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
        xaxis=dict(gridcolor="#1e2432", rangeslider_visible=False),
        yaxis=dict(gridcolor="#1e2432"),
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
            xaxis=dict(gridcolor="#1e2432"), yaxis=dict(gridcolor="#1e2432", ticksuffix="%"),
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

_MACRO_ORDER = ["us2y", "us10y", "dxy", "gold", "wti", "vix"]

_MACRO_LABEL = {
    "us2y":  "🇺🇸 2년물 금리",
    "us10y": "🇺🇸 10년물 금리",
    "dxy":   "💵 DXY",
    "gold":  "🥇 Gold",
    "wti":   "🛢️ WTI",
    "vix":   "😨 VIX",
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


# ── 기록 탭 ──────────────────────────────────────────────────────────────────

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
        f"**{mkt_disp}** | {ch_disp} | 전송: `{rec['sent_at']}`"
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

    def _hist_table(rows, currency):
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
        st.dataframe(df_h.drop(columns=["신호"]), use_container_width=True, hide_index=True)

    with tab_b:
        _hist_table(rec.get("top_buy", []), currency)
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

    cols = st.columns(6)
    for col, key in zip(cols, _MACRO_ORDER):
        d = macro.get(key, {})
        label = _MACRO_LABEL.get(key, key)
        unit  = d.get("unit", "")
        val   = d.get("value")
        chg   = d.get("change")
        up_bad = d.get("up_bad", False)

        with col:
            if val is None:
                st.markdown(
                    f'<div style="background:#1a1d27;border:1px solid #2d3748;border-radius:10px;padding:12px 14px;text-align:center;">'
                    f'<div style="color:#718096;font-size:0.75rem;margin-bottom:4px;">{label}</div>'
                    f'<div style="color:#4a5568;font-size:1.1rem;">—</div>'
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
                f'<div style="background:#1a1d27;border:1px solid #2d3748;border-radius:10px;padding:12px 14px;">'
                f'<div style="color:#718096;font-size:0.72rem;margin-bottom:4px;">{label}</div>'
                f'<div style="color:#e2e8f0;font-size:1.15rem;font-weight:700;">{val_str}</div>'
                f'<div style="color:{chg_color};font-size:0.78rem;margin-top:3px;">{arrow} {chg_str}</div>'
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
        st.markdown("## 📈 주식 신호 스캐너")

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
    st.markdown(f"# {flag} {market} 매수/매도 신호 스캐너")
    st.caption("5가지 기술 지표(이동평균·RSI·볼린저밴드·MACD·모멘텀) 앙상블 신호")
    st.divider()

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
    tab_scan, tab_bt, tab_hist = st.tabs(["📊 신호 스캔", "📈 백테스팅", "📋 기록"])

    with tab_scan:
        render_scan_tab(df, top_n, currency)

    with tab_bt:
        render_backtest_tab(df, market, currency)

    with tab_hist:
        render_history_tab()


if __name__ == "__main__":
    main()
