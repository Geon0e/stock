"""
백테스팅 전역 설정
"""

# 기본 자본금
DEFAULT_CAPITAL = 10_000_000  # 1천만원

# 거래 수수료 (매수/매도 각각)
COMMISSION_RATE = 0.00015  # 0.015% (증권사 온라인 수수료)

# 증권거래세 (매도 시)
TAX_RATE = 0.0018  # 0.18% (코스피 기준, 2024년)

# 슬리피지 (체결 오차)
SLIPPAGE_RATE = 0.001  # 0.1%

# 기본 데이터 경로
DATA_DIR = "data"

# 결과 저장 경로
RESULTS_DIR = "results"

# 기본 벤치마크
DEFAULT_BENCHMARK = "069500"  # KODEX 200 ETF
