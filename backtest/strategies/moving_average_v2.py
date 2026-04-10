"""
MA 크로스 V2 전략 (기관급 설계)

승율 개선 포인트 (V2.1):
    1. RSI 하한선 추가 (rsi_entry_min): RSI < 40이면 진입 회피 (약한 모멘텀 필터)
    2. 거래량 확인 옵션 (volume_confirm): 평균 거래량 이상에서만 진입
    3. 강세 봉 확인 (bull_bar): 종가 > 시가 (상승 봉)에서만 진입

진입 조건 (모두 충족):
    1. MA5  > MA20          (단기 골든크로스)
    2. 종가 > MA60          (중기 추세 확인)
    3. rsi_entry_min < RSI(14) < rsi_entry_max  (과매수·약세 동시 제외)
    4. 시장 지수 > MA200    (상승장만 진입, market_df 제공 시)
    5. 거래량 > N일 평균    (volume_confirm=True 시)
    6. 종가 > 시가          (bull_bar=True 시, 강세 봉 확인)

청산 조건 (하나라도 충족):
    1. MA5 < MA20           (데드크로스)
    2. 종가 < MA60          (추세 이탈)
    3. 시장 지수 < MA200    (레짐 이탈, market_df 제공 시)
    4. ATR 트레일링 스톱 트리거 (엔진이 자동 처리)

포지션 사이징:
    - use_vol_sizing=True  → 리스크 기반 사이징 (risk_pct % 리스크)
    - use_vol_sizing=False → invest_pct 고정 비율 (기본 50%)

트레일링 스톱:
    - trail_mult > 0 이면 진입 후 매 bar마다 스톱가격 상향 조정
    - new_stop = 진입 후 최고가 - trail_mult × ATR(14)
    - 스톱은 올릴 수만 있음 (하향 금지)
    - trail_mult=0 이면 진입가 기준 고정 손절 (atr_stop_mult 사용)
"""

import pandas as pd
from typing import Optional
from .base import BaseStrategy


