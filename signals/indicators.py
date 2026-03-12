"""
매수/매도 신호 판단 모듈 (v2 — 동적 가중치 + 다이버전스 + ADX/스토캐스틱/OBV)

각 함수는 pandas DataFrame(OHLCV)을 받아 아래 형태의 dict를 반환합니다:

    {
        "signal":  "BUY" | "SELL" | "HOLD",
        "reason":  "판단 근거 문자열",
        "score":   0 ~ 100  (100에 가까울수록 강한 매수, 0에 가까울수록 강한 매도)
    }

[v2 주요 변경]
- ADX 전략 추가: 추세 강도 측정 → 동적 가중치 결정에 활용
- 스토캐스틱 전략 추가: 횡보장 특화 과매수/과매도 감지
- OBV 전략 추가: 거래량-가격 다이버전스/확인
- RSI 다이버전스 감지: 가격 신저점 + RSI 상승 → 강한 반전 신호
- MACD 다이버전스 감지: 히스토그램 기반 반전 포착
- 동적 가중치: ADX 값에 따라 추세 지표 vs 역추세 지표 가중치 자동 조정
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


def _has_ohlc(df: pd.DataFrame) -> bool:
    """High/Low 컬럼 존재 여부 확인"""
    return "High" in df.columns and "Low" in df.columns


def _calc_adx(df: pd.DataFrame, period: int = 14):
    """
    ADX, +DI, -DI 계산 (Wilder 평활)
    Returns: (adx Series, di_plus Series, di_minus Series)
    """
    high  = df["High"]
    low   = df["Low"]
    close = df["Close"]

    # True Range
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    # Directional Movement
    up   = high.diff()
    down = -low.diff()

    dm_plus  = up.where((up > down) & (up > 0), 0.0)
    dm_minus = down.where((down > up) & (down > 0), 0.0)

    alpha = 1 / period
    atr      = tr.ewm(alpha=alpha, adjust=False).mean()
    di_plus  = 100 * dm_plus.ewm(alpha=alpha, adjust=False).mean()  / atr.replace(0, np.nan)
    di_minus = 100 * dm_minus.ewm(alpha=alpha, adjust=False).mean() / atr.replace(0, np.nan)

    dx  = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    adx = dx.ewm(alpha=alpha, adjust=False).mean()

    return adx, di_plus, di_minus


def _calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR (Average True Range) 계산 — 노이즈 필터·변동성 측정에 사용"""
    high  = df["High"]
    low   = df["Low"]
    close = df["Close"]
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _atr_ratio(df: pd.DataFrame, period: int = 14) -> float:
    """ATR / 현재가 비율(%) — 변동성 레짐 판별용"""
    if not _has_ohlc(df) or len(df) < period + 1:
        return 2.0  # 기본값
    try:
        atr_val   = _calc_atr(df, period).iloc[-1]
        price_val = df["Close"].iloc[-1]
        return float(atr_val / price_val * 100) if price_val > 0 else 2.0
    except Exception:
        return 2.0


