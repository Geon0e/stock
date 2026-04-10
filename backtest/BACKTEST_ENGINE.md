# 백테스팅 엔진 레퍼런스

## 개요

한국/미국 주식 대상 기관급 백테스팅 엔진. T+1 체결, 거래량 기반 슬리피지, ATR 트레일링 스톱을 기본 탑재.

---

## 빠른 시작

```python
from backtest.engine import BacktestEngine
from backtest.strategies import MovingAverageCrossV2Strategy

# 데이터 준비 (pandas DataFrame, 컬럼: Open/High/Low/Close/Volume, index: DatetimeIndex)
engine = BacktestEngine(
    data={"005930": df},
    initial_capital=10_000_000,
)

strategy = MovingAverageCrossV2Strategy("005930")
engine.run(strategy)
metrics = engine.report()
```

---

## BacktestEngine

```python
BacktestEngine(
    data: dict[str, pd.DataFrame],  # {ticker: OHLCV df}
    initial_capital: float = 10_000_000,
    commission_rate: float = 0.00015,   # 0.015%
    tax_rate:        float = 0.0018,    # 0.18% (코스피)
    slippage_rate:   float = 0.0002,    # 0.02% base
)
```

### 체결 방식

- **T+1 Open**: 신호는 `Close[t]`에서 발생, 체결은 `Open[t+1]`에서 실행 → look-ahead bias 없음
- **슬리피지**: `base(0.02%) + market_impact(주문금액/일거래대금 × 0.1, 최대 0.5%)`
- **ATR 손절**: `register_stop()` 등록 시 `Low[t] ≤ stop` 조건으로 즉시 체결

### 주문 메서드

| 메서드 | 설명 |
|-------|------|
| `buy_pct(date, ticker, pct)` | 가용 현금의 `pct` 비율만큼 매수 |
| `buy(date, ticker, quantity)` | 수량 지정 매수 |
| `sell(date, ticker)` | 전량 매도 (T+1) |
| `register_stop(ticker, price)` | ATR 손절가 등록 |
| `clear_stop(ticker)` | 손절 해제 |
| `get_stop(ticker)` | 현재 손절가 조회 |

### 조회 메서드

| 메서드 | 반환 |
|-------|------|
| `get_position(ticker)` | `Position(quantity, avg_price, entry_date)` |
| `get_cash()` | 현재 보유 현금 |
| `get_orders()` | 체결 내역 DataFrame |
| `report()` | 성과지표 dict |

---

## 성과지표 (report() 반환값)

| 키 | 설명 |
|----|------|
| `총수익률(%)` | 전체 기간 수익률 |
| `연환산수익률(CAGR,%)` | 연복리 수익률 |
| `최대낙폭(MDD,%)` | 고점 대비 최대 하락률 |
| `샤프비율` | 초과수익 / 변동성 (무위험률 3.5%) |
| `소르티노비율` | 초과수익 / 하방변동성 |
| `칼마비율` | CAGR / \|MDD\| |
| `오메가비율` | 가중수익합 / 가중손실합 (>1 = 양호) |
| `회복계수` | 총수익률 / \|MDD\| |
| `Profit Factor` | 총이익 / 총손실 (>1.2 = 목표) |
| `Expectancy(%)` | 거래당 기대 수익률 |
| `R배수(평균승/패)` | 평균수익 / 평균손실 |
| `총거래횟수` | 매수+매도 주문 합계 |
| `승률(%)` | 수익 거래 비율 |
| `최대연속손실` | 연속 손실 최대 횟수 |
| `평균보유일` | 거래당 평균 보유 기간 |

---

## 전략 목록

### 1. MovingAverageCrossV2Strategy (권장)

```python
MovingAverageCrossV2Strategy(
    ticker:         str,
    short_window:   int   = 5,      # 단기 MA
    long_window:    int   = 20,     # 장기 MA
    trend_window:   int   = 60,     # 추세 MA
    rsi_period:     int   = 14,
    rsi_entry_max:  float = 65.0,   # 진입 시 RSI 상한
    invest_pct:     float = 0.5,    # 고정 투자 비율
    market_df:      pd.DataFrame = None,  # 레짐 필터용 지수 데이터
    trail_mult:     float = 3.0,    # 트레일링 스톱 ATR 배수 (0=고정손절)
    atr_stop_mult:  float = 2.0,    # 고정 손절 ATR 배수 (trail_mult=0 시)
    regime_window:  int   = 200,    # 레짐 MA 기간
    use_vol_sizing: bool  = False,  # 변동성 조정 사이징 사용
    risk_pct:       float = 0.02,   # 거래당 리스크 비율 (use_vol_sizing=True 시)
)
```

**진입** (모두 충족):
- MA5 > MA20 (골든크로스)
- 종가 > MA60 (중기 추세 확인)
- RSI(14) < 65 (과매수 제외)
- 시장지수 > MA200 (market_df 제공 시)

**청산** (하나라도 충족):
- MA5 < MA20 (데드크로스)
- 종가 < MA60 (추세 이탈)
- 시장지수 < MA200
- 트레일링 스톱 트리거

**트레일링 스톱**: `최고가 - trail_mult × ATR(14)`, 매 bar마다 상향 조정