class MovingAverageCrossV2Strategy(BaseStrategy):

    def __init__(
        self,
        ticker:          str,
        short_window:    int   = 5,
        long_window:     int   = 20,
        trend_window:    int   = 60,
        rsi_period:      int   = 14,
        rsi_entry_max:   float = 65.0,
        rsi_entry_min:   float = 0.0,     # RSI 하한선 (0=비활성화, 권장 40)
        invest_pct:      float = 0.5,     # 고정 포지션 비율 (use_vol_sizing=False 시)
        market_df:       Optional[pd.DataFrame] = None,   # 시장 레짐 필터
        atr_stop_mult:   float = 2.0,     # 초기 ATR 손절 배수 (trail_mult=0 일 때 사용)
        trail_mult:      float = 3.0,     # 트레일링 스톱 ATR 배수 (0 = 고정 손절 사용)
        regime_window:   int   = 200,     # 레짐 MA 기간
        use_vol_sizing:  bool  = False,   # 변동성 조정 포지션 사이징
        risk_pct:        float = 0.02,    # 거래당 리스크 비율 (use_vol_sizing=True 시)
        volume_confirm:  bool  = False,   # 거래량 필터 (평균 이상에서만 진입)
        volume_window:   int   = 20,      # 거래량 평균 기간
        bull_bar:        bool  = False,   # 강세 봉 확인 (종가 > 시가)
    ):
        self.ticker          = ticker
        self.short_window    = short_window
        self.long_window     = long_window
        self.trend_window    = trend_window
        self.rsi_period      = rsi_period
        self.rsi_entry_max   = rsi_entry_max
        self.rsi_entry_min   = rsi_entry_min
        self.invest_pct      = invest_pct
        self.market_df       = market_df
        self.atr_stop_mult   = atr_stop_mult
        self.trail_mult      = trail_mult
        self.regime_window   = regime_window
        self.use_vol_sizing  = use_vol_sizing
        self.risk_pct        = risk_pct
        self.volume_confirm  = volume_confirm
        self.volume_window   = volume_window
        self.bull_bar        = bull_bar

        self.price_history:  list = []
        self.high_history:   list = []
        self.low_history:    list = []
        self.open_history:   list = []   # 시가 이력 (bull_bar 확인용)
        self.volume_history: list = []   # 거래량 이력

        self.in_position:         bool  = False
        self.stop_armed:          bool  = False   # 초기 손절 등록 여부
        self.highest_since_entry: float = 0.0     # 진입 후 최고가 (트레일링 스톱용)

        # 시장 레짐 MA 사전 계산
        self._market_ma: Optional[pd.Series] = None
        if market_df is not None and not market_df.empty:
            col = "Close" if "Close" in market_df.columns else market_df.columns[0]
            self._market_ma = market_df[col].rolling(regime_window, min_periods=regime_window).mean()

    # ── 초기화 ───────────────────────────────────────────────────────────

    def initialize(self, engine) -> None:
        pass

    # ── 보조 지표 ────────────────────────────────────────────────────────

    def _rsi(self) -> float:
        """Wilder 평활 RSI"""
        period = self.rsi_period
        prices = self.price_history
        if len(prices) < period + 1:
            return 50.0
        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        gains  = [max(d, 0.0) for d in deltas]
        losses = [max(-d, 0.0) for d in deltas]
        avg_g  = sum(gains[:period]) / period
        avg_l  = sum(losses[:period]) / period
        for g, l in zip(gains[period:], losses[period:]):
            avg_g = (avg_g * (period - 1) + g) / period
            avg_l = (avg_l * (period - 1) + l) / period
        if avg_l == 0:
            return 100.0
        return 100.0 - (100.0 / (1.0 + avg_g / avg_l))

    def _atr(self, period: int = 14) -> float:
        """Wilder 평활 ATR (True Range 기반)"""
        need = period + 1
        if len(self.high_history) < need or len(self.price_history) < need:
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
        """시장 레짐 필터: 시장 지수 > MA200"""
        if self._market_ma is None:
            return True
        try:
            ma_series = self._market_ma.loc[:date].dropna()
            if ma_series.empty:
                return True
            ma_val = float(ma_series.iloc[-1])
            col    = "Close" if "Close" in self.market_df.columns else self.market_df.columns[0]
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

        # OHLCV 이력 업데이트
        self.price_history.append(price)
        if row is not None:
            try:
                self.high_history.append(float(row["High"])   if "High"   in row.index else price)
                self.low_history.append( float(row["Low"])    if "Low"    in row.index else price)
                self.open_history.append(float(row["Open"])   if "Open"   in row.index else price)
                self.volume_history.append(float(row["Volume"]) if "Volume" in row.index else 0.0)
            except Exception:
                self.high_history.append(price)
                self.low_history.append(price)
                self.open_history.append(price)
                self.volume_history.append(0.0)
        else:
            self.high_history.append(price)
            self.low_history.append(price)
            self.open_history.append(price)
            self.volume_history.append(0.0)

        # ── 포지션 상태 동기화 (손절 등 외부 청산 반영) ───────────────
        actual_qty     = engine.get_position(self.ticker).quantity
        pending_buy    = any(p.ticker == self.ticker and p.action == "BUY"
                             for p in engine.pending_orders)
        actual_in_pos  = actual_qty > 0 or pending_buy
        if self.in_position and not actual_in_pos:
            self.in_position         = False
            self.stop_armed          = False
            self.highest_since_entry = 0.0

        # 데이터 부족
        if len(self.price_history) < self.trend_window:
            return

        # ── 지표 계산 ─────────────────────────────────────────────────
        short_ma = sum(self.price_history[-self.short_window:]) / self.short_window
        long_ma  = sum(self.price_history[-self.long_window:])  / self.long_window
        trend_ma = sum(self.price_history[-self.trend_window:]) / self.trend_window
        rsi      = self._rsi()
        atr      = self._atr(14)
        regime   = self._regime_ok(date)

        # ── 트레일링 스톱 / 초기 손절 관리 ──────────────────────────
        if self.in_position:
            pos = engine.get_position(self.ticker)
            if pos.quantity > 0:
                # 진입 후 최고가 추적
                if price > self.highest_since_entry:
                    self.highest_since_entry = price

                if self.trail_mult > 0 and atr > 0:
                    # 트레일링 스톱: 최고가 기준으로 매 bar 업데이트
                    new_stop = self.highest_since_entry - self.trail_mult * atr
                    current_stop = engine.get_stop(self.ticker)
                    # 스톱은 올릴 수만 있음 (하향 금지)
                    if current_stop is None or new_stop > current_stop:
                        engine.register_stop(self.ticker, new_stop)
                        if not self.stop_armed:
                            self.stop_armed = True

                elif not self.stop_armed and self.atr_stop_mult > 0 and atr > 0:
                    # 고정 ATR 손절 (trail_mult=0 일 때)
                    stop = pos.avg_price - self.atr_stop_mult * atr
                    engine.register_stop(self.ticker, stop)
                    self.stop_armed = True

        # ── 거래량 필터 ───────────────────────────────────────────────
        volume_ok = True
        if self.volume_confirm and len(self.volume_history) > self.volume_window:
            today_vol = self.volume_history[-1]
            avg_vol   = sum(self.volume_history[-(self.volume_window + 1):-1]) / self.volume_window
            volume_ok = (avg_vol > 0) and (today_vol >= avg_vol)

        # ── 강세 봉 필터 ──────────────────────────────────────────────
        bull_ok = True
        if self.bull_bar and self.open_history:
            bull_ok = price > self.open_history[-1]   # 종가 > 시가

        # ── 진입 조건 ─────────────────────────────────────────────────
        entry_ok = (
            regime
            and short_ma > long_ma      # 골든크로스
            and price    > trend_ma     # MA60 위
            and rsi      < self.rsi_entry_max
            and (self.rsi_entry_min <= 0 or rsi > self.rsi_entry_min)  # RSI 하한선
            and volume_ok
            and bull_ok
        )

        # ── 청산 조건 ─────────────────────────────────────────────────
        exit_ok = (short_ma < long_ma) or (price < trend_ma) or (not regime)

        if entry_ok and not self.in_position:
            # 변동성 조정 포지션 사이징
            if self.use_vol_sizing and atr > 0:
                stop_mult = self.trail_mult if self.trail_mult > 0 else self.atr_stop_mult
                if stop_mult > 0:
                    stop_distance_pct = (stop_mult * atr) / price
                    invest_pct = min(self.risk_pct / stop_distance_pct, self.invest_pct)
                    invest_pct = max(invest_pct, 0.05)   # 최소 5%
                else:
                    invest_pct = self.invest_pct
            else:
                invest_pct = self.invest_pct

            engine.buy_pct(date, self.ticker, invest_pct)
            self.in_position         = True
            self.stop_armed          = False
            self.highest_since_entry = price

        elif exit_ok and self.in_position:
            engine.sell(date, self.ticker)
            self.in_position         = False
            self.stop_armed          = False
            self.highest_since_entry = 0.0
            engine.clear_stop(self.ticker)

    def name(self) -> str:
        stop_str = (f"트레일ATR{self.trail_mult}x"
                    if self.trail_mult > 0 else f"고정ATR{self.atr_stop_mult}x")
        sizing_str = (f"리스크{self.risk_pct:.0%}"
                      if self.use_vol_sizing else f"고정{self.invest_pct:.0%}")
        rsi_str = (f"RSI{self.rsi_entry_min:.0f}~{self.rsi_entry_max:.0f}"
                   if self.rsi_entry_min > 0 else f"RSI<{self.rsi_entry_max:.0f}")
        parts = [f"MA크로스V2({self.short_window}/{self.long_window}/추세{self.trend_window})",
                 rsi_str,
                 stop_str,
                 sizing_str]
        if self.volume_confirm:
            parts.append("거래량확인ON")
        if self.bull_bar:
            parts.append("강세봉확인ON")
        if self._market_ma is not None:
            parts.append("레짐필터ON")
        return " ".join(parts)
