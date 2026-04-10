"""
통계 기반 파라미터 추론 엔진

사이클 결과를 분석해서 다음 사이클의 파라미터 공간을 자동으로 좁힌다.
Claude API 없이 순수 통계로 "어떤 파라미터가 승률에 기여했는가"를 추론.

핵심 아이디어:
  - 상위 25% 결과에서 파라미터 분포 분석
  - 하위 25%와 비교해서 "차별적으로 등장하는 값" 식별
  - 해당 값 위주로 좁힌 새 파라미터 공간 생성
"""

from __future__ import annotations

from collections import Counter
from typing import Any


# ── 파라미터 분석 ──────────────────────────────────────────────────────────

def _extract_param_values(results: list[dict], param: str) -> list:
    """결과 리스트에서 특정 파라미터 값 추출"""
    vals = []
    for r in results:
        p = r.get("_params", {})
        if param in p:
            vals.append(p[param])
    return vals


def _winning_values(top: list[dict], bottom: list[dict], param: str,
                    original_space: list) -> list:
    """
    상위 결과에서 더 자주 나오는 값을 선별.
    - 상위에서 등장 비율이 하위 대비 1.5배 이상이면 "유망한 값"
    - 유망한 값이 없으면 원래 공간 그대로 반환
    """
    if not top:
        return original_space

    top_vals  = _extract_param_values(top, param)
    bot_vals  = _extract_param_values(bottom, param)

    if not top_vals:
        return original_space

    top_cnt = Counter(top_vals)
    bot_cnt = Counter(bot_vals)
    top_n   = max(len(top_vals), 1)
    bot_n   = max(len(bot_vals), 1)

    good = []
    for val in original_space:
        top_ratio = top_cnt.get(val, 0) / top_n
        bot_ratio = bot_cnt.get(val, 0) / bot_n
        # 상위에서 최소 1번 등장 + 하위 대비 1.5배 이상
        if top_ratio > 0 and top_ratio >= bot_ratio * 1.5:
            good.append(val)

    # 너무 좁아지면 (2개 미만) 상위에서 가장 많이 등장한 값 상위 3개 사용
    if len(good) < 2:
        most_common = [v for v, _ in top_cnt.most_common(3) if v in original_space]
        good = most_common if most_common else original_space

    return sorted(set(good))


def _filter_effectiveness(results: list[dict], filter_param: str) -> dict:
    """
    필터 파라미터(rsi_filter, adx_filter, trend_filter)의 효과 분석.
    Returns: {"use_filter": bool, "best_value": int, "avg_wr_with": float, "avg_wr_without": float}
    """
    with_filter    = [r for r in results if r.get("_params", {}).get(filter_param, 0) > 0]
    without_filter = [r for r in results if r.get("_params", {}).get(filter_param, 0) == 0]

    avg_with    = _avg_wr(with_filter)
    avg_without = _avg_wr(without_filter)

    best_value = 0
    if with_filter:
        # 필터 값별 평균 승률 계산
        val_groups: dict[int, list[float]] = {}
        for r in with_filter:
            v  = r["_params"][filter_param]
            wr = r.get("승률(%)", 0) or 0
            val_groups.setdefault(v, []).append(wr)
        best_value = max(val_groups, key=lambda v: sum(val_groups[v]) / len(val_groups[v]))

    return {
        "use_filter":    avg_with > avg_without + 2.0,  # 2%p 이상 차이 시 사용
        "best_value":    best_value,
        "avg_wr_with":   avg_with,
        "avg_wr_without": avg_without,
    }


def _avg_wr(results: list[dict]) -> float:
    if not results:
        return 0.0
    return sum(r.get("승률(%)", 0) or 0 for r in results) / len(results)


# ── 메인 추론 함수 ─────────────────────────────────────────────────────────

