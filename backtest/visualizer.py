"""
백테스트 결과 시각화
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from pathlib import Path
import warnings
warnings.filterwarnings("ignore")

# 한글 폰트 설정
try:
    plt.rcParams["font.family"] = "Malgun Gothic"  # Windows
except Exception:
    pass
plt.rcParams["axes.unicode_minus"] = False

RESULTS_DIR = Path(__file__).parent.parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


def _safe_name(name: str) -> str:
    """파일명으로 사용할 수 없는 문자 제거"""
    for ch in r'\/:*?"<>|()':
        name = name.replace(ch, "_")
    return name


def plot_equity_curve(
    engine,
    benchmark_data: pd.DataFrame = None,
    strategy_name: str = "전략",
    save: bool = True,
    show: bool = True,
):
    """
    자산 곡선 + 드로다운 차트

    Args:
        engine: 실행 완료된 BacktestEngine
        benchmark_data: 벤치마크 OHLCV DataFrame
        strategy_name: 전략 이름 (차트 제목)
        save: 파일 저장 여부
        show: 화면 표시 여부
    """
    if engine.results is None:
        print("먼저 engine.run()을 실행하세요.")
        return

    results = engine.results
    equity = results["total_value"]

    fig, axes = plt.subplots(3, 1, figsize=(14, 10), gridspec_kw={"height_ratios": [3, 1, 1]})
    fig.suptitle(f"{strategy_name} 백테스트 결과", fontsize=16, fontweight="bold")

    # 1. 자산 곡선
    ax1 = axes[0]
    normalized = equity / equity.iloc[0] * 100
    ax1.plot(equity.index, normalized, label=strategy_name, color="steelblue", linewidth=1.5)

    if benchmark_data is not None:
        bm = benchmark_data["Close"].reindex(equity.index, method="ffill")
        bm_norm = bm / bm.iloc[0] * 100
        ax1.plot(bm.index, bm_norm, label="벤치마크", color="orange", linewidth=1.2, alpha=0.8)

    ax1.axhline(y=100, color="gray", linestyle="--", alpha=0.5)
    ax1.set_ylabel("수익률 (초기=100)")
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))

    # 2. 드로다운
    ax2 = axes[1]
    roll_max = equity.cummax()
    drawdown = (equity - roll_max) / roll_max * 100
    ax2.fill_between(drawdown.index, drawdown, 0, color="red", alpha=0.4)
    ax2.set_ylabel("드로다운 (%)")
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))

    # 3. 일별 수익률
    ax3 = axes[2]
    daily_ret = equity.pct_change().dropna() * 100
    colors = ["green" if r > 0 else "red" for r in daily_ret]
    ax3.bar(daily_ret.index, daily_ret, color=colors, width=1, alpha=0.6)
    ax3.axhline(y=0, color="black", linewidth=0.5)
    ax3.set_ylabel("일별 수익률 (%)")
    ax3.grid(True, alpha=0.3)
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))

    plt.tight_layout()

    if save:
        filepath = RESULTS_DIR / f"{_safe_name(strategy_name)}_equity_curve.png"
        plt.savefig(filepath, dpi=150, bbox_inches="tight")
        print(f"차트 저장: {filepath}")

    if show:
        plt.show()

    plt.close()


def plot_price_with_signals(
    engine,
    ticker: str,
    strategy_name: str = "전략",
    save: bool = True,
    show: bool = True,
):
    """
    주가 차트에 매수/매도 신호 표시
    """
    if ticker not in engine.data:
        print(f"데이터에 {ticker}가 없습니다.")
        return

    price_data = engine.data[ticker]
    orders_df = engine.get_orders()

    if orders_df.empty:
        print("거래 내역이 없습니다.")
        return

    ticker_orders = orders_df[orders_df["ticker"] == ticker]
    buys = ticker_orders[ticker_orders["action"] == "BUY"]
    sells = ticker_orders[ticker_orders["action"] == "SELL"]

    fig, axes = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={"height_ratios": [3, 1]})
    fig.suptitle(f"{strategy_name} - {ticker} 매매 신호", fontsize=14, fontweight="bold")

    ax1 = axes[0]
    ax1.plot(price_data.index, price_data["Close"], color="black", linewidth=1, label="종가")

    ax1.scatter(
        buys["date"], buys["price"],
        marker="^", color="red", s=100, zorder=5, label="매수"
    )
    ax1.scatter(
        sells["date"], sells["price"],
        marker="v", color="blue", s=100, zorder=5, label="매도"
    )

    ax1.set_ylabel("주가 (원)")
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"{x:,.0f}"))

    ax2 = axes[1]
    ax2.bar(price_data.index, price_data["Volume"], color="gray", alpha=0.5)
    ax2.set_ylabel("거래량")
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))

    plt.tight_layout()

    if save:
        filepath = RESULTS_DIR / f"{_safe_name(strategy_name)}_{ticker}_signals.png"
        plt.savefig(filepath, dpi=150, bbox_inches="tight")
        print(f"차트 저장: {filepath}")

    if show:
        plt.show()

    plt.close()


def plot_monthly_returns(engine, strategy_name: str = "전략", save: bool = True, show: bool = True):
    """월별 수익률 히트맵"""
    if engine.results is None:
        return

    equity = engine.results["total_value"]
    monthly = equity.resample("ME").last().pct_change().dropna() * 100

    monthly_df = pd.DataFrame({
        "year": monthly.index.year,
        "month": monthly.index.month,
        "return": monthly.values,
    })
    pivot = monthly_df.pivot(index="year", columns="month", values="return")
    pivot.columns = ["1월", "2월", "3월", "4월", "5월", "6월",
                     "7월", "8월", "9월", "10월", "11월", "12월"][:len(pivot.columns)]

    fig, ax = plt.subplots(figsize=(14, max(4, len(pivot) * 0.6 + 2)))
    fig.suptitle(f"{strategy_name} 월별 수익률 (%)", fontsize=14, fontweight="bold")

    import matplotlib.colors as mcolors
    cmap = plt.cm.RdYlGn
    vmax = max(abs(pivot.values[~np.isnan(pivot.values)].max()),
               abs(pivot.values[~np.isnan(pivot.values)].min()), 5)

    im = ax.imshow(pivot.values, cmap=cmap, aspect="auto",
                   vmin=-vmax, vmax=vmax)

    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels(pivot.columns)
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels(pivot.index)

    for i in range(len(pivot.index)):
        for j in range(len(pivot.columns)):
            val = pivot.values[i, j]
            if not np.isnan(val):
                color = "black" if abs(val) < vmax * 0.6 else "white"
                ax.text(j, i, f"{val:.1f}%", ha="center", va="center",
                        fontsize=9, color=color, fontweight="bold")

    plt.colorbar(im, ax=ax, label="수익률 (%)")
    plt.tight_layout()

    if save:
        filepath = RESULTS_DIR / f"{_safe_name(strategy_name)}_monthly_returns.png"
        plt.savefig(filepath, dpi=150, bbox_inches="tight")
        print(f"차트 저장: {filepath}")

    if show:
        plt.show()

    plt.close()
