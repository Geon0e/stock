"""
전략 최적화 결과 시각화

생성 차트:
  1. 라운드별 승률 추이
  2. 커버리지(종목수) vs 승률 산점도
  3. 파라미터별 승률 분포 (박스플롯)
  4. 상위 종목 승률 막대 차트
  5. 사이클 요약 비교
"""

from __future__ import annotations

import os
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

# 한글 폰트 설정
import platform
if platform.system() == "Windows":
    plt.rcParams["font.family"] = "Malgun Gothic"
else:
    plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.facecolor"] = "#0d1117"
plt.rcParams["axes.facecolor"]   = "#161b22"
plt.rcParams["axes.edgecolor"]   = "#30363d"
plt.rcParams["text.color"]       = "#e6edf3"
plt.rcParams["axes.labelcolor"]  = "#e6edf3"
plt.rcParams["xtick.color"]      = "#8b949e"
plt.rcParams["ytick.color"]      = "#8b949e"
plt.rcParams["grid.color"]       = "#21262d"
plt.rcParams["grid.linewidth"]   = 0.6


ACCENT   = "#58a6ff"
GREEN    = "#3fb950"
YELLOW   = "#d29922"
RED      = "#f85149"
PURPLE   = "#bc8cff"


# ── 1. 라운드별 승률 추이 ─────────────────────────────────────────────────────

def plot_round_progress(summary_path: str, out_dir: str) -> str:
    with open(summary_path, encoding="utf-8") as f:
        data = json.load(f)

    rounds     = data["rounds"]
    names      = [f"R{r['round']:02d}\n{r['name'][:8]}" for r in rounds]
    wrs        = [r["best_wr"] for r in rounds]
    best_so_far = []
    cur = 0.0
    for w in wrs:
        cur = max(cur, w)
        best_so_far.append(cur)

    fig, ax = plt.subplots(figsize=(12, 5))
    x = range(len(rounds))

    bars = ax.bar(x, wrs, color=[GREEN if w == max(wrs) else ACCENT for w in wrs],
                  alpha=0.8, zorder=2)
    ax.plot(x, best_so_far, color=YELLOW, linewidth=2, marker="o",
            markersize=5, label="누적 최고", zorder=3)

    for i, (bar, w) in enumerate(zip(bars, wrs)):
        ax.text(bar.get_x() + bar.get_width() / 2, w + 0.3,
                f"{w:.1f}%", ha="center", va="bottom", fontsize=8,
                color="#e6edf3")

    ax.set_xticks(list(x))
    ax.set_xticklabels(names, fontsize=7)
    ax.set_ylabel("합산 승률 (%)")
    ax.set_title(f"라운드별 합산 승률  (사이클 {data['cycle']}  |  최고: {data['best_wr']:.2f}%)",
                 fontsize=12, pad=12)
    ax.set_ylim(max(0, min(wrs) - 5), min(100, max(wrs) + 5))
    ax.legend(fontsize=9)
    ax.grid(axis="y", zorder=1)
    fig.tight_layout()

    out_path = os.path.join(out_dir, f"cycle{data['cycle']}_round_progress.png")
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


# ── 2. 커버리지 vs 승률 산점도 ────────────────────────────────────────────────

def plot_coverage_vs_wr(round_log_paths: list[str], out_dir: str,
                        cycle: int) -> str:
    xs, ys, labels = [], [], []
    for path in round_log_paths:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        for r in d.get("top10", []):
            cov = r.get("covered_stocks", 0)
            wr  = r.get("win_rate", 0)
            if cov > 0 and wr > 0:
                xs.append(cov)
                ys.append(wr)

    fig, ax = plt.subplots(figsize=(8, 6))
    sc = ax.scatter(xs, ys, c=ys, cmap="RdYlGn", alpha=0.7,
                    s=40, edgecolors="none", vmin=55, vmax=80)
    plt.colorbar(sc, ax=ax, label="승률 (%)")
    ax.set_xlabel("적용 종목 수 (Coverage)")
    ax.set_ylabel("합산 승률 (%)")
    ax.set_title(f"커버리지 vs 승률  (사이클 {cycle})", fontsize=12, pad=12)
    ax.grid(True, zorder=1)
    fig.tight_layout()

    out_path = os.path.join(out_dir, f"cycle{cycle}_coverage_vs_wr.png")
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


