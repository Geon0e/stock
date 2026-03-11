# 📈 KOSPI 200 주식 신호 스캐너

KOSPI 200 전종목 매수/매도 신호 분석 + 백테스팅 + 알림 전송 시스템

---

## 📁 프로젝트 구조

```
stock/
├── config.py               # 전역 설정 (수수료, 세금, 초기자본)
├── scanner.py              # KOSPI 200 전종목 신호 스캔 (메인)
├── backtest.py             # 백테스트 실행
├── cli.py                  # 데이터 수집 CLI
│
├── signals/
│   ├── __init__.py
│   └── indicators.py       # 5가지 기술 지표 신호 계산
│
├── data/
│   ├── fetcher.py          # OHLCV 데이터 + KOSPI 200 목록 수집
│   └── crawler.py          # 네이버 증권 크롤러
│
├── backtest/
│   ├── engine.py           # 백테스팅 엔진 (핵심)
│   ├── visualizer.py       # 차트 시각화
│   └── strategies/
│       ├── base.py         # 전략 기본 클래스
│       ├── moving_average.py
│       ├── rsi.py
│       ├── momentum.py
│       └── bollinger.py
│
├── telegram_bot.py         # 텔레그램 알림 전송
├── kakao_bot.py            # 카카오톡 알림 전송
├── kakao_setup.py          # 카카오 초기 설정 (최초 1회)
│
├── .env                    # 텔레그램/카카오 인증 정보 (git 제외)
├── .env.example            # .env 템플릿
└── tests/
    └── test_engine.py
```

---

## ⚙️ 설치

```bash
git clone https://github.com/Geon0e/stock.git
cd stock
pip install -r requirements.txt
```

---

## 🚀 기능별 사용법

### 1. KOSPI 200 전종목 신호 스캔

5가지 기술 지표(이동평균·RSI·볼린저밴드·MACD·모멘텀)를 앙상블로 결합해 매수/매도 신호를 계산합니다.

```bash
# 기본 실행 (최근 60일 기준, 상위 10개 출력)
python scanner.py

# 옵션
python scanner.py --days 90        # 90일 기준으로 분석
python scanner.py --top 20         # 상위 20개 출력
python scanner.py --no-cache       # 데이터 새로 수집 (캐시 무시)
python scanner.py --out results/scan.csv   # 결과 CSV 저장 경로 지정
```

**출력 예시:**
```
══════════════════════════════════════════════════════════════
         KOSPI 200 매수/매도 신호 스캐너
══════════════════════════════════════════════════════════════
  분석 기간  : 최근 60일
  대상 종목  : KOSPI 200 (200개)

② 매수 추천 상위 10개
  순위  코드      종목명          신호   점수      종가      5일     20일
  ···
   1  012450  한화에어로스페이스  ★매수   82   420,000원  +5.2%  +18.4%
   2  005930  삼성전자           ★매수   74    63,000원  +2.1%   +9.3%
```

---

### 2. 백테스트

```bash
# 단일 전략 (기본: 삼성전자, 이동평균 크로스)
python backtest.py

# 5가지 전략 비교
python backtest.py --mode compare

# 커스텀 전략 예시
python backtest.py --mode custom
```

**결과 지표:** 총수익률 · CAGR · MDD · 샤프비율 · 승률
**차트:** `results/` 폴더에 자동 저장

#### 커스텀 전략 작성

```python
from backtest.strategies.base import BaseStrategy

class MyStrategy(BaseStrategy):
    def initialize(self, engine):
        self.ticker = "005930"
        self.in_position = False

    def on_bar(self, engine, date, data, prices):
        if self.ticker not in prices:
            return
        # 매수
        if not self.in_position:
            engine.buy_pct(date, self.ticker, 1.0)
            self.in_position = True
        # 매도
        # elif ...:
        #     engine.sell(date, self.ticker)

    def name(self):
        return "내전략"
```

---

### 3. 데이터 수집 CLI

```bash
python cli.py info 005930                              # 현재가 조회
python cli.py ohlcv 005930 --start 2024-01-01          # 시세 CSV 저장
python cli.py ohlcv 005930 000660 --start 2024-01-01   # 여러 종목
python cli.py investor 005930 --start 2024-01-01        # 투자자 거래동향
python cli.py index                                     # KOSPI/KOSDAQ 지수
python cli.py search 삼성                               # 종목 검색
```

---

## 🔧 설정 파일

### `config.py` — 전역 설정

```python
DEFAULT_CAPITAL  = 10_000_000   # 초기 자본금 (1천만원)
COMMISSION_RATE  = 0.00015      # 수수료 0.015% (매수/매도 각각)
TAX_RATE         = 0.0018       # 증권거래세 0.18% (매도 시)
SLIPPAGE_RATE    = 0.001        # 슬리피지 0.1%
```

---

## 📊 신호 계산 방식

| 전략 | 가중치 | 매수 조건 | 매도 조건 |
|------|--------|-----------|-----------|
| 이동평균 크로스 | 2.0 | MA5 > MA20 골든크로스 | MA5 < MA20 데드크로스 |
| RSI(14) | 2.0 | RSI < 30 (과매도) | RSI > 70 (과매수) |
| 볼린저밴드 | 1.5 | %B < 0 (하단 이탈) | %B > 1 (상단 이탈) |
| MACD(12,26,9) | 2.0 | MACD 골든크로스 | MACD 데드크로스 |
| 모멘텀(20일) | 1.5 | 20일 수익률 양수 | 음수 |

**최종 신호:** 가중 평균 점수 ≥ 60 → BUY / ≤ 40 → SELL / 그 외 → HOLD

---

## 🧪 테스트

```bash
python -m pytest tests/ -v
# 또는
python tests/test_engine.py
```

---

## 📌 주요 종목코드

| 코드 | 종목명 |
|------|--------|
| 005930 | 삼성전자 |
| 000660 | SK하이닉스 |
| 035420 | NAVER |
| 005380 | 현대차 |
| 069500 | KODEX 200 ETF |
