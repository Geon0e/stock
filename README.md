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

### 2. 텔레그램 알림 전송

스캔 결과를 텔레그램 봇으로 받아볼 수 있습니다.

#### 텔레그램 봇 설정 (최초 1회)

1. 텔레그램에서 **@BotFather** 검색
2. `/newbot` 입력 → 봇 이름 설정 → **토큰** 발급
3. 발급받은 봇과 대화 (메시지 아무거나 전송)
4. 아래 URL에서 `"id"` 값이 **Chat ID**:
   ```
   https://api.telegram.org/bot<토큰>/getUpdates
   ```
5. `.env` 파일에 입력:
   ```
   TELEGRAM_BOT_TOKEN=1234567890:ABCdef...
   TELEGRAM_CHAT_ID=123456789
   ```

#### 사용법

```bash
# 연결 테스트
python telegram_bot.py --test

# 즉시 스캔 후 전송
python telegram_bot.py

# 옵션
python telegram_bot.py --days 90        # 90일 기준
python telegram_bot.py --top 15         # 상위 15개 전송
python telegram_bot.py --no-cache       # 새로 수집 후 전송

# 매일 자동 전송 (스케줄 모드)
python telegram_bot.py --schedule                    # 기본 오전 8시
python telegram_bot.py --schedule --hour 9 --minute 30  # 오전 9시 30분
```

---

### 3. 카카오톡 알림 전송

스캔 결과를 **나에게 보내기**로 카카오톡에서 받아볼 수 있습니다.

#### 카카오 앱 설정 (최초 1회)

1. [developers.kakao.com](https://developers.kakao.com) 접속 → 로그인
2. **내 애플리케이션 → 애플리케이션 추가하기** (이름 자유)
3. **앱 키** 탭 → `REST API 키` 복사
4. **카카오 로그인** → 활성화 **ON**
5. **카카오 로그인 → Redirect URI** → `http://localhost` 추가
6. **동의항목 → 카카오톡 메시지 전송** 체크

#### 초기 설정 실행 (최초 1회)

```bash
python kakao_setup.py
```

실행 흐름:
1. REST API 키 입력
2. (선택) Client Secret 입력 — 개발자 콘솔 **보안** 탭에서 확인
3. 브라우저에서 카카오 로그인
4. 리다이렉트된 URL 복사 후 터미널에 붙여넣기
5. 자동으로 토큰 발급 + `.env` 저장 + 테스트 메시지 전송

> ✅ 설정 완료 시 카카오톡으로 테스트 메시지가 도착합니다.

#### 사용법

```bash
# 연결 테스트
python kakao_bot.py --test

# 즉시 스캔 후 전송
python kakao_bot.py

# 옵션
python kakao_bot.py --days 90           # 90일 기준
python kakao_bot.py --top 15            # 상위 15개 전송
python kakao_bot.py --no-cache          # 새로 수집 후 전송

# 매일 자동 전송 (스케줄 모드)
python kakao_bot.py --schedule                       # 기본 오전 8시
python kakao_bot.py --schedule --hour 9 --minute 30  # 오전 9시 30분
```

**카카오톡 수신 메시지 형태:**

리스트형 메시지로 수신되며, 종목명을 탭하면 네이버 증권 페이지로 연결됩니다.

```
[KOSPI 200 신호 스캐너]
2026-03-11 08:00 | 최근 60일 기준
──────────────────────
분석 종목 : 198개
매수 신호 : 42개 (21%)
매도 신호 : 31개 (16%)
관  망    : 125개 (63%)
평균 점수 : 52.3점
```

---

### 4. 백테스트

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

### 5. 데이터 수집 CLI

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

### `.env` — 알림 봇 인증 정보

`.env.example`을 복사해 `.env`로 만든 뒤 값을 입력하세요:

```bash
cp .env.example .env
```

```
TELEGRAM_BOT_TOKEN=          # @BotFather에서 발급
TELEGRAM_CHAT_ID=            # getUpdates API로 확인

KAKAO_REST_API_KEY=          # 카카오 개발자 콘솔 → REST API 키
KAKAO_CLIENT_SECRET=         # 카카오 개발자 콘솔 → 보안 탭 (없으면 빈칸)
KAKAO_ACCESS_TOKEN=          # kakao_setup.py 실행 시 자동 저장
KAKAO_REFRESH_TOKEN=         # kakao_setup.py 실행 시 자동 저장
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
