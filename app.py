"""
KOSPI 200 매수/매도 신호 대시보드
실행: streamlit run app.py
"""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from data.fetcher import get_kospi200_tickers
from data.naver_crawler import NaverFinanceCrawler
from stock_signal import evaluate
from telegram_bot import (
    TelegramBot, format_summary, format_buy_list, format_sell_list,
    BOT_TOKEN, CHAT_ID, TOP_N,
)
from kakao_bot import (
    KakaoBot, send_report as kakao_send_report,
    REST_API_KEY, ACCESS_TOKEN, REFRESH_TOKEN,
)

# ── 페이지 설정 ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="KOSPI 200 신호 스캐너",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 스타일 ────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* 전체 배경 */
.stApp { background-color: #0e1117; }

/* 메트릭 카드 */
[data-testid="metric-container"] {
    background: #1a1d27;
    border: 1px solid #2d3748;
    border-radius: 12px;
    padding: 16px 20px;
}
[data-testid="stMetricValue"] { font-size: 2rem !important; font-weight: 700; }

/* 매수/매도 배지 */
.badge-buy  { background:#0ecb81; color:#000; padding:3px 10px; border-radius:6px; font-weight:700; font-size:0.85rem; }
.badge-sell { background:#f6465d; color:#fff; padding:3px 10px; border-radius:6px; font-weight:700; font-size:0.85rem; }
.badge-hold { background:#4a5568; color:#fff; padding:3px 10px; border-radius:6px; font-weight:700; font-size:0.85rem; }

/* 점수 바 */
.score-bar-wrap { background:#2d3748; border-radius:4px; height:8px; width:100%; }
.score-bar      { border-radius:4px; height:8px; }

/* 섹션 헤더 */
h2 { color: #e2e8f0 !important; }
h3 { color: #a0aec0 !important; }

/* 전략 카드 */
.strategy-card {
    background: #1a1d27;
    border: 1px solid #2d3748;
    border-radius: 10px;
    padding: 14px 18px;
    margin-bottom: 8px;
}
.strategy-name { font-weight:600; color:#e2e8f0; font-size:0.9rem; }
.strategy-reason { color:#718096; font-size:0.8rem; margin-top:4px; }
</style>
""", unsafe_allow_html=True)


# ── 상수 ─────────────────────────────────────────────────────────────────────
MAX_WORKERS   = 5
REQUEST_DELAY = 0.3
SIG_COLOR = {"BUY": "#0ecb81", "SELL": "#f6465d", "HOLD": "#718096"}
SIG_LABEL = {"BUY": "★ 매수", "SELL": "▼ 매도", "HOLD": "─ 관망"}


# ── 데이터 수집 ───────────────────────────────────────────────────────────────

def _fetch_one(args):
    ticker, name, start, end, crawler, use_cache = args
    try:
        df = crawler.get_ohlcv(ticker, start, end, use_cache=use_cache)
        if df is None or df.empty or len(df) < 10:
            return None
        result = evaluate(df)
        return {
            "ticker":   ticker,
            "name":     name,
            "signal":   result["signal"],
            "score":    result["score"],
            "details":  result["details"],
            "close":    df["Close"].iloc[-1],
            "ret5":     (df["Close"].iloc[-1] / df["Close"].iloc[-5]  - 1) * 100 if len(df) >= 5  else 0,
            "ret20":    (df["Close"].iloc[-1] / df["Close"].iloc[-20] - 1) * 100 if len(df) >= 20 else 0,
            "ohlcv":    df,
        }
    except Exception:
        return None


def run_scan(days: int, use_cache: bool) -> pd.DataFrame:
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=days + 30)).strftime("%Y-%m-%d")

    kospi200 = get_kospi200_tickers(use_cache=use_cache)
    total    = len(kospi200)

    crawler  = NaverFinanceCrawler(request_delay=REQUEST_DELAY, verify_ssl=False)
    task_args = [
        (row["Code"], row["Name"], start, end, crawler, use_cache)
        for _, row in kospi200.iterrows()
    ]

    results = []
    progress_bar = st.progress(0, text="KOSPI 200 데이터 수집 중...")
    status_box   = st.empty()

    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch_one, arg): arg for arg in task_args}
        for future in as_completed(futures):
            done += 1
            res = future.result()
            if res:
                results.append(res)
            arg = futures[future]
            pct  = done / total
            name = res["name"] if res else arg[1]
            sig  = res["signal"] if res else "실패"
            progress_bar.progress(pct, text=f"[{done}/{total}] {arg[0]} {name} — {sig}")
            status_box.caption(f"완료 {done} / {total} | 성공 {len(results)}")

    progress_bar.empty()
    status_box.empty()

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results).sort_values("score", ascending=False).reset_index(drop=True)
    return df


# ── 컴포넌트 ─────────────────────────────────────────────────────────────────

def render_badge(signal: str) -> str:
    cls = {"BUY": "badge-buy", "SELL": "badge-sell", "HOLD": "badge-hold"}.get(signal, "badge-hold")
    return f'<span class="{cls}">{SIG_LABEL.get(signal, signal)}</span>'


def render_score_bar(score: int) -> str:
    color = "#0ecb81" if score >= 60 else "#f6465d" if score <= 40 else "#ecc94b"
    return (
        f'<div class="score-bar-wrap">'
        f'<div class="score-bar" style="width:{score}%;background:{color};"></div>'
        f'</div>'
        f'<small style="color:#a0aec0">{score}/100</small>'
    )


def stock_table(df: pd.DataFrame):
    """종목 테이블 렌더링"""
    if df.empty:
        st.info("해당 종목이 없습니다.")
        return

    html = """
    <table style="width:100%;border-collapse:collapse;font-size:0.88rem;">
    <thead>
      <tr style="border-bottom:2px solid #2d3748;color:#718096;">
        <th style="padding:10px 8px;text-align:center;">순위</th>
        <th style="padding:10px 8px;text-align:left;">종목</th>
        <th style="padding:10px 8px;text-align:center;">신호</th>
        <th style="padding:10px 8px;text-align:center;">점수</th>
        <th style="padding:10px 8px;text-align:right;">종가</th>
        <th style="padding:10px 8px;text-align:right;">5일 수익률</th>
        <th style="padding:10px 8px;text-align:right;">20일 수익률</th>
      </tr>
    </thead>
    <tbody>
    """

    for rank, (_, row) in enumerate(df.iterrows(), 1):
        score   = row["score"]
        signal  = row["signal"]
        ret5    = row["ret5"]
        ret20   = row["ret20"]

        bar_color  = "#0ecb81" if score >= 60 else "#f6465d" if score <= 40 else "#ecc94b"
        ret5_color  = "#0ecb81" if ret5  >= 0 else "#f6465d"
        ret20_color = "#0ecb81" if ret20 >= 0 else "#f6465d"
        badge = render_badge(signal)
        row_bg = "#141720" if rank % 2 == 0 else "#0e1117"

        html += f"""
        <tr style="border-bottom:1px solid #1e2432;background:{row_bg};">
          <td style="padding:10px 8px;text-align:center;color:#718096;">{rank}</td>
          <td style="padding:10px 8px;">
            <span style="font-weight:600;color:#e2e8f0;">{row['name']}</span>
            <span style="color:#4a5568;font-size:0.78rem;margin-left:6px;">{row['ticker']}</span>
          </td>
          <td style="padding:10px 8px;text-align:center;">{badge}</td>
          <td style="padding:10px 8px;">
            <div style="background:#2d3748;border-radius:4px;height:6px;margin-bottom:4px;">
              <div style="width:{score}%;background:{bar_color};border-radius:4px;height:6px;"></div>
            </div>
            <div style="text-align:center;color:#a0aec0;font-size:0.8rem;">{score}</div>
          </td>
          <td style="padding:10px 8px;text-align:right;color:#e2e8f0;">{row['close']:,.0f}원</td>
          <td style="padding:10px 8px;text-align:right;color:{ret5_color};font-weight:600;">{ret5:+.2f}%</td>
          <td style="padding:10px 8px;text-align:right;color:{ret20_color};font-weight:600;">{ret20:+.2f}%</td>
        </tr>
        """

    html += "</tbody></table>"
    st.markdown(html, unsafe_allow_html=True)


def detail_panel(row: dict):
    """종목 상세 패널 (전략별 분석 + 가격 차트)"""
    st.markdown(f"### [{row['ticker']}] {row['name']}")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("종합 신호",  SIG_LABEL.get(row["signal"], row["signal"]))
    col2.metric("종합 점수",  f"{row['score']}/100")
    col3.metric("5일 수익률",  f"{row['ret5']:+.2f}%")
    col4.metric("20일 수익률", f"{row['ret20']:+.2f}%")

    st.divider()

    # 전략별 점수
    st.markdown("**전략별 분석**")
    for d in row["details"]:
        score   = d["score"]
        signal  = d["signal"]
        color   = SIG_COLOR.get(signal, "#718096")
        bar_pct = score
        st.markdown(f"""
        <div class="strategy-card">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <span class="strategy-name">{d['name']}</span>
            <span style="color:{color};font-weight:700;font-size:0.9rem;">{SIG_LABEL.get(signal,signal)}  {score}점</span>
          </div>
          <div style="background:#2d3748;border-radius:4px;height:5px;margin:8px 0;">
            <div style="width:{bar_pct}%;background:{color};border-radius:4px;height:5px;"></div>
          </div>
          <div class="strategy-reason">{d['reason']}</div>
        </div>
        """, unsafe_allow_html=True)

    # 가격 차트
    st.markdown("**최근 가격 추이**")
    ohlcv = row.get("ohlcv")
    if ohlcv is not None and not ohlcv.empty:
        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=ohlcv.index,
            open=ohlcv["Open"], high=ohlcv["High"],
            low=ohlcv["Low"],  close=ohlcv["Close"],
            increasing_line_color="#0ecb81",
            decreasing_line_color="#f6465d",
            name="OHLCV",
        ))
        # 이동평균선
        if len(ohlcv) >= 5:
            fig.add_trace(go.Scatter(
                x=ohlcv.index, y=ohlcv["Close"].rolling(5).mean(),
                line=dict(color="#f6c90e", width=1.2), name="MA5"
            ))
        if len(ohlcv) >= 20:
            fig.add_trace(go.Scatter(
                x=ohlcv.index, y=ohlcv["Close"].rolling(20).mean(),
                line=dict(color="#3b82f6", width=1.2), name="MA20"
            ))
        fig.update_layout(
            paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            font=dict(color="#a0aec0"),
            xaxis=dict(gridcolor="#1e2432", showgrid=True, rangeslider_visible=False),
            yaxis=dict(gridcolor="#1e2432", showgrid=True),
            margin=dict(l=0, r=0, t=20, b=0),
            height=320, legend=dict(orientation="h", y=1.08),
        )
        st.plotly_chart(fig, use_container_width=True)


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    # ── 사이드바 ──────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("## ⚙️ 설정")
        days      = st.slider("분석 기간 (일)", 30, 120, 60, 10)
        top_n     = st.slider("표시 종목 수",    5,  50,  10,  5)
        use_cache = st.toggle("캐시 사용", value=True,
                              help="OFF 시 모든 데이터를 새로 수집합니다 (시간 소요)")

        st.divider()
        scan_btn  = st.button("🔍 스캔 시작", use_container_width=True, type="primary")
        st.caption(f"마지막 조회: {st.session_state.get('scan_time', '없음')}")

        st.divider()
        st.markdown("## 📨 텔레그램 전송")

        tg_ok = bool(BOT_TOKEN and not BOT_TOKEN.startswith("여기에"))
        if not tg_ok:
            st.warning("`.env` 파일에 봇 토큰/채팅 ID를 설정하세요.")
        else:
            st.success("봇 연결 설정 완료")

        tg_top  = st.slider("전송 종목 수", 3, 20, TOP_N, key="tg_top")
        tg_btn  = st.button("📨 텔레그램으로 전송", use_container_width=True,
                            disabled=not tg_ok)

        st.divider()
        st.markdown("## 💬 카카오톡 전송")
        kakao_ok = bool(REST_API_KEY and ACCESS_TOKEN)
        if not kakao_ok:
            st.warning("`python kakao_setup.py` 로 초기 설정하세요.")
        else:
            st.success("카카오 연결 설정 완료")
        kakao_top = st.slider("전송 종목 수 ", 3, 20, TOP_N, key="kakao_top")
        kakao_btn = st.button("💬 카카오톡으로 전송", use_container_width=True,
                              disabled=not kakao_ok)

    # ── 헤더 ──────────────────────────────────────────────────────────────────
    st.markdown("# 📈 KOSPI 200 매수/매도 신호 스캐너")
    st.caption("5가지 기술 지표(이동평균·RSI·볼린저밴드·MACD·모멘텀) 앙상블 신호")
    st.divider()

    # ── 카카오톡 전송 ────────────────────────────────────────────────────────
    if kakao_btn:
        df_cur = st.session_state.get("scan_df", pd.DataFrame())
        if df_cur.empty:
            st.sidebar.error("먼저 스캔을 실행하세요.")
        else:
            with st.sidebar:
                with st.spinner("카카오톡 전송 중..."):
                    bot = KakaoBot(REST_API_KEY, ACCESS_TOKEN, REFRESH_TOKEN)
                    days_cur = st.session_state.get("scan_days", 60)
                    try:
                        kakao_send_report(bot, df_cur, days_cur, kakao_top)
                        st.success("✅ 카카오톡 전송 완료!")
                    except Exception as e:
                        st.error(f"❌ 전송 실패: {e}")

    # ── 텔레그램 전송 ────────────────────────────────────────────────────────
    if tg_btn:
        df_cur = st.session_state.get("scan_df", pd.DataFrame())
        if df_cur.empty:
            st.sidebar.error("먼저 스캔을 실행하세요.")
        else:
            with st.sidebar:
                with st.spinner("텔레그램 전송 중..."):
                    bot = TelegramBot(BOT_TOKEN, CHAT_ID)
                    days_cur = st.session_state.get("scan_days", 60)
                    ok  = bot.send(format_summary(df_cur, days_cur))
                    ok &= bot.send(format_buy_list(df_cur, tg_top))
                    ok &= bot.send(format_sell_list(df_cur, tg_top))
                if ok:
                    st.success("✅ 전송 완료!")
                else:
                    st.error("❌ 전송 실패 — 토큰/채팅ID를 확인하세요.")

    # ── 스캔 실행 ─────────────────────────────────────────────────────────────
    if scan_btn or "scan_df" not in st.session_state:
        with st.spinner("KOSPI 200 전종목 신호 계산 중..."):
            df = run_scan(days=days, use_cache=use_cache)
        if df.empty:
            st.error("데이터 수집에 실패했습니다.")
            return
        st.session_state["scan_df"]   = df
        st.session_state["scan_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        st.session_state["scan_days"] = days
        st.success(f"✅ {len(df)}개 종목 분석 완료!")

    df = st.session_state.get("scan_df", pd.DataFrame())
    if df.empty:
        st.info("사이드바에서 '스캔 시작'을 눌러주세요.")
        return

    # ── 요약 카드 ─────────────────────────────────────────────────────────────
    buy_df  = df[df["signal"] == "BUY"]
    sell_df = df[df["signal"] == "SELL"]
    hold_df = df[df["signal"] == "HOLD"]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("📊 분석 종목",  f"{len(df)}개")
    c2.metric("🟢 매수 신호",  f"{len(buy_df)}개",
              delta=f"{len(buy_df)/len(df)*100:.0f}%")
    c3.metric("🔴 매도 신호",  f"{len(sell_df)}개",
              delta=f"-{len(sell_df)/len(df)*100:.0f}%", delta_color="inverse")
    c4.metric("⚪ 관망",       f"{len(hold_df)}개")
    c5.metric("📈 평균 점수",  f"{df['score'].mean():.1f}점")

    st.divider()

    # ── 차트 행 ───────────────────────────────────────────────────────────────
    ch1, ch2 = st.columns([1, 2])

    with ch1:
        st.markdown("#### 신호 분포")
        pie = px.pie(
            values=[len(buy_df), len(hold_df), len(sell_df)],
            names=["매수", "관망", "매도"],
            color_discrete_sequence=["#0ecb81", "#718096", "#f6465d"],
            hole=0.55,
        )
        pie.update_layout(
            paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            font=dict(color="#a0aec0"), margin=dict(l=0,r=0,t=0,b=0),
            height=260, showlegend=True,
            legend=dict(orientation="h", y=-0.1, x=0.15),
        )
        pie.update_traces(textinfo="percent+label", textfont_size=13)
        st.plotly_chart(pie, use_container_width=True)

    with ch2:
        st.markdown("#### 점수 분포")
        hist = px.histogram(
            df, x="score", nbins=20,
            color="signal",
            color_discrete_map={"BUY":"#0ecb81","HOLD":"#718096","SELL":"#f6465d"},
            labels={"score":"점수","signal":"신호"},
            category_orders={"signal":["BUY","HOLD","SELL"]},
        )
        hist.update_layout(
            paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            font=dict(color="#a0aec0"),
            xaxis=dict(gridcolor="#1e2432", title="점수"),
            yaxis=dict(gridcolor="#1e2432", title="종목 수"),
            margin=dict(l=0,r=0,t=0,b=0), height=260,
            bargap=0.1,
            legend=dict(title="", orientation="h", y=1.1),
        )
        st.plotly_chart(hist, use_container_width=True)

    st.divider()

    # ── 탭 ────────────────────────────────────────────────────────────────────
    tab_buy, tab_sell, tab_all, tab_detail = st.tabs([
        f"🟢 매수 추천 ({len(buy_df)})",
        f"🔴 매도 추천 ({len(sell_df)})",
        f"📋 전체 종목 ({len(df)})",
        "🔎 종목 상세",
    ])

    with tab_buy:
        st.markdown(f"#### 매수 추천 상위 {top_n}개")
        stock_table(buy_df.head(top_n))

    with tab_sell:
        st.markdown(f"#### 매도 추천 상위 {top_n}개 (점수 낮은 순)")
        stock_table(sell_df.sort_values("score").head(top_n))

    with tab_all:
        st.markdown("#### 전체 종목 (점수 순)")
        # 검색 필터
        search = st.text_input("종목명 또는 코드 검색", placeholder="예: 삼성, 005930")
        sig_filter = st.multiselect("신호 필터", ["BUY", "HOLD", "SELL"],
                                    default=["BUY", "HOLD", "SELL"])
        filtered = df[df["signal"].isin(sig_filter)]
        if search:
            mask = (
                filtered["name"].str.contains(search, case=False, na=False) |
                filtered["ticker"].str.contains(search, case=False, na=False)
            )
            filtered = filtered[mask]
        stock_table(filtered)

    with tab_detail:
        st.markdown("#### 종목 상세 분석")
        ticker_options = {f"[{r['ticker']}] {r['name']}": i
                          for i, r in df.iterrows()}
        selected = st.selectbox("종목 선택", list(ticker_options.keys()))
        if selected:
            idx = ticker_options[selected]
            detail_panel(df.iloc[idx].to_dict())


if __name__ == "__main__":
    main()
