"""
볼린저 밴드 전략 V2

승율 개선 포인트:
    1. 하단 밴드 터치 → 하단 밴드 아래에서 위로 복귀 (확인 진입)
    2. 추세 필터: 가격 > MA50 (하락 추세 물타기 방지)
    3. %B 지표 기반 포지션 상태 추적 (더 정확한 신호)
    4. ATR 트레일링 스톱 추가 (손절 없음 문제 해결)
    5. 밴드폭 필터 (Bandwidth): 변동성이 낮은 횡보장 진입 제한
    6. 포지션 동기화 (외부 청산 반영)

진입 조건 (모두 충족):
    1. 전봉 종가 < 하단 밴드 (과매도 영역 진입 확인)
    2. 현재 종가 > 하단 밴드 (하단 밴드 위로 복귀 = 반등 확인)
    3. 가격 > MA50 (상승 추세 컨텍스트)
    4. 밴드폭 > min_bandwidth (변동성 충분한 구간)

청산 조건 (하나라도 충족):
    1. 종가 > 상단 밴드 (이익 실현)
    2. 종가 > MA(window) (중선 회귀 완료)  ← 빠른 청산 옵션
    3. 가격 < MA50 (추세 이탈 방어)
    4. ATR 트레일링 스톱 트리거
"""

import pandas as pd
import numpy as np
from typing import Optional
from .base import BaseStrategy


