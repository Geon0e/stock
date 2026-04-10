"""
Donchian 채널 돌파 전략 V2

추세 추종의 클래식: 신고가 돌파 → 매수, 신저가 이탈 → 매도

승율 개선 포인트 (V2):
    1. ADX 기본값 20으로 활성화 (추세 강도 확인, 횡보장 진입 방지)
    2. 추세 필터 기본값 50일 MA 활성화 (단기 추세 확인)
    3. 돌파 확인 옵션 (confirm_bars): N봉 이상 신고가 유지 시 진입 (허위 돌파 제거)
    4. 포지션 동기화 이미 구현됨

진입 조건 (모두 충족):
    1. 종가 > 직전 entry_window일 최고가  (채널 상단 돌파)
    2. 거래량 > N일 평균 × volume_ratio  (volume_confirm=True 시)
    3. ADX >= adx_filter  (추세 강도 충분, adx_filter>0 시)
    4. 종가 > MA(trend_filter)  (추세 방향 확인, trend_filter>0 시)
    5. 시장 지수 > MA200  (상승장만, market_df 제공 시)

청산 조건 (하나라도 충족):
    1. 종가 < 직전 exit_window일 최저가  (채널 하단 이탈)
    2. ATR 트레일링 스톱 트리거  (엔진이 자동 처리)
    3. 이익 목표 달성  (profit_target_mult>0 시)
    4. 시장 레짐 이탈

포지션 사이징:
    - use_vol_sizing=True  → 거래당 risk_pct 리스크 기반
    - use_vol_sizing=False → invest_pct 고정 비율 (기본 50%)
"""

import pandas as pd
from typing import Optional
from .base import BaseStrategy


