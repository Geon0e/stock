"""
매수/매도 신호 판단 모듈

각 함수는 pandas DataFrame(OHLCV)을 받아 아래 형태의 dict를 반환합니다:

    {
        "signal":  "BUY" | "SELL" | "HOLD",
        "reason":  "판단 근거 문자열",
        "score":   0 ~ 100  (100에 가까울수록 강한 매수, 0에 가까울수록 강한 매도)
    }
"""

import pandas as pd
import numpy as np


# ────────────────────────────────────────────────────────────────────────────
# 내부 유틸
# ────────────────────────────────────────────────────────────────────────────

def _result(signal: str, reason: str, score: int) -> dict:
    return {"signal": signal, "reason": reason, "score": max(0, min(100, score))}


def _signal_from_score(score: float) -> str:
    if score >= 60:
        return "BUY"
    elif score <= 40:
        return "SELL"
    return "HOLD"


# ────────────────────────────────────────────────────────────────────────────
# 전략 1: 이동평균 골든크로스 / 데드크로스
#   단기(5일) MA가 장기(20일) MA를 상향 돌파 → 매수
#   단기(5일) MA가 장기(20일) MA를 하향 돌파 → 매도
#   크로스 직전 대비 현재 괴리율로 강도 조절
# ────────────────────────────────────────────────────────────────────────────

def strategy_ma_cross(df: pd.DataFrame) -> dict:
    if len(df) < 20:
        return _result("HOLD", "데이터 부족 (20일 미만)", 50)

    close = df["Close"]
    ma5  = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()

    cur_diff  = ma5.iloc[-1] - ma20.iloc[-1]
    prev_diff = ma5.iloc[-2] - ma20.iloc[-2]
    gap_pct   = cur_diff / ma20.iloc[-1] * 100  # 괴리율(%)

    if cur_diff > 0 and prev_diff <= 0:
        # 골든크로스 발생
        score  = min(85, 65 + abs(gap_pct) * 5)
        reason = f"골든크로스 발생 (MA5={ma5.iloc[-1]:,.0f} > MA20={ma20.iloc[-1]:,.0f}, 괴리={gap_pct:+.2f}%)"
    elif cur_diff < 0 and prev_diff >= 0:
        # 데드크로스 발생
        score  = max(15, 35 - abs(gap_pct) * 5)
        reason = f"데드크로스 발생 (MA5={ma5.iloc[-1]:,.0f} < MA20={ma20.iloc[-1]:,.0f}, 괴리={gap_pct:+.2f}%)"
    elif cur_diff > 0:
        # 정배열 유지
        score  = min(70, 55 + abs(gap_pct) * 2)
        reason = f"정배열 유지 (MA5 > MA20, 괴리={gap_pct:+.2f}%)"
    else:
        # 역배열 유지
        score  = max(30, 45 - abs(gap_pct) * 2)
        reason = f"역배열 유지 (MA5 < MA20, 괴리={gap_pct:+.2f}%)"

    return _result(_signal_from_score(score), reason, round(score))


# ────────────────────────────────────────────────────────────────────────────
# 전략 2: RSI (상대강도지수 14일)
#   RSI < 30 → 과매도 → 매수
#   RSI > 70 → 과매수 → 매도
#   30~70 → HOLD, 구간 내 선형 스코어
# ────────────────────────────────────────────────────────────────────────────

def strategy_rsi(df: pd.DataFrame) -> dict:
    period = 14
    if len(df) < period + 1:
        return _result("HOLD", f"데이터 부족 ({period + 1}일 미만)", 50)

    close  = df["Close"]
    deltas = close.diff().dropna()
    gains  = deltas.where(deltas > 0, 0)
    losses = (-deltas).where(deltas < 0, 0)

    # Wilder 평활 RSI
    avg_gain = gains.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs  = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]

    if pd.isna(val):
        return _result("HOLD", "RSI 계산 오류", 50)

    if val < 30:
        score  = round(80 - val * (80 - 60) / 30)   # 0→80, 30→60
        reason = f"RSI={val:.1f} 과매도 구간 (< 30)"
    elif val > 70:
        score  = round(40 - (val - 70) * (40 - 20) / 30)  # 70→40, 100→20
        reason = f"RSI={val:.1f} 과매수 구간 (> 70)"
    else:
        score  = round(60 - (val - 30) * 20 / 40)   # 30→60, 70→40
        reason = f"RSI={val:.1f} 중립 구간 (30~70)"

    return _result(_signal_from_score(score), reason, score)


