"""
최적화된 전략을 MD 파일로 저장하고 git에 커밋하는 모듈
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from typing import Any


def _build_entry_exit(strategy_type: str, params: dict) -> tuple[list[str], list[str]]:
    entry, exit_ = [], []

    if strategy_type == "Breakout":
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
            exit_.append(f"ATR 손절: 진입가 - {tm} x ATR (트레일링)")
        pt = params.get("profit_target_mult", 0)
        if pt > 0:
            exit_.append(f"이익 목표: 진입가 + {pt} x ATR 도달 시 청산")

    elif strategy_type == "V2":
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
            exit_.append(f"ATR 손절: 진입가 - {tm} x ATR (트레일링)")

    return entry, exit_


def _next_version(strategies_dir: str, prefix: str) -> tuple[int, str]:
    existing = [
        f for f in os.listdir(strategies_dir)
        if f.startswith(f"{prefix}_v") and f.endswith(".md")
    ]
    nums = []
    for f in existing:
        try:
            nums.append(int(f.replace(f"{prefix}_v", "").replace(".md", "")))
        except ValueError:
            pass
    version = max(nums) + 1 if nums else 1
    path = os.path.join(strategies_dir, f"{prefix}_v{version}.md")
    return version, path


def save_strategy_md(
    strategy_type: str,
    params: dict[str, Any],
    aggregate: dict[str, Any],      # 전체 집계 지표
    per_stock: list[dict],           # 종목별 결과
    backtest_period: dict[str, str],
    project_root: str,
    cycle: int = 1,
) -> str:
    """코스피200 유니버설 전략을 MD 파일로 저장 후 git 커밋. 저장 경로 반환."""

    strategies_dir = os.path.join(project_root, "strategies")
    os.makedirs(strategies_dir, exist_ok=True)

    version, file_path = _next_version(strategies_dir, "KOSPI200")
    entry_conds, exit_conds = _build_entry_exit(strategy_type, params)

    wr   = aggregate.get("win_rate", 0)
    pf   = aggregate.get("profit_factor", 0)
    cagr = aggregate.get("avg_cagr", 0)
    mdd  = aggregate.get("avg_mdd", 0)
    total_trades   = aggregate.get("total_trades", 0)
    covered_stocks = aggregate.get("covered_stocks", 0)

    # 승률 상위 20개 종목
    top_stocks = sorted(per_stock, key=lambda x: x.get("win_rate", 0), reverse=True)[:20]

    # 파라미터 테이블
    param_rows = "\n".join(
        f"| {k} | {v} |" for k, v in params.items()
    )

    # 진입/청산 조건
    entry_md = "\n".join(f"{i+1}. {c}" for i, c in enumerate(entry_conds))
    exit_md  = "\n".join(f"{i+1}. {c}" for i, c in enumerate(exit_conds))

    # 상위 종목 테이블
    top_rows = "\n".join(
        f"| {r.get('ticker','')} | {r.get('name','')} | "
        f"{r.get('win_rate',0):.1f}% | {r.get('total_trades',0)} | "
        f"{r.get('profit_factor',0):.2f} | {r.get('cagr',0):+.1f}% |"
        for r in top_stocks
    )

    md = f"""# KOSPI 200 유니버설 전략 v{version}

> 최적화 기준: KOSPI 200 전 종목 합산 승률 최대화
> 생성일: {datetime.now().strftime("%Y-%m-%d %H:%M")} | 사이클: {cycle}

---

## 전략 개요

| 항목 | 내용 |
|------|------|
| 전략 유형 | {strategy_type} |
| 백테스팅 기간 | {backtest_period.get('start')} ~ {backtest_period.get('end')} |
| 대상 | KOSPI 200 전 종목 |
| 최적화 기준 | 전 종목 합산 승률 |

---

## 성과 지표

| 지표 | 값 |
|------|----|
| **전체 승률** | **{wr:.1f}%** |
| Profit Factor | {pf:.2f} |
| 평균 CAGR | {cagr:+.1f}% |
| 평균 MDD | {mdd:.1f}% |
| 총 거래 횟수 | {total_trades:,}회 |
| 적용 종목 수 | {covered_stocks}/200개 |

---

## 진입 조건

{entry_md}

## 청산 조건

{exit_md}

---

## 파라미터

| 파라미터 | 값 |
|---------|-----|
{param_rows}

---

## 승률 상위 20개 종목

| 티커 | 종목명 | 승률 | 거래수 | PF | CAGR |
|------|--------|------|--------|-----|------|
{top_rows}
"""

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(md)

    _git_commit(project_root, file_path, version, wr, pf, cagr)
    return file_path


def _git_commit(project_root: str, file_path: str, version: int,
                wr: float, pf: float, cagr: float):
    rel_path = os.path.relpath(file_path, project_root)
    msg = f"strategy: KOSPI200 v{version} - 승률 {wr:.1f}% / PF {pf:.2f} / CAGR {cagr:+.1f}%"
    try:
        subprocess.run(["git", "add", rel_path],
                       cwd=project_root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", msg],
                       cwd=project_root, check=True, capture_output=True)
        print(f"  [git] {msg}")
    except subprocess.CalledProcessError as e:
        print(f"  [!] git 커밋 실패: {e.stderr.decode().strip()}")