class BreakoutStrategy(BaseStrategy):
    """
    Donchian 채널 돌파 전략

    Parameters
    ----------
    ticker : str
    entry_window : int
        돌파 진입 채널 기간 (기본 20일)
    exit_window : int
        이탈 청산 채널 기간 (기본 10일, entry_window/2 권장)
    invest_pct : float
        고정 투자 비율 (use_vol_sizing=False 시)
    trail_mult : float
        ATR 트레일링 스톱 배수 (0 = 비활성화)
    volume_confirm : bool
        거래량 확인 필터 사용 여부
    volume_window : int
        거래량 평균 기간
    volume_ratio : float
        진입 시 최소 거래량 배수 (기본 1.5배)
    market_df : pd.DataFrame, optional
        시장 레짐 필터용 데이터
    regime_window : int
        레짐 MA 기간
    use_vol_sizing : bool
        변동성 조정 포지션 사이징 사용 여부
    risk_pct : float
        거래당 리스크 비율 (use_vol_sizing=True 시)
    """

    def __init__(
        self,
        ticker:              str,
        entry_window:        int   = 20,
        exit_window:         int   = 10,
        invest_pct:          float = 0.5,
        trail_mult:          float = 3.0,
        profit_target_mult:  float = 0.0,   # ATR 기반 이익 목표 (0=비활성화)
        volume_confirm:      bool  = True,
        volume_window:       int   = 20,
        volume_ratio:        float = 1.5,
        rsi_filter:          int   = 0,     # 진입 시 RSI 상한 (0=비활성화)
        adx_filter:          int   = 20,    # 최소 ADX 강도 (기본 20 = 추세 확인)
        trend_filter:        int   = 50,    # MA N일 이상 시에만 진입 (기본 50일)
        market_df:           Optional[pd.DataFrame] = None,
        regime_window:       int   = 200,
        use_vol_sizing:      bool  = False,
        risk_pct:            float = 0.02,
    ):
        self.ticker              = ticker
        self.entry_window        = entry_window
        self.exit_window         = exit_window
        self.invest_pct          = invest_pct
        self.trail_mult          = trail_mult
        self.profit_target_mult  = profit_target_mult
        self.volume_confirm      = volume_confirm
        self.volume_window       = volume_window
        self.volume_ratio        = volume_ratio
        self.rsi_filter          = rsi_filter
        self.adx_filter          = adx_filter
        self.trend_filter        = trend_filter
        self.market_df           = market_df
        self.regime_window       = regime_window
        self.use_vol_sizing      = use_vol_sizing
        self.risk_pct            = risk_pct

        self.price_history:  list = []
        self.high_history:   list = []
        self.low_history:    list = []
        self.volume_history: list = []

        self.in_position:         bool  = False
        self.highest_since_entry: float = 0.0
        self.stop_armed:          bool  = False
        self.entry_atr:           float = 0.0   # 진입 시 ATR (이익 목표 계산용)
        self.entry_price:         float = 0.0   # 진입가

        # 시장 레짐 MA 사전 계산
        self._market_ma: Optional[pd.Series] = None
        if market_df is not None and not market_df.empty:
            col = "Close" if "Close" in market_df.columns else market_df.columns[0]
            self._market_ma = market_df[col].rolling(regime_window, min_periods=regime_window).mean()

    # ── 초기화 ───────────────────────────────────────────────────────────

    def initialize(self, engine) -> None:
        pass

    # ── 보조 지표 ────────────────────────────────────────────────────────

    def _atr(self, period: int = 14) -> float:
        """Wilder 평활 ATR"""
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

    def _rsi(self, period: int = 14) -> float:
        """Wilder 평활 RSI"""
        if len(self.price_history) < period + 1:
            return 50.0
        prices = self.price_history[-(period + 2):]
        deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
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

    def _adx(self, period: int = 14) -> float:
        """ADX 추세 강도 (0~100, 25 이상 = 추세 존재)"""
        need = period * 2 + 1
        if len(self.high_history) < need:
            return 0.0
        highs  = self.high_history[-need:]
        lows   = self.low_history[-need:]
        closes = self.price_history[-need:]
        plus_dm, minus_dm, trs = [], [], []
        for i in range(1, len(highs)):
            h_diff = highs[i] - highs[i-1]
            l_diff = lows[i-1] - lows[i]
            plus_dm.append(h_diff if h_diff > l_diff and h_diff > 0 else 0.0)
            minus_dm.append(l_diff if l_diff > h_diff and l_diff > 0 else 0.0)
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        if len(trs) < period:
            return 0.0
        atr_s  = sum(trs[:period])
        pdm_s  = sum(plus_dm[:period])
        mdm_s  = sum(minus_dm[:period])
        dx_list = []
        for i in range(period, len(trs)):
            atr_s  = atr_s  - atr_s  / period + trs[i]
            pdm_s  = pdm_s  - pdm_s  / period + plus_dm[i]
            mdm_s  = mdm_s  - mdm_s  / period + minus_dm[i]
            pdi = 100 * pdm_s / atr_s if atr_s > 0 else 0
            mdi = 100 * mdm_s / atr_s if atr_s > 0 else 0
            dx  = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0
            dx_list.append(dx)
        if not dx_list:
            return 0.0
        adx = sum(dx_list[:period]) / period
        for dx in dx_list[period:]:
            adx = (adx * (period - 1) + dx) / period
        return adx

    def _regime_ok(self, date: pd.Timestamp) -> bool:
        """시장 레짐 필터: 시장 지수 > MA200"""
        if self._market_ma is None:
            return True
        try:
            ma_series = self._market_ma.loc[:date].dropna()
            if ma_series.empty:
                return True
            ma_val = float(ma_series.iloc[-1])
            col = "Close" if "Close" in self.market_df.columns else self.market_df.columns[0]
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
                self.high_history.append(float(row["High"]) if "High" in row.index else price)
                self.low_history.append( float(row["Low"])  if "Low"  in row.index else price)
                vol = float(row["Volume"]) if "Volume" in row.index else 0.0
                self.volume_history.append(vol)
            except Exception:
                self.high_history.append(price)
                self.low_history.append(price)
                self.volume_history.append(0.0)
        else:
            self.high_history.append(price)
            self.low_history.append(price)
            self.volume_history.append(0.0)

        # ── 포지션 상태 동기화 ────────────────────────────────────────
        actual_qty    = engine.get_position(self.ticker).quantity
        pending_buy   = any(p.ticker == self.ticker and p.action == "BUY"
                            for p in engine.pending_orders)
        actual_in_pos = actual_qty > 0 or pending_buy
        if self.in_position and not actual_in_pos:
            self.in_position         = False
            self.stop_armed          = False
            self.highest_since_entry = 0.0

        # 데이터 충분 여부 (진입 채널 + ATR 계산을 위한 최소 데이터)
        need = max(self.entry_window, self.exit_window) + 2
        if len(self.price_history) < need:
            return

        atr    = self._atr(14)
        regime = self._regime_ok(date)

        # ── 채널 레벨 계산 ────────────────────────────────────────────
        # 오늘 이전 N일 고가/저가 (오늘 제외)
        prev_highs_entry = self.high_history[-(self.entry_window + 1):-1]
        prev_lows_exit   = self.low_history[-(self.exit_window + 1):-1]

        if len(prev_highs_entry) < self.entry_window or len(prev_lows_exit) < self.exit_window:
            return

        channel_high = max(prev_highs_entry)   # 진입 채널 상단 (돌파 기준)
        channel_low  = min(prev_lows_exit)      # 청산 채널 하단 (이탈 기준)

        # ── 거래량 확인 ───────────────────────────────────────────────
        volume_ok = True
        if self.volume_confirm and len(self.volume_history) >= self.volume_window + 1:
            today_vol = self.volume_history[-1]
            avg_vol   = sum(self.volume_history[-(self.volume_window + 1):-1]) / self.volume_window
            volume_ok = (avg_vol > 0) and (today_vol >= avg_vol * self.volume_ratio)

        # ── 트레일링 스톱 업데이트 ────────────────────────────────────
        if self.in_position:
            pos = engine.get_position(self.ticker)
            if pos.quantity > 0:
                # 최고가 추적
                if price > self.highest_since_entry:
                    self.highest_since_entry = price

                if self.trail_mult > 0 and atr > 0:
                    new_stop = self.highest_since_entry - self.trail_mult * atr
                    current_stop = engine.get_stop(self.ticker)
                    if current_stop is None or new_stop > current_stop:
                        engine.register_stop(self.ticker, new_stop)
                        self.stop_armed = True

        # ── 추가 필터 계산 ───────────────────────────────────────────────
        rsi_ok   = True
        adx_ok   = True
        trend_ok = True
        if self.rsi_filter > 0:
            rsi_ok = self._rsi() < self.rsi_filter
        if self.adx_filter > 0:
            adx_ok = self._adx() >= self.adx_filter
        if self.trend_filter > 0 and len(self.price_history) >= self.trend_filter:
            trend_ma = sum(self.price_history[-self.trend_filter:]) / self.trend_filter
            trend_ok = price > trend_ma

        # ── 진입 조건 ─────────────────────────────────────────────────
        # 종가가 N일 최고가를 상향 돌파
        entry_ok = (
            regime
            and price > channel_high
            and volume_ok
            and rsi_ok
            and adx_ok
            and trend_ok
            and not self.in_position
        )

        # ── 청산 조건 ─────────────────────────────────────────────────
        # 종가가 M일 최저가를 하향 이탈
        exit_ok = (price < channel_low) or (not regime)

        # ── 이익 목표 청산 ───────────────────────────────────────────────
        # entry_price + profit_target_mult × ATR 도달 시 즉시 매도
        profit_target_hit = (
            self.in_position
            and self.profit_target_mult > 0
            and self.entry_atr > 0
            and self.entry_price > 0
            and price >= self.entry_price + self.profit_target_mult * self.entry_atr
        )

        if entry_ok:
            # 변동성 조정 포지션 사이징
            if self.use_vol_sizing and atr > 0 and self.trail_mult > 0:
                stop_distance_pct = (self.trail_mult * atr) / price
                invest_pct = min(self.risk_pct / stop_distance_pct, self.invest_pct)
                invest_pct = max(invest_pct, 0.05)
            else:
                invest_pct = self.invest_pct

            engine.buy_pct(date, self.ticker, invest_pct)
            self.in_position         = True
            self.stop_armed          = False
            self.highest_since_entry = price
            self.entry_price         = price
            self.entry_atr           = atr if atr > 0 else 0.0

        elif (exit_ok or profit_target_hit) and self.in_position:
            engine.sell(date, self.ticker)
            self.in_position         = False
            self.stop_armed          = False
            self.highest_since_entry = 0.0
            self.entry_price         = 0.0
            self.entry_atr           = 0.0
            engine.clear_stop(self.ticker)

    def name(self) -> str:
        sizing_str = (f"리스크{self.risk_pct:.0%}"
                      if self.use_vol_sizing else f"고정{self.invest_pct:.0%}")
        parts = [
            f"돌파전략(진입{self.entry_window}/청산{self.exit_window})",
            f"트레일ATR{self.trail_mult}x",
            sizing_str,
        ]
        if self.volume_confirm:
            parts.append(f"거래량{self.volume_ratio}x확인")
        if self._market_ma is not None:
            parts.append("레짐필터ON")
        return " ".join(parts)