# ────────────────────────────────────────────────────────────────────────────
# 전략 3: 볼린저 밴드 (20일, 2σ)
#   %B = (Close - 하단) / (상단 - 하단)
#   %B < 0.05 → 하단 이탈 → 매수
#   %B > 0.95 → 상단 이탈 → 매도
# ────────────────────────────────────────────────────────────────────────────

def strategy_bollinger(df: pd.DataFrame) -> dict:
    window = 20
    if len(df) < window:
        return _result("HOLD", f"데이터 부족 ({window}일 미만)", 50)

    close  = df["Close"]
    ma     = close.rolling(window).mean()
    std    = close.rolling(window).std(ddof=1)
    upper  = ma + 2 * std
    lower  = ma - 2 * std

    price  = close.iloc[-1]
    u, l   = upper.iloc[-1], lower.iloc[-1]

    if u == l:
        return _result("HOLD", "볼린저 밴드 폭 0 (변동 없음)", 50)

    pct_b  = (price - l) / (u - l)  # 0=하단, 1=상단

    if pct_b < 0:
        score  = round(min(85, 80 + abs(pct_b) * 20))
        reason = f"하단 밴드 이탈 (%B={pct_b:.2f}, 가격={price:,.0f}, 하단={l:,.0f})"
    elif pct_b > 1:
        score  = round(max(15, 20 - (pct_b - 1) * 20))
        reason = f"상단 밴드 이탈 (%B={pct_b:.2f}, 가격={price:,.0f}, 상단={u:,.0f})"
    elif pct_b < 0.2:
        score  = round(70 - pct_b * 50)
        reason = f"하단 밴드 근접 (%B={pct_b:.2f})"
    elif pct_b > 0.8:
        score  = round(50 - (pct_b - 0.5) * 60)
        reason = f"상단 밴드 근접 (%B={pct_b:.2f})"
    else:
        score  = round(65 - pct_b * 30)
        reason = f"밴드 중간 구간 (%B={pct_b:.2f})"

    return _result(_signal_from_score(score), reason, score)


# ────────────────────────────────────────────────────────────────────────────
# 전략 4: MACD (12, 26, 9)
#   MACD 선이 시그널 선을 상향 돌파 → 매수
#   MACD 선이 시그널 선을 하향 돌파 → 매도
#   히스토그램 방향 및 크기로 강도 조절
# ────────────────────────────────────────────────────────────────────────────

def strategy_macd(df: pd.DataFrame) -> dict:
    if len(df) < 35:
        return _result("HOLD", "데이터 부족 (35일 미만)", 50)

    close     = df["Close"]
    ema12     = close.ewm(span=12, adjust=False).mean()
    ema26     = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal    = macd_line.ewm(span=9, adjust=False).mean()
    hist      = macd_line - signal

    cur_hist  = hist.iloc[-1]
    prev_hist = hist.iloc[-2]
    macd_val  = macd_line.iloc[-1]
    sig_val   = signal.iloc[-1]

    # 히스토그램 변화율로 강도 측정
    hist_std = hist.std()
    strength = abs(cur_hist) / hist_std if hist_std > 0 else 0

    if cur_hist > 0 and prev_hist <= 0:
        # 골든크로스
        score  = min(85, round(70 + strength * 10))
        reason = f"MACD 골든크로스 (MACD={macd_val:.2f} > Signal={sig_val:.2f})"
    elif cur_hist < 0 and prev_hist >= 0:
        # 데드크로스
        score  = max(15, round(30 - strength * 10))
        reason = f"MACD 데드크로스 (MACD={macd_val:.2f} < Signal={sig_val:.2f})"
    elif cur_hist > 0:
        score  = min(72, round(55 + strength * 8))
        reason = f"MACD 양수 유지 (히스토그램={cur_hist:.2f})"
    else:
        score  = max(28, round(45 - strength * 8))
        reason = f"MACD 음수 유지 (히스토그램={cur_hist:.2f})"

    return _result(_signal_from_score(score), reason, score)