class BollingerBandStrategy(BaseStrategy):
    """
    볼린저 밴드 전략 V2 (승율 개선)

    - 확인 진입: 하단 밴드 아래 → 위로 복귀 시 매수
    - 추세 필터: 가격 > MA50에서만 매수
    - ATR 트레일링 스톱
    """

    def __init__(
        self,
        ticker:          str,
        window:          int   = 20,
        num_std:         float = 2.0,
        invest_pct:      float = 1.0,
        trend_window:    int   = 50,       # 추세 필터 MA 기간 (0=비활성화)
        trail_mult:      float = 2.0,      # ATR 트레일링 스톱 배수 (0=비활성화)
        exit_at_mid:     bool  = False,    # True면 중선(MA) 도달 시 청산 (빠른 이익 실현)
        min_bandwidth:   float = 0.03,     # 최소 밴드폭 (Band/MA, 0=비활성화)
        market_df:       Optional[pd.DataFrame] = None,
        regime_window:   int   = 200,
        use_vol_sizing:  bool  = False,
        risk_pct:        float = 0.02,
    ):
        self.ticker          = ticker
        self.window          = window
        self.num_std         = num_std
        self.invest_pct      = invest_pct
        self.trend_window    = trend_window
        self.trail_mult      = trail_mult
        self.exit_at_mid     = exit_at_mid
        self.min_bandwidth   = min_bandwidth
        self.market_df       = market_df
        self.regime_window   = regime_window
        self.use_vol_sizing  = use_vol_sizing
        self.risk_pct        = risk_pct

        self.price_history:  list = []
        self.high_history:   list = []
        self.low_history:    list = []

        self.in_position:         bool  = False
        self.stop_armed:          bool  = False
        self.highest_since_entry: float = 0.0
        self.prev_price:          float = 0.0   # 직전 종가 (크로스 감지용)
        self.was_below_lower:     bool  = False  # 직전 봉 하단 밴드 하회 여부

        # 시장 레짐 MA 사전 계산
        self._market_ma: Optional[pd.Series] = None
        if market_df is not None and not market_df.empty:
            col = "Close" if "Close" in market_df.columns else market_df.columns[0]
            self._market_ma = market_df[col].rolling(regime_window, min_periods=regime_window).mean()

    def initialize(self, engine) -> None:
        pass

    # ── 보조 지표 ────────────────────────────────────────────────────────

    def _bollinger(self):
        """볼린저 밴드 계산 → (upper, mid, lower, bandwidth)"""
        if len(self.price_history) < self.window:
            return None, None, None, 0.0
        w = self.price_history[-self.window:]
        ma  = float(np.mean(w))
        std = float(np.std(w, ddof=1))
        upper = ma + self.num_std * std
        lower = ma - self.num_std * std
        bw    = (upper - lower) / ma if ma > 0 else 0.0   # 밴드폭 (정규화)
        return upper, ma, lower, bw

    def _atr(self, period: int = 14) -> float:
        """Wilder 평활 ATR"""
        need = period + 1
        if len(self.high_history) < need:
            return 0.0
        highs  = self.high_history[-need:]
        lows   = self.low_history[-need:]
        closes = self.price_history[-need:]
        trs = []
        for i in range(1, len(highs)):
            c_prev = closes[i - 1]
            tr = max(highs[i] - lows[i],
                     abs(highs[i] - c_prev),
                     abs(lows[i] - c_prev))
            trs.append(tr)
        if not trs:
            return 0.0
        atr = sum(trs[:period]) / period
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        return atr

    def _regime_ok(self, date: pd.Timestamp) -> bool:
        if self._market_ma is None:
            return True
        try:
            ma_series = self._market_ma.loc[:date].dropna()
            if ma_series.empty:
                return True
            ma_val    = float(ma_series.iloc[-1])
            col       = "Close" if "Close" in self.market_df.columns else self.market_df.columns[0]
            close_val = float(self.market_df[col].loc[:date].iloc[-1])
            return close_val > ma_val
        except Exception:
            return True

    # ── 매 봉 처리 ───────────────────────────────────────────────────────

    def on_bar(self, engine, date: pd.Timestamp, data: dict, prices: dict) -> None:
        if self.ticker not in prices:
            return

        price = prices[self.ticker]
        row   = data.get(self.ticker)

        self.price_history.append(price)
        if row is not None:
            try:
                self.high_history.append(float(row["High"]) if "High" in row.index else price)
                self.low_history.append( float(row["Low"])  if "Low"  in row.index else price)
            except Exception:
                self.high_history.append(price)
                self.low_history.append(price)
        else:
            self.high_history.append(price)
            self.low_history.append(price)

        # ── 포지션 동기화 ─────────────────────────────────────────────
        actual_qty    = engine.get_position(self.ticker).quantity
        pending_buy   = any(p.ticker == self.ticker and p.action == "BUY"
                            for p in engine.pending_orders)
        actual_in_pos = actual_qty > 0 or pending_buy
        if self.in_position and not actual_in_pos:
            self.in_position         = False
            self.stop_armed          = False
            self.highest_since_entry = 0.0

        if len(self.price_history) < self.window + 1:
            self.prev_price = price
            return

        upper, mid, lower, bw = self._bollinger()
        if lower is None:
            self.prev_price = price
            return

        atr    = self._atr(14)
        regime = self._regime_ok(date)

        # ── 추세 필터 ─────────────────────────────────────────────────
        trend_ok = True
        if self.trend_window > 0 and len(self.price_history) >= self.trend_window:
            trend_ma = sum(self.price_history[-self.trend_window:]) / self.trend_window
            trend_ok = price > trend_ma

        # ── 밴드폭 필터 ───────────────────────────────────────────────
        bw_ok = (bw >= self.min_bandwidth) if self.min_bandwidth > 0 else True

        # ── 트레일링 스톱 업데이트 ────────────────────────────────────
        if self.in_position:
            pos = engine.get_position(self.ticker)
            if pos.quantity > 0:
                if price > self.highest_since_entry:
                    self.highest_since_entry = price
                if self.trail_mult > 0 and atr > 0:
                    new_stop     = self.highest_since_entry - self.trail_mult * atr
                    current_stop = engine.get_stop(self.ticker)
                    if current_stop is None or new_stop > current_stop:
                        engine.register_stop(self.ticker, new_stop)
                        self.stop_armed = True

        # ── 진입 조건: 하단 밴드 크로스오버 확인 ────────────────────
        # 직전 종가 < lower 이고, 현재 종가 >= lower → 하단 밴드 위로 복귀 (반등 확인)
        # was_below_lower는 직전 봉이 하단 밴드 아래였음을 기억
        entry_ok = (
            regime
            and trend_ok
            and bw_ok
            and self.was_below_lower   # 직전 봉 하단 밴드 하회
            and price >= lower          # 현재 하단 밴드 위 복귀
        )

        # ── 청산 조건 ─────────────────────────────────────────────────
        ma_break = False
        if self.trend_window > 0 and len(self.price_history) >= self.trend_window:
            trend_ma = sum(self.price_history[-self.trend_window:]) / self.trend_window
            ma_break = price < trend_ma

        mid_exit = self.exit_at_mid and (price >= mid)
        exit_ok  = (price > upper) or mid_exit or ma_break or (not regime)

        if entry_ok and not self.in_position:
            if self.use_vol_sizing and atr > 0 and self.trail_mult > 0:
                stop_distance_pct = (self.trail_mult * atr) / price
                inv_pct = min(self.risk_pct / stop_distance_pct, self.invest_pct)
                inv_pct = max(inv_pct, 0.05)
            else:
                inv_pct = self.invest_pct
            engine.buy_pct(date, self.ticker, inv_pct)
            self.in_position         = True
            self.stop_armed          = False
            self.highest_since_entry = price

        elif exit_ok and self.in_position:
            engine.sell(date, self.ticker)
            self.in_position         = False
            self.stop_armed          = False
            self.highest_since_entry = 0.0
            engine.clear_stop(self.ticker)

        # ── 하단 밴드 상태 업데이트 ──────────────────────────────────
        self.was_below_lower = price < lower
        self.prev_price      = price

    def name(self) -> str:
        parts = [f"볼린저V2({self.window},{self.num_std}σ)"]
        if self.trend_window > 0:
            parts.append(f"추세MA{self.trend_window}")
        if self.trail_mult > 0:
            parts.append(f"트레일ATR{self.trail_mult}x")
        if self.exit_at_mid:
            parts.append("중선청산ON")
        if self._market_ma is not None:
            parts.append("레짐필터ON")
        return " ".join(parts)
