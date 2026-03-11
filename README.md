# 한국 주식 백테스팅 시스템

Python으로 구현한 한국 주식 거래 데이터 백테스팅 프레임워크입니다.

## 프로젝트 구조

```
stock/
├── backtest.py          # 메인 실행 파일
├── engine.py            # 백테스팅 엔진 (핵심)
├── config.py            # 전역 설정 (수수료, 세금 등)
├── visualizer.py        # 차트 시각화
├── requirements.txt     # 패키지 목록
├── data/
│   ├── fetcher.py       # 주식 데이터 수집 (pykrx / FinanceDataReader)
│   └── *.parquet        # 캐시된 데이터
├── strategies/
│   ├── base.py          # 전략 기본 클래스
│   ├── moving_average.py # 이동평균 크로스 전략
│   ├── rsi.py           # RSI 전략
│   ├── momentum.py      # 모멘텀 전략
│   └── bollinger.py     # 볼린저 밴드 전략
├── results/             # 백테스트 결과 차트 저장
└── tests/
    └── test_engine.py   # 단위 테스트
```

## 설치

```bash
pip install -r requirements.txt
```

## 빠른 시작

### 1. 단일 전략 실행
```bash
python backtest.py --mode single
```

### 2. 전략 비교
```bash
python backtest.py --mode compare
```

### 3. 커스텀 전략 예시
```bash
python backtest.py --mode custom
```

## 설정 변경 (`backtest.py`)

```python
TICKER = "005930"         # 종목코드 (삼성전자)
START_DATE = "2020-01-01"
END_DATE = "2024-12-31"
INITIAL_CAPITAL = 10_000_000  # 초기 자본금 (1천만원)
```

## 주요 종목코드

| 코드   | 종목명         |
|--------|--------------|
| 005930 | 삼성전자      |
| 000660 | SK하이닉스   |
| 035420 | NAVER        |
| 035720 | 카카오        |
| 068270 | 셀트리온      |
| 069500 | KODEX 200    |

## 커스텀 전략 작성

```python
from strategies.base import BaseStrategy

class MyStrategy(BaseStrategy):
    def initialize(self, engine):
        self.ticker = "005930"
        self.in_position = False

    def on_bar(self, engine, date, data, prices):
        if self.ticker not in prices:
            return

        price = prices[self.ticker]

        # 매수 조건
        if not self.in_position:
            engine.buy_pct(date, self.ticker, 1.0)  # 전액 매수
            self.in_position = True

        # 매도 조건
        # elif ...:
        #     engine.sell(date, self.ticker)
        #     self.in_position = False

    def name(self):
        return "내전략"
```

## BacktestEngine API

| 메서드 | 설명 |
|--------|------|
| `engine.buy(date, ticker, quantity)` | 수량 지정 매수 |
| `engine.buy_pct(date, ticker, pct)` | 현금 비율 매수 (pct: 0~1) |
| `engine.sell(date, ticker, quantity)` | 수량 지정 매도 (quantity 생략 시 전량) |
| `engine.get_position(ticker)` | 포지션 조회 |
| `engine.get_cash()` | 현금 잔고 조회 |
| `engine.get_orders()` | 주문 내역 DataFrame |
| `engine.report()` | 성과 지표 출력 |

## 성과 지표

- **총수익률** - 전체 기간 수익률
- **CAGR** - 연환산 수익률
- **MDD** - 최대 낙폭
- **샤프비율** - 위험조정 수익률 (무위험수익률 3.5% 가정)
- **승률** - 수익 거래 비율

## 비용 설정 (`config.py`)

| 항목 | 기본값 | 설명 |
|------|--------|------|
| 수수료 | 0.015% | 매수/매도 각각 |
| 증권거래세 | 0.18% | 매도 시만 |
| 슬리피지 | 0.1% | 체결 오차 |

## 테스트

```bash
python -m pytest tests/ -v
# 또는
python tests/test_engine.py
```
