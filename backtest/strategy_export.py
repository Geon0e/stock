"""
최적화된 전략을 JSON으로 저장하고 git에 커밋하는 모듈
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from typing import Any


def _build_entry_exit(strategy_type: str, params: dict) -> tuple[list[str], list[str]]:
    """전략 타입과 파라미터로 진입/청산 조건 생성"""
    entry, exit_ = [], []

    if strategy_type == "breakout":
        entry.append(f"종가 > {params.get('entry_window', '?')}일 최고가 (채널 돌파)")
        vr = params.get("volume_ratio", 1.0)
        if vr > 1.0:
            entry.append(f"거래량 > {vr}x 평균거래량")
        rsi = params.get("rsi_filter", 0)
        if rsi > 0:
            entry.append(f"RSI < {rsi} (과매수 제외)")
        adx = params.get("adx_filter", 0)
        if adx > 0:
            entry.append(f"ADX > {adx} (추세 강도 확인)")
        tf = params.get("trend_filter", 0)
        if tf > 0:
            entry.append(f"종가 > MA{tf} (대세 상승장 확인)")

        exit_.append(f"종가 < {params.get('exit_window', '?')}일 최저가")
        tm = params.get("trail_mult", 0)
        if tm > 0:
            exit_.append(f"ATR 손절: 진입가 - {tm} × ATR (트레일링)")
        pt = params.get("profit_target_mult", 0)
        if pt > 0:
            exit_.append(f"이익 목표: 진입가 + {pt} × ATR 도달 시 청산")

    elif strategy_type == "moving_average_v2":
        sw = params.get("short_window", "?")
        lw = params.get("long_window", "?")
        tw = params.get("trend_window", "?")
        entry.append(f"MA{sw} > MA{lw} (골든크로스)")
        entry.append(f"종가 > MA{tw} (추세 확인)")
        rsi = params.get("rsi_entry_max", 0)
        if rsi > 0:
            entry.append(f"RSI < {rsi} (과매수 제외)")

        exit_.append(f"MA{sw} < MA{lw} (데드크로스)")
        tm = params.get("trail_mult", 0)
        if tm > 0:
            exit_.append(f"ATR 손절: 진입가 - {tm} × ATR (트레일링)")

    elif strategy_type == "rsi":
        period = params.get("period", 14)
        oversold = params.get("oversold", 30)
        overbought = params.get("overbought", 70)
        entry.append(f"RSI({period}) 상향 돌파 {oversold} (과매도 탈출)")
        tw = params.get("trend_window", 0)
        if tw > 0:
            entry.append(f"종가 > MA{tw} (추세 확인)")

        exit_.append(f"RSI({period}) 상향 돌파 {overbought} (과매수 도달)")
        tm = params.get("trail_mult", 0)
        if tm > 0:
            exit_.append(f"ATR 손절: 진입가 - {tm} × ATR")

    elif strategy_type == "bollinger":
        w = params.get("window", 20)
        std = params.get("num_std", 2.0)
        entry.append(f"볼린저밴드({w}, {std}σ) 하단 터치 후 회복")
        bw = params.get("min_bandwidth", 0)
        if bw > 0:
            entry.append(f"밴드폭 > {bw} (저변동성 구간 제외)")

        exit_.append(f"볼린저밴드 상단 도달")
        if params.get("exit_at_mid"):
            exit_.append("중간 밴드(MA) 도달 시 조기 청산")
        tm = params.get("trail_mult", 0)
        if tm > 0:
            exit_.append(f"ATR 손절: 진입가 - {tm} × ATR")

    elif strategy_type == "momentum":
        lb = params.get("lookback", 120)
        slb = params.get("short_lookback", 20)
        entry.append(f"장기 모멘텀({lb}일) > 0")
        entry.append(f"단기 모멘텀({slb}일) > 0")
        tw = params.get("trend_window", 0)
        if tw > 0:
            entry.append(f"종가 > MA{tw} (시장 레짐)")
        rf = params.get("rebalance_freq", 20)
        entry.append(f"리밸런싱: {rf}일마다")

        exit_.append("장기/단기 모멘텀 모두 음수 전환")
        tw = params.get("trend_window", 0)
        if tw > 0:
            exit_.append(f"종가 < MA{tw}")

    return entry, exit_


def _next_version(strategies_dir: str, ticker: str) -> tuple[int, str]:
    """다음 버전 번호와 파일 경로 반환"""
    existing = [
        f for f in os.listdir(strategies_dir)
        if f.startswith(f"{ticker}_v") and f.endswith(".json")
    ]
    nums = []
    for f in existing:
        try:
            nums.append(int(f.replace(f"{ticker}_v", "").replace(".json", "")))
        except ValueError:
            pass
    version = max(nums) + 1 if nums else 1
    path = os.path.join(strategies_dir, f"{ticker}_v{version}.json")
    return version, path


def save_strategy(
    ticker: str,
    cycle: int,
    strategy_type: str,
    params: dict[str, Any],
    metrics: dict[str, Any],
    backtest_period: dict[str, str],
    project_root: str,
) -> str:
    """전략 JSON 저장 후 git 커밋. 저장된 파일 경로 반환."""

    strategies_dir = os.path.join(project_root, "strategies")
    os.makedirs(strategies_dir, exist_ok=True)

    version, file_path = _next_version(strategies_dir, ticker)
    entry_conditions, exit_conditions = _build_entry_exit(strategy_type, params)

    data = {
        "version": version,
        "ticker": ticker,
        "cycle": cycle,
        "optimized_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "backtest_period": backtest_period,
        "strategy": {
            "type": strategy_type,
            "params": params,
        },
        "entry_conditions": entry_conditions,
        "exit_conditions": exit_conditions,
        "performance": {
            "win_rate":       round(metrics.get("승률(%)", 0), 2),
            "profit_factor":  round(metrics.get("Profit Factor", 0), 3),
            "cagr":           round(metrics.get("연환산수익률(CAGR,%)", 0), 2),
            "mdd":            round(metrics.get("최대낙폭(MDD,%)", 0), 2),
            "sharpe":         round(metrics.get("Sharpe Ratio", 0), 3),
            "sortino":        round(metrics.get("Sortino Ratio", 0), 3),
            "expectancy":     round(metrics.get("Expectancy(%)", 0), 3),
            "total_trades":   int(metrics.get("총거래횟수", 0)),
            "avg_hold_days":  round(metrics.get("평균보유일", 0), 1),
        },
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    _git_commit(project_root, file_path, ticker, version, metrics)
    return file_path


def _git_commit(project_root: str, file_path: str, ticker: str, version: int, metrics: dict):
    """전략 파일을 git에 커밋"""
    wr = metrics.get("승률(%)", 0)
    pf = metrics.get("Profit Factor", 0)
    cagr = metrics.get("연환산수익률(CAGR,%)", 0)

    rel_path = os.path.relpath(file_path, project_root)
    msg = (
        f"strategy: {ticker} v{version} - "
        f"승률 {wr:.1f}% / PF {pf:.2f} / CAGR {cagr:+.1f}%"
    )

    try:
        subprocess.run(
            ["git", "add", rel_path],
            cwd=project_root, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=project_root, check=True, capture_output=True
        )
        print(f"  [저장] git 커밋: {msg}")
    except subprocess.CalledProcessError as e:
        print(f"  [!]️  git 커밋 실패: {e.stderr.decode().strip()}")