# ── 3. 파라미터별 승률 분포 ──────────────────────────────────────────────────

def plot_param_importance(round_log_paths: list[str], out_dir: str,
                          cycle: int) -> str:
    param_keys = ["entry_window", "exit_window", "trail_mult",
                  "profit_target_mult", "volume_ratio", "adx_filter"]
    param_data: dict[str, dict] = {k: {} for k in param_keys}

    for path in round_log_paths:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        for r in d.get("top10", []):
            wr = r.get("win_rate", 0)
            p  = r.get("_params", {})
            if not p or wr < 50:
                continue
            for k in param_keys:
                v = p.get(k)
                if v is None:
                    continue
                if v not in param_data[k]:
                    param_data[k][v] = []
                param_data[k][v].append(wr)

    labels = {
        "entry_window":       "진입 윈도우",
        "exit_window":        "청산 윈도우",
        "trail_mult":         "ATR 손절 배수",
        "profit_target_mult": "이익목표 배수",
        "volume_ratio":       "거래량 비율",
        "adx_filter":         "ADX 임계값",
    }

    fig, axes = plt.subplots(2, 3, figsize=(14, 8))
    axes = axes.flatten()

    for ax, key in zip(axes, param_keys):
        pd_k = param_data[key]
        if not pd_k:
            ax.set_visible(False)
            continue
        vals = sorted(pd_k.keys())
        means = [np.mean(pd_k[v]) for v in vals]
        colors = [GREEN if m == max(means) else ACCENT for m in means]
        ax.bar([str(v) for v in vals], means, color=colors, alpha=0.85)
        ax.set_title(labels.get(key, key), fontsize=10)
        ax.set_ylabel("평균 승률 (%)", fontsize=8)
        ax.tick_params(axis="x", labelsize=7, rotation=30)
        ax.grid(axis="y")
        ax.set_ylim(max(0, min(means) - 3), min(100, max(means) + 3))

    fig.suptitle(f"파라미터별 평균 승률  (사이클 {cycle})", fontsize=13, y=1.01)
    fig.tight_layout()

    out_path = os.path.join(out_dir, f"cycle{cycle}_param_importance.png")
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


# ── 4. 상위 종목 승률 막대 차트 ──────────────────────────────────────────────

def plot_top_stocks(per_stock: list[dict], out_dir: str,
                    version: int, strategy_wr: float) -> str:
    top = sorted(per_stock, key=lambda x: x.get("win_rate", 0), reverse=True)[:25]
    names = [f"{r.get('name','')[:6]}\n({r.get('ticker','')})" for r in top]
    wrs   = [r.get("win_rate", 0) for r in top]
    trades = [r.get("total_trades", 0) for r in top]

    fig, ax1 = plt.subplots(figsize=(14, 6))

    colors = [GREEN if w >= 80 else ACCENT if w >= 70 else YELLOW for w in wrs]
    bars = ax1.bar(range(len(top)), wrs, color=colors, alpha=0.85, zorder=2)
    ax1.axhline(strategy_wr, color=RED, linewidth=1.5,
                linestyle="--", label=f"전체 평균 {strategy_wr:.1f}%", zorder=3)

    ax2 = ax1.twinx()
    ax2.plot(range(len(top)), trades, color=PURPLE, linewidth=1.5,
             marker="o", markersize=4, alpha=0.7, label="거래수")
    ax2.set_ylabel("거래 횟수", color=PURPLE, fontsize=9)
    ax2.tick_params(axis="y", colors=PURPLE)

    ax1.set_xticks(range(len(top)))
    ax1.set_xticklabels(names, fontsize=7)
    ax1.set_ylabel("종목 승률 (%)")
    ax1.set_ylim(0, 100)
    ax1.set_title(f"승률 상위 25개 종목  (전략 v{version})", fontsize=12, pad=12)
    ax1.grid(axis="y", zorder=1)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, fontsize=8, loc="upper right")

    fig.tight_layout()

    out_path = os.path.join(out_dir, f"KOSPI200_v{version}_top_stocks.png")
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