def _calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI 계산"""
    deltas = close.diff().dropna()
    gains  = deltas.where(deltas > 0, 0.0)
    losses = (-deltas).where(deltas < 0, 0.0)
    alpha  = 1 / period
    avg_gain = gains.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    avg_loss = losses.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    rs  = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ────────────────────────────────────────────────────────────────────────────
# 전략 1: 이동평균 골든크로스 / 데드크로스
# ────────────────────────────────────────────────────────────────────────────

def strategy_ma_cross(df: pd.DataFrame) -> dict:
    if len(df) < 20:
        return _result("HOLD", "데이터 부족 (20일 미만)", 50)

    close = df["Close"]
    ma5   = close.rolling(5).mean()
    ma20  = close.rolling(20).mean()

    cur_diff  = ma5.iloc[-1] - ma20.iloc[-1]
    prev_diff = ma5.iloc[-2] - ma20.iloc[-2]
    gap_pct   = cur_diff / ma20.iloc[-1] * 100

    if cur_diff > 0 and prev_diff <= 0:
        score  = min(85, 65 + abs(gap_pct) * 5)
        reason = f"골든크로스 (MA5={ma5.iloc[-1]:,.0f} > MA20={ma20.iloc[-1]:,.0f}, 괴리={gap_pct:+.2f}%)"
    elif cur_diff < 0 and prev_diff >= 0:
        score  = max(15, 35 - abs(gap_pct) * 5)
        reason = f"데드크로스 (MA5={ma5.iloc[-1]:,.0f} < MA20={ma20.iloc[-1]:,.0f}, 괴리={gap_pct:+.2f}%)"
    elif cur_diff > 0:
        score  = min(70, 55 + abs(gap_pct) * 2)
        reason = f"정배열 유지 (괴리={gap_pct:+.2f}%)"
    else:
        score  = max(30, 45 - abs(gap_pct) * 2)
        reason = f"역배열 유지 (괴리={gap_pct:+.2f}%)"

    # ── ATR 노이즈 필터: 크로스 갭이 ATR의 0.3배 미만 → 신호 감쇠 ─────────
    # 횡보장에서 MA5/MA20이 수렴하며 반복 크로스하는 오신호를 억제
    atr_note = ""
    if _has_ohlc(df) and len(df) >= 15:
        try:
            atr_val   = _calc_atr(df, 14).iloc[-1]
            cross_gap = abs(cur_diff)
            if atr_val > 0 and cross_gap < atr_val * 0.3:
                score    = round(50 + (score - 50) * 0.45)
                atr_note = f" [ATR노이즈필터: 갭({cross_gap:.0f}) < ATR×0.3({atr_val*0.3:.0f})]"
        except Exception:
            pass
    reason += atr_note

    return _result(_signal_from_score(score), reason, round(score))


# ────────────────────────────────────────────────────────────────────────────
# 전략 2: RSI (14일) + 다이버전스 감지
#   기본: 과매도(<30)→매수, 과매수(>70)→매도
#   추가: 가격 신저점 + RSI 상승 → 강세 다이버전스 (보너스 +15)
#          가격 신고점 + RSI 하락 → 약세 다이버전스 (패널티 -15)
# ────────────────────────────────────────────────────────────────────────────

def strategy_rsi(df: pd.DataFrame) -> dict:
    # ── 적응형 RSI 기간: 변동성에 따라 자동 조정 ─────────────────────────
    # 고변동성(ATR/가격 > 3%) → 빠른 RSI(9): 급격한 변화를 빠르게 포착
    # 저변동성(ATR/가격 < 1%) → 느린 RSI(21): 노이즈 제거
    # 일반                   → RSI(14): 기본값
    vol = _atr_ratio(df)
    if vol > 3.0:
        period = 9
        period_note = "(고변동성→RSI9)"
    elif vol < 1.0:
        period = 21
        period_note = "(저변동성→RSI21)"
    else:
        period = 14
        period_note = ""

    if len(df) < period + 1:
        return _result("HOLD", f"데이터 부족 ({period + 1}일 미만)", 50)

    close = df["Close"]
    rsi   = _calc_rsi(close, period)
    val   = rsi.iloc[-1]

    if pd.isna(val):
        return _result("HOLD", "RSI 계산 오류", 50)

    if val < 30:
        score  = round(80 - val * (80 - 60) / 30)
        reason = f"RSI{period_note}={val:.1f} 과매도 (<30)"
    elif val > 70:
        score  = round(40 - (val - 70) * (40 - 20) / 30)
        reason = f"RSI{period_note}={val:.1f} 과매수 (>70)"
    else:
        score  = round(60 - (val - 30) * 20 / 40)
        reason = f"RSI{period_note}={val:.1f} 중립 구간"

    # ── 다이버전스 감지 (최근 5봉 vs 이전 15봉) ──────────────────────────
    divergence_note = ""
    if len(df) >= 20 and len(rsi.dropna()) >= 20:
        price_recent_low  = close.iloc[-5:].min()
        price_prev_low    = close.iloc[-20:-5].min()
        rsi_recent_low    = rsi.iloc[-5:].min()
        rsi_prev_low      = rsi.iloc[-20:-5].min()

        price_recent_high = close.iloc[-5:].max()
        price_prev_high   = close.iloc[-20:-5].max()
        rsi_recent_high   = rsi.iloc[-5:].max()
        rsi_prev_high     = rsi.iloc[-20:-5].max()

        if price_recent_low < price_prev_low * 0.995 and rsi_recent_low > rsi_prev_low + 2:
            score += 15
            divergence_note = " + 강세 다이버전스(가격↓ RSI↑)"
        elif price_recent_high > price_prev_high * 1.005 and rsi_recent_high < rsi_prev_high - 2:
            score -= 15
            divergence_note = " + 약세 다이버전스(가격↑ RSI↓)"

    return _result(_signal_from_score(score), reason + divergence_note, round(score))


# ────────────────────────────────────────────────────────────────────────────
# 전략 3: 볼린저 밴드 (20일, 2σ)
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
        return _result("HOLD", "볼린저 밴드 폭 0", 50)

    pct_b = (price - l) / (u - l)

    # 밴드 폭 (Bandwidth): 수축 후 확장은 추세 시작 신호
    bw_now  = (u - l) / ma.iloc[-1] * 100
    bw_prev = ((upper.iloc[-5] - lower.iloc[-5]) / ma.iloc[-5] * 100) if len(df) >= 25 else bw_now
    bw_expanding = bw_now > bw_prev * 1.05  # 밴드 확장 중

    if pct_b < 0:
        score  = round(min(85, 80 + abs(pct_b) * 20))
        reason = f"하단 밴드 이탈 (%B={pct_b:.2f})"
    elif pct_b > 1:
        score  = round(max(15, 20 - (pct_b - 1) * 20))
        reason = f"상단 밴드 이탈 (%B={pct_b:.2f})"
    elif pct_b < 0.2:
        score  = round(70 - pct_b * 50)
        reason = f"하단 밴드 근접 (%B={pct_b:.2f})"
    elif pct_b > 0.8:
        score  = round(50 - (pct_b - 0.5) * 60)
        reason = f"상단 밴드 근접 (%B={pct_b:.2f})"
    else:
        score  = round(65 - pct_b * 30)
        reason = f"밴드 중간 구간 (%B={pct_b:.2f})"

    # 밴드 확장 중이면 현재 방향 강화
    bw_note = ""
    if bw_expanding:
        if pct_b > 0.5:
            score = min(90, score + 5)
            bw_note = ", 밴드 확장↑"
        else:
            score = max(10, score - 5)
            bw_note = ", 밴드 확장↓"
    reason += f" | BW={bw_now:.1f}%{bw_note}"

    return _result(_signal_from_score(score), reason, round(score))


# ────────────────────────────────────────────────────────────────────────────
# 전략 4: MACD (12, 26, 9) + 다이버전스 감지
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
    hist_std  = hist.std()
    strength  = abs(cur_hist) / hist_std if hist_std > 0 else 0

    if cur_hist > 0 and prev_hist <= 0:
        score  = min(85, round(70 + strength * 10))
        reason = f"MACD 골든크로스 (MACD={macd_val:.2f} > Signal={sig_val:.2f})"
    elif cur_hist < 0 and prev_hist >= 0:
        score  = max(15, round(30 - strength * 10))
        reason = f"MACD 데드크로스 (MACD={macd_val:.2f} < Signal={sig_val:.2f})"
    elif cur_hist > 0:
        score  = min(72, round(55 + strength * 8))
        reason = f"MACD 양수 유지 (히스토그램={cur_hist:.2f})"
    else:
        score  = max(28, round(45 - strength * 8))
        reason = f"MACD 음수 유지 (히스토그램={cur_hist:.2f})"

    # ── 히스토그램 다이버전스 감지 ───────────────────────────────────────
    divergence_note = ""
    if len(df) >= 20:
        hist_recent_trough = hist.iloc[-5:].min()
        hist_prev_trough   = hist.iloc[-20:-5].min()
        hist_recent_peak   = hist.iloc[-5:].max()
        hist_prev_peak     = hist.iloc[-20:-5].max()
        price_recent_low   = close.iloc[-5:].min()
        price_prev_low     = close.iloc[-20:-5].min()
        price_recent_high  = close.iloc[-5:].max()
        price_prev_high    = close.iloc[-20:-5].max()

        # 강세 다이버전스: 가격 신저점 + 히스토그램 저점 상승 (음수 구간)
        if (hist_recent_trough < 0 and hist_prev_trough < 0
                and price_recent_low < price_prev_low * 0.995
                and hist_recent_trough > hist_prev_trough):
            score += 12
            divergence_note = " + 강세 다이버전스"
        # 약세 다이버전스: 가격 신고점 + 히스토그램 고점 하락 (양수 구간)
        elif (hist_recent_peak > 0 and hist_prev_peak > 0
              and price_recent_high > price_prev_high * 1.005
              and hist_recent_peak < hist_prev_peak):
            score -= 12
            divergence_note = " + 약세 다이버전스"

    return _result(_signal_from_score(score), reason + divergence_note, round(score))


# ────────────────────────────────────────────────────────────────────────────
# 전략 5: 20일 가격 모멘텀 + 거래량 모멘텀
# ────────────────────────────────────────────────────────────────────────────

def strategy_momentum(df: pd.DataFrame) -> dict:
    lookback = 20
    if len(df) < lookback + 1:
        return _result("HOLD", f"데이터 부족 ({lookback + 1}일 미만)", 50)

    close   = df["Close"]
    ret_pct = (close.iloc[-1] / close.iloc[-lookback] - 1) * 100

    # 거래량 모멘텀: 최근 5일 vs 이전 15일
    vol_ratio = 1.0
    vol_note  = ""
    if "Volume" in df.columns:
        vol_recent = df["Volume"].iloc[-5:].mean()
        vol_prev   = df["Volume"].iloc[-lookback:-5].mean()
        vol_ratio  = vol_recent / vol_prev if vol_prev > 0 else 1.0

    # 기본 스코어: 수익률 ±10% → 0~100 선형 매핑
    raw_score = 50 + ret_pct * 2.5
    raw_score = max(10, min(90, raw_score))

    # 거래량 가중 (상승+거래량 증가 → 강세 확인 / 하락+거래량 증가 → 매도 압력)
    if ret_pct > 0 and vol_ratio > 1.2:
        raw_score = min(87, raw_score + 7)
        vol_note  = f", 거래량 급증 ×{vol_ratio:.1f}(상승 확인)"
    elif ret_pct < 0 and vol_ratio > 1.2:
        raw_score = max(13, raw_score - 7)
        vol_note  = f", 거래량 급증 ×{vol_ratio:.1f}(매도 압력)"
    elif ret_pct > 0 and vol_ratio < 0.7:
        raw_score = max(10, raw_score - 5)
        vol_note  = f", 거래량 급감(상승 신뢰도↓)"

    reason = f"{lookback}일 수익률={ret_pct:+.2f}%{vol_note}"
    return _result(_signal_from_score(raw_score), reason, round(raw_score))


# ────────────────────────────────────────────────────────────────────────────
# 전략 6: ADX (Average Directional Index, 14일)
#   추세 강도 측정 + 방향 판단
#   ADX > 25: 강한 추세 (+DI/-DI 방향으로 신호)
#   ADX < 20: 횡보장 (신호 신뢰도 낮음)
#   → 이 전략 자체의 score 외에 evaluate()에서 동적 가중치에도 활용
# ────────────────────────────────────────────────────────────────────────────

def strategy_adx(df: pd.DataFrame) -> dict:
    if not _has_ohlc(df):
        return _result("HOLD", "High/Low 데이터 없음 (ADX 계산 불가)", 50)
    if len(df) < 30:
        return _result("HOLD", "데이터 부족 (30일 미만)", 50)

    adx, di_plus, di_minus = _calc_adx(df)
    adx_val  = adx.iloc[-1]
    dip_val  = di_plus.iloc[-1]
    dim_val  = di_minus.iloc[-1]

    if pd.isna(adx_val):
        return _result("HOLD", "ADX 계산 오류", 50)

    # 추세 강도에 따른 기본 스코어
    if adx_val >= 25:
        if dip_val > dim_val:
            # 강한 상승 추세
            trend_score = min(80, 60 + (adx_val - 25) * 0.8)
            reason = f"강한 상승추세 (ADX={adx_val:.1f}, +DI={dip_val:.1f} > -DI={dim_val:.1f})"
        else:
            # 강한 하락 추세
            trend_score = max(20, 40 - (adx_val - 25) * 0.8)
            reason = f"강한 하락추세 (ADX={adx_val:.1f}, -DI={dim_val:.1f} > +DI={dip_val:.1f})"
    elif adx_val >= 20:
        if dip_val > dim_val:
            trend_score = 58
            reason = f"추세 형성 중 (ADX={adx_val:.1f}, 상승 방향)"
        else:
            trend_score = 42
            reason = f"추세 형성 중 (ADX={adx_val:.1f}, 하락 방향)"
    else:
        # 횡보장: 중립에 가깝되, DI 방향 소폭 반영
        if dip_val > dim_val:
            trend_score = 52
        else:
            trend_score = 48
        reason = f"횡보장 (ADX={adx_val:.1f}, 신호 신뢰도↓)"

    return _result(_signal_from_score(trend_score), reason, round(trend_score))


# ────────────────────────────────────────────────────────────────────────────
# 전략 7: 스토캐스틱 오실레이터 (14, 3, 3)
#   횡보장에서 RSI보다 정확한 과매수/과매도 감지
#   %K < 20 & %D < 20: 과매도 → 매수
#   %K > 80 & %D > 80: 과매수 → 매도
#   크로스 방향으로 강도 조절
# ────────────────────────────────────────────────────────────────────────────

def strategy_stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> dict:
    if not _has_ohlc(df):
        return _result("HOLD", "High/Low 데이터 없음 (스토캐스틱 계산 불가)", 50)
    if len(df) < k_period + d_period:
        return _result("HOLD", f"데이터 부족 ({k_period + d_period}일 미만)", 50)

    high  = df["High"]
    low   = df["Low"]
    close = df["Close"]

    low_min  = low.rolling(k_period).min()
    high_max = high.rolling(k_period).max()
    denom    = (high_max - low_min).replace(0, np.nan)

    stoch_k = 100 * (close - low_min) / denom
    stoch_d = stoch_k.rolling(d_period).mean()

    k      = stoch_k.iloc[-1]
    d      = stoch_d.iloc[-1]
    k_prev = stoch_k.iloc[-2]
    d_prev = stoch_d.iloc[-2]

    if pd.isna(k) or pd.isna(d):
        return _result("HOLD", "스토캐스틱 계산 오류", 50)

    # %K가 %D를 상향 돌파: 크로스업 / 하향 돌파: 크로스다운
    cross_up   = k > d and k_prev <= d_prev
    cross_down = k < d and k_prev >= d_prev

    if k < 20 and d < 20:
        base  = 75
        extra = 10 if cross_up else 0
        score = min(88, base + extra)
        reason = f"과매도 (%K={k:.1f}, %D={d:.1f})" + (" + 크로스업" if cross_up else "")
    elif k > 80 and d > 80:
        base  = 25
        extra = 10 if cross_down else 0
        score = max(12, base - extra)
        reason = f"과매수 (%K={k:.1f}, %D={d:.1f})" + (" + 크로스다운" if cross_down else "")
    elif k < 50:
        # 50 미만: 하강 영역
        score  = round(55 - (50 - k) * 0.3)
        reason = f"하강 영역 (%K={k:.1f}, %D={d:.1f})"
    else:
        score  = round(45 + (k - 50) * 0.3)
        reason = f"상승 영역 (%K={k:.1f}, %D={d:.1f})"

    return _result(_signal_from_score(score), reason, round(score))


# ────────────────────────────────────────────────────────────────────────────
# 전략 8: OBV (On-Balance Volume) 추세 분석
#   거래량이 가격 움직임을 선행하는 특성 활용
#   OBV 추세 ↑ + 가격 추세 ↑ → 강세 확인
#   OBV 추세 ↑ + 가격 추세 ↓ → 강세 다이버전스 (반전 기대)
#   OBV 추세 ↓ + 가격 추세 ↑ → 약세 다이버전스 (하락 경고)
# ────────────────────────────────────────────────────────────────────────────

def strategy_obv(df: pd.DataFrame) -> dict:
    if "Volume" not in df.columns:
        return _result("HOLD", "거래량 데이터 없음 (OBV 계산 불가)", 50)
    if len(df) < 20:
        return _result("HOLD", "데이터 부족 (20일 미만)", 50)

    close  = df["Close"]
    volume = df["Volume"]

    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    obv       = (direction * volume).cumsum()

    # 선형 회귀로 기울기 계산 (최근 20봉)
    obv_20   = obv.iloc[-20:].values
    price_20 = close.iloc[-20:].values
    x        = np.arange(len(obv_20))

    if len(obv_20) < 5:
        return _result("HOLD", "OBV 계산 데이터 부족", 50)

    obv_slope   = np.polyfit(x, obv_20, 1)[0]
    price_slope = np.polyfit(x, price_20, 1)[0]

    # 기울기 정규화 (절대값 기준)
    obv_mean   = np.abs(obv_20).mean()
    price_mean = price_20.mean()
    obv_slope_n   = obv_slope   / obv_mean   if obv_mean   > 0 else 0
    price_slope_n = price_slope / price_mean if price_mean > 0 else 0

    obv_up    = obv_slope_n   > 0.001
    obv_down  = obv_slope_n   < -0.001
    price_up  = price_slope_n > 0.001
    price_down = price_slope_n < -0.001

    if obv_up and price_up:
        score  = 68
        reason = f"OBV↑ + 가격↑ 강세 확인"
    elif obv_up and price_down:
        score  = 72
        reason = f"OBV↑ + 가격↓ 강세 다이버전스 (반전 기대)"
    elif obv_down and price_up:
        score  = 32
        reason = f"OBV↓ + 가격↑ 약세 다이버전스 (하락 경고)"
    elif obv_down and price_down:
        score  = 32
        reason = f"OBV↓ + 가격↓ 하락 확인"
    else:
        score  = 50
        reason = f"OBV 방향 불명확 (중립)"

    return _result(_signal_from_score(score), reason, score)


# ────────────────────────────────────────────────────────────────────────────
# 전략 9: 이벤트 필터 — 갭·패닉셀·돌파 거래량 감지
#   급락/이벤트성 변동에 대응하기 위한 신규 전략
#   갭 업/다운: 시가가 전일 종가 대비 ±2% 이상 이탈
#   패닉셀:    거래량 급증(×3↑) + 당일 -3% 이하 → 역발상 매수 기회
#   돌파 매수: 거래량 급증(×2↑) + 당일 +2% 이상 → 추세 시작 확인
# ────────────────────────────────────────────────────────────────────────────

def strategy_event_filter(df: pd.DataFrame) -> dict:
    if len(df) < 21:
        return _result("HOLD", "데이터 부족 (21일 미만)", 50)

    close = df["Close"]

    # 당일 등락률
    today_ret = (close.iloc[-1] / close.iloc[-2] - 1) * 100 if len(df) >= 2 else 0.0

    # 갭 계산 (시가 vs 전일 종가)
    gap_pct = 0.0
    if "Open" in df.columns and len(df) >= 2:
        gap_pct = (df["Open"].iloc[-1] / close.iloc[-2] - 1) * 100

    # 거래량 스파이크 (당일 vs 20일 평균)
    vol_ratio = 1.0
    if "Volume" in df.columns:
        avg_vol = df["Volume"].iloc[-21:-1].mean()
        if avg_vol > 0:
            vol_ratio = df["Volume"].iloc[-1] / avg_vol

    # ── 신호 판단 ──────────────────────────────────────────────────────────
    if vol_ratio > 3.0 and today_ret < -3.0:
        # 패닉셀: 거래량 폭증 + 급락 → 과매도 반등 기대
        score  = 70
        reason = f"패닉셀 감지 (거래량×{vol_ratio:.1f}, {today_ret:+.1f}%) → 반등 기대"
    elif vol_ratio > 2.0 and today_ret > 2.0:
        # 돌파 매수: 거래량 확인 + 강세 → 추세 개시
        score  = 72
        reason = f"거래량 돌파 (×{vol_ratio:.1f}, {today_ret:+.1f}%) → 추세 확인"
    elif gap_pct > 2.0:
        score  = 65
        reason = f"갭 업 {gap_pct:+.1f}% (상승 모멘텀)"
    elif gap_pct < -2.0:
        score  = 35
        reason = f"갭 다운 {gap_pct:+.1f}% (하방 압력)"
    elif vol_ratio > 1.5 and today_ret < -1.5:
        score  = 40
        reason = f"거래량 증가 매도 (×{vol_ratio:.1f}, {today_ret:+.1f}%)"
    elif vol_ratio > 1.5 and today_ret > 1.5:
        score  = 62
        reason = f"거래량 증가 상승 (×{vol_ratio:.1f}, {today_ret:+.1f}%)"
    else:
        score  = 50
        reason = f"특이 이벤트 없음 (거래량×{vol_ratio:.1f}, 갭{gap_pct:+.1f}%)"

    return _result(_signal_from_score(score), reason, score)


# ────────────────────────────────────────────────────────────────────────────
# 전략 10: 52주 지지·저항
#   현재가의 52주(최대 250거래일) 범위 내 위치로 지지·저항 강도 측정
#   52주 신고점 돌파: 강한 매수 (저항 돌파 = 모멘텀)
#   52주 저점 근접:  매수 (지지대 반등 기대)
#   52주 고점 근접:  매도 (저항권 진입)
# ────────────────────────────────────────────────────────────────────────────

def strategy_support_resistance(df: pd.DataFrame) -> dict:
    period = min(250, len(df) - 1)
    if period < 20:
        return _result("HOLD", "데이터 부족 (20일 미만)", 50)

    close    = df["Close"]
    price    = close.iloc[-1]
    high_52w = close.iloc[-period - 1:-1].max()
    low_52w  = close.iloc[-period - 1:-1].min()
    range_52w = high_52w - low_52w

    if range_52w == 0:
        return _result("HOLD", "가격 범위 없음", 50)

    position = (price - low_52w) / range_52w   # 0 = 저점, 1 = 고점

    if price >= high_52w * 0.998:
        # 52주 신고점 돌파/근접: 강한 모멘텀
        score  = 78
        reason = f"52주 신고점 돌파/근접 ({price:,.0f} ≥ {high_52w:,.0f}, 위치 {position:.0%})"
    elif position < 0.05:
        # 52주 저점 5% 이내: 강한 지지
        score  = 73
        reason = f"52주 저점 근접 ({price:,.0f} / 저점 {low_52w:,.0f}, 위치 {position:.0%})"
    elif position < 0.20:
        score  = 63
        reason = f"52주 하위 구간 (위치 {position:.0%}) — 지지대 접근"
    elif position > 0.85:
        # 상위 15%: 저항권
        score  = 37
        reason = f"52주 저항 구간 (위치 {position:.0%}) — 고점 부담"
    elif position > 0.70:
        score  = 45
        reason = f"52주 상단 접근 (위치 {position:.0%})"
    else:
        # 중간 구간: 중립 (위치에 따라 완만하게 조정)
        score  = round(65 - position * 30)
        reason = f"52주 중간 구간 (위치 {position:.0%})"

    return _result(_signal_from_score(score), reason, score)


# ────────────────────────────────────────────────────────────────────────────
# 전략 등록 테이블
# ────────────────────────────────────────────────────────────────────────────

STRATEGIES = {
    "이동평균크로스": {"fn": strategy_ma_cross,          "weight": 2.0},
    "RSI":           {"fn": strategy_rsi,                "weight": 2.0},
    "볼린저밴드":    {"fn": strategy_bollinger,          "weight": 1.5},
    "MACD":          {"fn": strategy_macd,               "weight": 2.0},
    "모멘텀":        {"fn": strategy_momentum,           "weight": 1.5},
    "ADX":           {"fn": strategy_adx,                "weight": 1.5},
    "스토캐스틱":    {"fn": strategy_stochastic,         "weight": 1.5},
    "OBV":           {"fn": strategy_obv,                "weight": 1.5},
    "이벤트필터":    {"fn": strategy_event_filter,       "weight": 1.5},  # 갭·패닉셀
    "52주지지저항":  {"fn": strategy_support_resistance, "weight": 1.5},  # 고저점 맥락
}

# ────────────────────────────────────────────────────────────────────────────
# 매크로 환경 전략 (macro dict 필요 — evaluate()에서 선택적으로 실행)
# ────────────────────────────────────────────────────────────────────────────

def strategy_macro_context(df: pd.DataFrame, macro: dict) -> dict:
    """
    VIX·DXY·Gold·WTI 기반 거시환경 신호.

    macro: fetch_all() 반환 dict
        {"vix": {"value": float, "change_pct": float, ...}, "dxy": ..., "gold": ..., "wti": ...}
    """
    score   = 50
    reasons = []

    # ── VIX (공포지수) — 가장 비중 높음 ──────────────────────────────────
    vix_d   = macro.get("vix", {})
    vix_val = vix_d.get("value")
    if vix_val is not None and not pd.isna(vix_val):
        if vix_val < 15:
            score += 13
            reasons.append(f"VIX {vix_val:.1f} 저공포(강세)")
        elif vix_val < 20:
            score += 6
            reasons.append(f"VIX {vix_val:.1f} 안정")
        elif vix_val < 25:
            score -= 3
            reasons.append(f"VIX {vix_val:.1f} 중립")
        elif vix_val < 30:
            score -= 11
            reasons.append(f"VIX {vix_val:.1f} 공포상승")
        else:
            score -= 20
            reasons.append(f"VIX {vix_val:.1f} 고공포(약세)")

        vix_chg = vix_d.get("change_pct") or 0.0
        if vix_chg > 10:
            score -= 5
            reasons.append(f"VIX +{vix_chg:.1f}% 급등")
        elif vix_chg < -10:
            score += 3
            reasons.append(f"VIX {vix_chg:.1f}% 급락(안도)")

    # ── DXY (달러인덱스) — 달러강세=위험자산 불리 ────────────────────────
    dxy_chg = (macro.get("dxy", {}).get("change_pct") or 0.0)
    if dxy_chg > 0.8:
        score -= 5
        reasons.append(f"DXY +{dxy_chg:.1f}% 달러강세")
    elif dxy_chg > 0.3:
        score -= 2
        reasons.append(f"DXY +{dxy_chg:.1f}% 달러소폭강세")
    elif dxy_chg < -0.8:
        score += 5
        reasons.append(f"DXY {dxy_chg:.1f}% 달러약세(위험선호)")
    elif dxy_chg < -0.3:
        score += 2
        reasons.append(f"DXY {dxy_chg:.1f}% 달러소폭약세")

    # ── Gold (금) — 안전자산 쏠림 여부 ──────────────────────────────────
    gold_chg = (macro.get("gold", {}).get("change_pct") or 0.0)
    if gold_chg > 2.0:
        score -= 6
        reasons.append(f"금 +{gold_chg:.1f}% 안전자산 쏠림")
    elif gold_chg > 1.0:
        score -= 3
        reasons.append(f"금 +{gold_chg:.1f}% 소폭 안전선호")
    elif gold_chg < -1.5:
        score += 3
        reasons.append(f"금 {gold_chg:.1f}% 위험선호 복귀")

    # ── WTI (유가) — 급등=인플레, 급락=경기침체 ──────────────────────────
    wti_chg = (macro.get("wti", {}).get("change_pct") or 0.0)
    if wti_chg > 4.0:
        score -= 4
        reasons.append(f"WTI +{wti_chg:.1f}% 인플레 우려")
    elif wti_chg < -4.0:
        score -= 3
        reasons.append(f"WTI {wti_chg:.1f}% 경기침체 우려")

    score = max(0, min(100, round(score)))

    if score >= 60:
        signal = "BUY"
    elif score <= 40:
        signal = "SELL"
    else:
        signal = "HOLD"

    reason = " | ".join(reasons) if reasons else "매크로 데이터 없음"
    return {"signal": signal, "score": score, "reason": reason}

# 동적 가중치: 추세장 (ADX ≥ 25)
# 추세추종 지표 강화, 역추세 지표 축소, 이벤트·지지저항은 중립
_WEIGHTS_TREND = {
    "이동평균크로스": 2.5,
    "RSI":           1.5,
    "볼린저밴드":    1.0,
    "MACD":          2.5,
    "모멘텀":        2.0,
    "ADX":           2.0,
    "스토캐스틱":    1.0,
    "OBV":           1.5,
    "이벤트필터":    1.5,
    "52주지지저항":  1.5,
    "매크로환경":    2.0,
}

# 동적 가중치: 횡보장 (ADX < 20)
# 역추세 지표 강화, 52주 지지저항 상향 (횡보=지지저항에서 반등)
_WEIGHTS_RANGING = {
    "이동평균크로스": 1.0,
    "RSI":           2.5,
    "볼린저밴드":    2.5,
    "MACD":          1.5,
    "모멘텀":        1.0,
    "ADX":           0.5,
    "스토캐스틱":    2.5,
    "OBV":           1.5,
    "이벤트필터":    1.0,
    "52주지지저항":  2.0,
    "매크로환경":    2.0,
}

# 동적 가중치: 중립/전환 (20 ≤ ADX < 25)
_WEIGHTS_DEFAULT = {name: cfg["weight"] for name, cfg in STRATEGIES.items()}
_WEIGHTS_DEFAULT["매크로환경"] = 2.0


# ────────────────────────────────────────────────────────────────────────────
# 최종 신호 집계 (외부에서 호출하는 메인 함수)
# ────────────────────────────────────────────────────────────────────────────

def evaluate(df: pd.DataFrame, macro: dict = None) -> dict:
    """
    등록된 모든 전략을 실행하고 가중 앙상블 결과를 반환.
    ADX 값에 따라 동적으로 가중치를 조정.

    Args:
        df:    OHLCV DataFrame
        macro: fetch_all() 반환 dict (선택). 제공 시 매크로환경 전략 추가 실행.

    Returns:
        {
            "signal":      "BUY" | "SELL" | "HOLD",
            "score":       0~100,
            "regime":      "추세장" | "횡보장" | "중립",
            "adx":         float | None,
            "details":     [{"name", "signal", "reason", "score", "weight"}, ...]
        }
    """
    if df is None or df.empty:
        return {"signal": "HOLD", "score": 50, "regime": "중립", "adx": None, "details": []}

    # ── ADX로 시장 상태 판별 ──────────────────────────────────────────────
    regime  = "중립"
    adx_val = None
    weights = _WEIGHTS_DEFAULT

    if _has_ohlc(df) and len(df) >= 30:
        try:
            adx_series, _, _ = _calc_adx(df)
            adx_val = adx_series.iloc[-1]
            if not pd.isna(adx_val):
                if adx_val >= 25:
                    regime  = "추세장"
                    weights = _WEIGHTS_TREND
                elif adx_val < 20:
                    regime  = "횡보장"
                    weights = _WEIGHTS_RANGING
        except Exception:
            pass

    # ── 전략별 실행 ───────────────────────────────────────────────────────
    details      = []
    total_weight = 0.0
    weighted_sum = 0.0

    for name, cfg in STRATEGIES.items():
        fn     = cfg["fn"]
        weight = weights.get(name, cfg["weight"])
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

    # ── 매크로 환경 전략 (macro 제공 시 추가) ────────────────────────────
    if macro:
        macro_weight = weights.get("매크로환경", 2.0)
        try:
            macro_result = strategy_macro_context(df, macro)
            details.append({"name": "매크로환경", "weight": macro_weight, **macro_result})
            weighted_sum += macro_result["score"] * macro_weight
            total_weight += macro_weight
        except Exception as e:
            details.append({"name": "매크로환경", "weight": macro_weight, "signal": "HOLD",
                            "reason": f"오류: {e}", "score": 50})
            weighted_sum += 50 * macro_weight
            total_weight += macro_weight

    avg_score = weighted_sum / total_weight if total_weight > 0 else 50

    # ── 신호 합의 보정: 전략 5개 이상이 같은 방향 → ±3점 추가 ──────────
    buy_count  = sum(1 for d in details if d["signal"] == "BUY")
    sell_count = sum(1 for d in details if d["signal"] == "SELL")
    n = len(details)
    if buy_count >= n * 0.7:
        avg_score = min(100, avg_score + 3)
    elif sell_count >= n * 0.7:
        avg_score = max(0, avg_score - 3)

    if avg_score >= 60:
        final = "BUY"
    elif avg_score <= 40:
        final = "SELL"
    else:
        final = "HOLD"

    return {
        "signal":  final,
        "score":   round(avg_score),
        "regime":  regime,
        "adx":     round(adx_val, 1) if adx_val is not None else None,
        "details": details,
    }