def reason(
    all_results: list[dict],
    best_strategy: str,
    best_params: dict,
    best_wr: float,
    round_history: list[tuple],   # [(round_num, name, wr)]
) -> dict:
    """
    사이클 결과를 분석해 다음 라운드 파라미터 공간과 가설을 반환.

    Returns:
        {
            "hypothesis": str,
            "focus_strategy": "breakout" | "v2" | "both",
            "breakout_space": dict | None,
            "v2_space": dict | None,
            "insights": list[str],
        }
    """
    if not all_results:
        return _fallback()

    # 승률 기준 정렬
    sorted_results = sorted(all_results, key=lambda r: r.get("승률(%)", 0) or 0, reverse=True)
    n      = len(sorted_results)
    top_n  = max(1, n // 4)        # 상위 25%
    bot_n  = max(1, n // 4)        # 하위 25%
    top    = sorted_results[:top_n]
    bottom = sorted_results[n - bot_n:]

    insights = []

    # ── 1. 전략 타입 분석 ────────────────────────────────────────────
    top_strategies = Counter(r.get("_strategy", "") for r in top)
    focus = "breakout" if top_strategies.get("Breakout", 0) >= top_strategies.get("V2", 0) else "v2"
    insights.append(
        f"상위 {top_n}개 중 Breakout={top_strategies.get('Breakout',0)}, "
        f"V2={top_strategies.get('V2',0)} -> {focus} 집중"
    )

    # ── 2. Breakout 파라미터 분석 ────────────────────────────────────
    breakout_results = [r for r in all_results if r.get("_strategy") == "Breakout"]
    breakout_top     = [r for r in top        if r.get("_strategy") == "Breakout"]
    breakout_bot     = [r for r in bottom     if r.get("_strategy") == "Breakout"]

    from backtest.optimizer import PARAM_SPACE_BREAKOUT, PARAM_SPACE_V2

    new_breakout_space = None
    if breakout_results:
        ew_good = _winning_values(breakout_top, breakout_bot, "entry_window",
                                  PARAM_SPACE_BREAKOUT["entry_window"])
        xw_good = _winning_values(breakout_top, breakout_bot, "exit_window",
                                  PARAM_SPACE_BREAKOUT["exit_window"])
        tm_good = _winning_values(breakout_top, breakout_bot, "trail_mult",
                                  PARAM_SPACE_BREAKOUT["trail_mult"])
        pt_good = _winning_values(breakout_top, breakout_bot, "profit_target_mult",
                                  PARAM_SPACE_BREAKOUT["profit_target_mult"])
        vr_good = _winning_values(breakout_top, breakout_bot, "volume_ratio",
                                  PARAM_SPACE_BREAKOUT["volume_ratio"])
        ip_good = _winning_values(breakout_top, breakout_bot, "invest_pct",
                                  PARAM_SPACE_BREAKOUT["invest_pct"])

        # 필터 효과 분석
        rsi_eff = _filter_effectiveness(breakout_results, "rsi_filter")
        adx_eff = _filter_effectiveness(breakout_results, "adx_filter")
        trn_eff = _filter_effectiveness(breakout_results, "trend_filter")

        rsi_vals = [rsi_eff["best_value"], 0] if not rsi_eff["use_filter"] else \
                   [rsi_eff["best_value"]]
        adx_vals = [adx_eff["best_value"], 0] if not adx_eff["use_filter"] else \
                   [adx_eff["best_value"]]
        trn_vals = [trn_eff["best_value"], 0] if not trn_eff["use_filter"] else \
                   [trn_eff["best_value"]]

        # 필터 효과 인사이트
        for fname, eff in [("RSI", rsi_eff), ("ADX", adx_eff), ("Trend", trn_eff)]:
            diff = eff["avg_wr_with"] - eff["avg_wr_without"]
            if abs(diff) >= 2.0:
                direction = "유효" if diff > 0 else "역효과"
                insights.append(
                    f"{fname} 필터 {direction}: 있을때={eff['avg_wr_with']:.1f}% "
                    f"없을때={eff['avg_wr_without']:.1f}% (최적값={eff['best_value']})"
                )

        insights.append(f"entry_window 유망 범위: {ew_good}")
        insights.append(f"profit_target 유망 범위: {pt_good}")

        new_breakout_space = {
            "entry_window":       ew_good,
            "exit_window":        xw_good,
            "trail_mult":         tm_good,
            "profit_target_mult": pt_good,
            "volume_ratio":       vr_good,
            "invest_pct":         ip_good,
            "rsi_filter":         sorted(set(rsi_vals)),
            "adx_filter":         sorted(set(adx_vals)),
            "trend_filter":       sorted(set(trn_vals)),
        }

    # ── 3. V2 파라미터 분석 ─────────────────────────────────────────
    v2_results = [r for r in all_results if r.get("_strategy") == "V2"]
    v2_top     = [r for r in top        if r.get("_strategy") == "V2"]
    v2_bot     = [r for r in bottom     if r.get("_strategy") == "V2"]

    new_v2_space = None
    if v2_results:
        new_v2_space = {
            "short_window":  _winning_values(v2_top, v2_bot, "short_window",
                                             PARAM_SPACE_V2["short_window"]),
            "long_window":   _winning_values(v2_top, v2_bot, "long_window",
                                             PARAM_SPACE_V2["long_window"]),
            "trend_window":  _winning_values(v2_top, v2_bot, "trend_window",
                                             PARAM_SPACE_V2["trend_window"]),
            "rsi_entry_max": _winning_values(v2_top, v2_bot, "rsi_entry_max",
                                             PARAM_SPACE_V2["rsi_entry_max"]),
            "trail_mult":    _winning_values(v2_top, v2_bot, "trail_mult",
                                             PARAM_SPACE_V2["trail_mult"]),
            "invest_pct":    _winning_values(v2_top, v2_bot, "invest_pct",
                                             PARAM_SPACE_V2["invest_pct"]),
        }

    # ── 4. 라운드 이력 분석: 어떤 가설이 효과 있었나 ───────────────
    best_round_name = ""
    if round_history:
        best_round = max(round_history, key=lambda x: x[2])
        best_round_name = best_round[1]
        insights.append(f"가장 효과적인 라운드: [{best_round_name}] 승률={best_round[2]:.1f}%")

    # ── 5. 가설 생성 ────────────────────────────────────────────────
    hypothesis = _build_hypothesis(
        focus, best_wr, best_params, best_round_name,
        new_breakout_space, rsi_eff if breakout_results else None,
        adx_eff if breakout_results else None,
    )

    return {
        "hypothesis":      hypothesis,
        "focus_strategy":  focus,
        "breakout_space":  new_breakout_space,
        "v2_space":        new_v2_space,
        "insights":        insights,
    }


def _build_hypothesis(focus, best_wr, best_params, best_round_name,
                      breakout_space, rsi_eff, adx_eff) -> str:
    parts = [f"이전 최고 {best_wr:.1f}% 기반 집중 탐색."]

    if best_round_name:
        parts.append(f"[{best_round_name}]이 가장 효과적.")

    if focus == "breakout" and breakout_space:
        ew = breakout_space.get("entry_window", [])
        pt = breakout_space.get("profit_target_mult", [])
        if ew:
            parts.append(f"entry_window {ew} 범위 집중.")
        if pt:
            parts.append(f"profit_target {pt} 범위 집중.")

    if adx_eff and adx_eff["use_filter"]:
        parts.append(f"ADX 필터 유효 (최적={adx_eff['best_value']}).")
    elif adx_eff and not adx_eff["use_filter"]:
        parts.append("ADX 필터 제거 (역효과).")

    if rsi_eff and rsi_eff["use_filter"]:
        parts.append(f"RSI 필터 유효 (최적={rsi_eff['best_value']}).")

    return " ".join(parts)


def _fallback() -> dict:
    """결과 없을 때 기본 반환"""
    from backtest.optimizer import PARAM_SPACE_BREAKOUT, PARAM_SPACE_V2
    return {
        "hypothesis":     "결과 부족 - 기본 공간 재탐색",
        "focus_strategy": "both",
        "breakout_space": PARAM_SPACE_BREAKOUT,
        "v2_space":       PARAM_SPACE_V2,
        "insights":       [],
    }