# ── 5. 사이클 비교 요약 ──────────────────────────────────────────────────────

def plot_cycle_summary(logs_dir: str, out_dir: str) -> str | None:
    summaries = []
    for f in sorted(os.listdir(logs_dir)):
        if f.endswith("_summary.json"):
            with open(os.path.join(logs_dir, f), encoding="utf-8") as fp:
                d = json.load(fp)
            summaries.append(d)

    if len(summaries) < 1:
        return None

    cycles   = [d["cycle"] for d in summaries]
    wrs      = [d["best_wr"] for d in summaries]
    covered  = [d.get("best_covered", 0) for d in summaries]
    trades   = [d.get("best_trades", 0) for d in summaries]

    fig, axes = plt.subplots(1, 3, figsize=(14, 5))

    # 승률
    axes[0].bar(cycles, wrs, color=ACCENT, alpha=0.85)
    for i, (c, w) in enumerate(zip(cycles, wrs)):
        axes[0].text(c, w + 0.3, f"{w:.1f}%", ha="center", fontsize=9)
    axes[0].set_title("사이클별 최고 승률", fontsize=11)
    axes[0].set_xlabel("사이클")
    axes[0].set_ylabel("합산 승률 (%)")
    axes[0].set_ylim(0, 100)
    axes[0].grid(axis="y")

    # 커버 종목수
    colors2 = [GREEN if c == max(covered) else ACCENT for c in covered]
    axes[1].bar(cycles, covered, color=colors2, alpha=0.85)
    for c, v in zip(cycles, covered):
        axes[1].text(c, v + 1, str(v), ha="center", fontsize=9)
    axes[1].set_title("사이클별 적용 종목 수", fontsize=11)
    axes[1].set_xlabel("사이클")
    axes[1].set_ylabel("종목 수")
    axes[1].grid(axis="y")

    # 총 거래수
    axes[2].bar(cycles, trades, color=PURPLE, alpha=0.85)
    for c, v in zip(cycles, trades):
        axes[2].text(c, v + 10, f"{v:,}", ha="center", fontsize=8)
    axes[2].set_title("사이클별 총 거래 횟수", fontsize=11)
    axes[2].set_xlabel("사이클")
    axes[2].set_ylabel("거래 횟수")
    axes[2].grid(axis="y")

    fig.suptitle("사이클별 최적화 성과 비교", fontsize=13, y=1.02)
    fig.tight_layout()

    out_path = os.path.join(out_dir, "cycle_summary.png")
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


# ── 공개 진입점 ──────────────────────────────────────────────────────────────

def generate_all_charts(project_root: str, cycle: int,
                        per_stock: list[dict], version: int,
                        strategy_wr: float) -> dict[str, str]:
    """
    모든 차트를 생성하고 {name: path} 딕셔너리 반환.
    MD 파일에서 이 경로를 상대경로로 참조.
    """
    logs_dir   = os.path.join(project_root, "strategies", "logs")
    charts_dir = os.path.join(project_root, "strategies", "charts")
    os.makedirs(charts_dir, exist_ok=True)

    result = {}

    # 1. 라운드 진행
    summary_path = os.path.join(logs_dir, f"cycle{cycle}_summary.json")
    if os.path.exists(summary_path):
        result["round_progress"] = plot_round_progress(summary_path, charts_dir)

    # 2. 커버리지 vs 승률
    round_logs = sorted([
        os.path.join(logs_dir, f)
        for f in os.listdir(logs_dir)
        if f.startswith(f"cycle{cycle}_round") and f.endswith(".json")
    ])
    if round_logs:
        result["coverage_vs_wr"]   = plot_coverage_vs_wr(round_logs, charts_dir, cycle)
        result["param_importance"] = plot_param_importance(round_logs, charts_dir, cycle)

    # 3. 상위 종목
    if per_stock:
        result["top_stocks"] = plot_top_stocks(per_stock, charts_dir, version, strategy_wr)

    # 4. 사이클 비교
    cs = plot_cycle_summary(logs_dir, charts_dir)
    if cs:
        result["cycle_summary"] = cs

    return result