**변동성 사이징**: `투자비율 = risk_pct ÷ (stop_distance / price)`, 최대 invest_pct

---

### 2. BreakoutStrategy (돌파 전략)

```python
BreakoutStrategy(
    ticker:         str,
    entry_window:   int   = 20,     # N일 최고가 돌파 진입
    exit_window:    int   = 10,     # M일 최저가 이탈 청산
    invest_pct:     float = 0.5,
    trail_mult:     float = 3.0,    # 트레일링 스톱 ATR 배수
    volume_confirm: bool  = True,   # 거래량 확인 (20일 평균 × 1.5배 이상)
    volume_window:  int   = 20,
    volume_ratio:   float = 1.5,
    market_df:      pd.DataFrame = None,
    regime_window:  int   = 200,
    use_vol_sizing: bool  = False,
    risk_pct:       float = 0.02,
)
```

**진입**: 종가 > 직전 20일 최고가 + 거래량 확인 + 레짐 OK

**청산**: 종가 < 직전 10일 최저가 OR 레짐 이탈 OR 트레일링 스톱

---

### 3. RSIStrategy

```python
RSIStrategy(
    ticker:     str,
    period:     int   = 14,
    oversold:   float = 30.0,   # 이하 시 매수
    overbought: float = 70.0,   # 이상 시 매도
    invest_pct: float = 1.0,
)
```

---

### 4. BollingerBandStrategy

```python
BollingerBandStrategy(
    ticker:     str,
    window:     int   = 20,
    num_std:    float = 2.0,
    invest_pct: float = 1.0,
)
```

진입: `종가 < MA - 2σ` / 청산: `종가 > MA + 2σ`

---

### 5. MomentumStrategy

```python
MomentumStrategy(
    ticker:         str,
    lookback:       int   = 120,   # 모멘텀 측정 기간
    invest_pct:     float = 1.0,
    rebalance_freq: int   = 20,    # 리밸런싱 주기 (거래일)
)
```

N일 수익률 > 0 → 보유 / ≤ 0 → 현금 전환

---

### 6. MovingAverageCrossStrategy (기본형)

```python
MovingAverageCrossStrategy(
    ticker:       str,
    short_window: int   = 20,
    long_window:  int   = 60,
    invest_pct:   float = 1.0,
)
```

단순 골든/데드크로스. 레짐 필터 없음.

---

## 커스텀 전략 작성

```python
from backtest.strategies.base import BaseStrategy

class MyStrategy(BaseStrategy):
    def initialize(self, engine) -> None:
        # 백테스트 시작 전 1회 실행
        pass

    def on_bar(self, engine, date, data, prices) -> None:
        # data:   {ticker: OHLCV Series (당일)}
        # prices: {ticker: 종가}
        pos = engine.get_position("005930")
        if pos.quantity == 0:
            engine.buy_pct(date, "005930", 0.5)
            engine.register_stop("005930", prices["005930"] * 0.95)

    def name(self) -> str:
        return "내전략"
```

---

## Walk-Forward Analysis

```python
from backtest.comparison import walk_forward_test

result = walk_forward_test(
    ohlcv=df,
    ticker="005930",
    capital=10_000_000,
    train_years=2,   # 워밍업 기간
    test_years=1,    # OOS 테스트 기간
    market_df=None,  # 레짐 필터 (선택)
)

# result["periods"]    → 기간별 OOS 성과 list[dict]
# result["oos_equity"] → 이어붙인 OOS 자산곡선 pd.Series
# result["summary"]    → 집계 통계 dict
```

---

## 로버스트니스 체크 (7개 항목)

```python
from backtest.comparison import robustness_check, calc_buyhold

bh = calc_buyhold(df, ticker, capital)
checks = robustness_check(v2_metrics, bh, multi_df, slip_df, base_slippage)
```

| 항목 | 기준 |
|------|------|
| CAGR > B&H + 2%p | 벤치마크 초과 성과 |
| MDD ≤ B&H MDD × 1.1 | 낙폭 통제 |
| 연간 거래 ≤ 24회 | 과도한 매매 비용 방지 |
| 슬리피지 2배 후 CAGR > 0 | 비용 내성 |
| Profit Factor > 1.2 | 총이익/총손실 |
| 회복계수 > 1.0 | 손실 복구력 |
| 오메가비율 > 1.0 | 수익/손실 기대값 비교 |

---

## 거래 비용 설정

`config.py`:

```python
DEFAULT_CAPITAL    = 10_000_000   # 1천만원
COMMISSION_RATE    = 0.00015      # 0.015% (온라인 증권사)
TAX_RATE           = 0.0018       # 0.18% (코스피 매도세)
SLIPPAGE_BASE      = 0.0002       # 0.02% base slippage
ATR_STOP_MULT      = 2.0
MAX_POSITION_PCT   = 0.5
```

---

## 주의사항

- 데이터 컬럼명은 `Open`, `High`, `Low`, `Close`, `Volume` (대소문자 정확히)
- `Close`만 있는 데이터도 동작하나 ATR 스톱 정확도 저하
- Walk-Forward 최소 데이터: `(train_years + test_years) × 252` 거래일
- 한국 주식 세금(`tax_rate`)은 매도 시에만 부과됨