# ────────────────────────────────────────────────────────────────────────────
# 전략 5: 20일 가격 모멘텀
#   20일 수익률이 양수이고 상승 추세 → 매수
#   음수이고 하락 추세 → 매도
#   거래량 가중치 반영
# ────────────────────────────────────────────────────────────────────────────

def strategy_momentum(df: pd.DataFrame) -> dict:
    lookback = 20
    if len(df) < lookback + 1:
        return _result("HOLD", f"데이터 부족 ({lookback + 1}일 미만)", 50)

    close    = df["Close"]
    ret_pct  = (close.iloc[-1] / close.iloc[-lookback] - 1) * 100

    # 거래량 모멘텀: 최근 5일 vs 이전 15일 평균 거래량 비율
    vol_col  = "Volume" if "Volume" in df.columns else None
    vol_ratio = 1.0
    if vol_col:
        vol_recent = df[vol_col].iloc[-5:].mean()
        vol_prev   = df[vol_col].iloc[-lookback:-5].mean()
        vol_ratio  = vol_recent / vol_prev if vol_prev > 0 else 1.0

    # 기본 스코어: 수익률 기반 (±10% → 0~100 선형 매핑)
    raw_score = 50 + ret_pct * 2.5
    raw_score = max(10, min(90, raw_score))

    # 거래량 가중치 (상승 시 거래량 증가 → 강세 확인)
    if ret_pct > 0 and vol_ratio > 1.2:
        raw_score = min(85, raw_score + 5)
        vol_note  = f", 거래량 급증 ×{vol_ratio:.1f}"
    elif ret_pct < 0 and vol_ratio > 1.2:
        raw_score = max(15, raw_score - 5)
        vol_note  = f", 거래량 급증 ×{vol_ratio:.1f} (매도 압력)"
    else:
        vol_note  = ""

    reason = f"{lookback}일 수익률={ret_pct:+.2f}%{vol_note}"
    return _result(_signal_from_score(raw_score), reason, round(raw_score))


# ────────────────────────────────────────────────────────────────────────────
# 전략 등록 테이블
# 여러 개 등록하면 가중 평균 앙상블로 최종 신호를 결정합니다.
# ────────────────────────────────────────────────────────────────────────────

STRATEGIES = {
    "이동평균크로스": {"fn": strategy_ma_cross,  "weight": 2.0},
    "RSI":          {"fn": strategy_rsi,        "weight": 2.0},
    "볼린저밴드":    {"fn": strategy_bollinger,  "weight": 1.5},
    "MACD":         {"fn": strategy_macd,       "weight": 2.0},
    "모멘텀":       {"fn": strategy_momentum,   "weight": 1.5},
}


# ────────────────────────────────────────────────────────────────────────────
# 최종 신호 집계 (외부에서 호출하는 메인 함수)
# ────────────────────────────────────────────────────────────────────────────

def evaluate(df: pd.DataFrame) -> dict:
    """
    등록된 모든 전략을 실행하고 가중 앙상블 결과를 반환

    Returns:
        {
            "signal":   "BUY" | "SELL" | "HOLD",
            "score":    0~100,
            "details":  [{"name": ..., "signal": ..., "reason": ..., "score": ..., "weight": ...}, ...]
        }
    """
    if df is None or df.empty:
        return {"signal": "HOLD", "score": 50, "details": []}

    details      = []
    total_weight = 0.0
    weighted_sum = 0.0

    for name, cfg in STRATEGIES.items():
        fn     = cfg["fn"]
        weight = cfg["weight"]
        try:
            result = fn(df)
            details.append({"name": name, "weight": weight, **result})
            weighted_sum += result["score"] * weight
            total_weight += weight
        except Exception as e:
            details.append({"name": name, "weight": weight, "signal": "HOLD",
                            "reason": f"오류: {e}", "score": 50})
            weighted_sum += 50 * weight
            total_weight += weight

    avg_score = weighted_sum / total_weight if total_weight > 0 else 50

    if avg_score >= 60:
        final = "BUY"
    elif avg_score <= 40:
        final = "SELL"
    else:
        final = "HOLD"

    return {"signal": final, "score": round(avg_score), "details": details}
