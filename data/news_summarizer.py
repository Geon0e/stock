"""
매수 추천 종목 뉴스 요약 (Claude API 사용)

.env에 ANTHROPIC_API_KEY 필요:
    ANTHROPIC_API_KEY=sk-ant-...
"""

import os
from pathlib import Path


def _load_env():
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()


def summarize_stocks_news(stocks_news: list, market: str = "kospi200") -> str:
    """
    여러 종목의 뉴스 헤드라인을 Claude Haiku로 요약

    Args:
        stocks_news: [{"ticker": str, "name": str, "articles": [{"title": str, ...}]}]
        market: "kospi200" | "nasdaq100"

    Returns:
        카카오톡 전송용 요약 텍스트. API 키 없거나 실패 시 빈 문자열 반환.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[뉴스요약] ANTHROPIC_API_KEY 미설정 — 뉴스 요약 생략")
        return ""

    try:
        import anthropic
    except ImportError:
        print("[뉴스요약] anthropic 패키지 미설치 — pip install anthropic")
        return ""

    # 기사 없는 종목 제외
    valid = [s for s in stocks_news if s.get("articles")]
    if not valid:
        return ""

    # 프롬프트 구성
    news_block = ""
    for s in valid:
        news_block += f"\n[{s['name']} ({s['ticker']})]\n"
        for i, a in enumerate(s["articles"], 1):
            news_block += f"  {i}. {a['title']}\n"

    lang_hint = "한국어" if market == "kospi200" else "한국어"
    prompt = (
        f"다음은 오늘 주식 매수 추천 종목들의 최신 뉴스 헤드라인입니다.\n"
        f"각 종목별로 {lang_hint}로 2~3줄씩 핵심 내용을 요약해 주세요.\n"
        f"투자자 관점에서 긍정/부정 요인을 간결하게 포함해 주세요.\n"
        f"종목명과 티커는 그대로 사용하고, 불필요한 서론 없이 바로 요약하세요.\n"
        f"\n{news_block}"
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # cp949 환경에서 전송 오류 방지: 특수 유니코드 문자 치환
        for src, dst in [('\u2014', '-'), ('\u2013', '-'), ('\u2019', "'"),
                         ('\u201c', '"'), ('\u201d', '"'), ('\u2022', '·')]:
            text = text.replace(src, dst)
        return text
    except Exception as e:
        print(f"[뉴스요약] Claude API 호출 실패: {e}")
        return ""


def summarize_market_context(news_data: dict, market: str = "kospi200") -> dict:
    """
    네이버 경제/세계 뉴스를 분석해 시장 센티멘트와 백테스팅 맥락 반환.

    Args:
        news_data: {"economy": [...], "world": [...]}  fetch_market_news() 결과
        market: "kospi200" | "nasdaq100"

    Returns:
        {
            "sentiment":     float,   # -1.0(매우 부정) ~ +1.0(매우 긍정)
            "label":         str,     # "강세" | "중립" | "약세"
            "summary":       str,     # 2~3줄 시장 요약
            "risks":         list,    # 주요 리스크 테마 (최대 3개)
            "opportunities": list,    # 주요 기회 테마 (최대 3개)
            "strategy_fit":  str,     # 현재 환경에서의 추세추종 전략 적합성
            "raw_articles":  int,     # 분석한 기사 수
        }
    """
    _load_env()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    economy_articles = news_data.get("economy", [])
    world_articles   = news_data.get("world", [])
    all_articles     = economy_articles + world_articles

    if not all_articles:
        return _empty_context()

    if not api_key:
        return _empty_context()

    try:
        import anthropic
    except ImportError:
        return _empty_context()

    # 기사 블록 구성
    eco_block = "\n".join(f"  - {a['title']}" for a in economy_articles[:10])
    world_block = "\n".join(f"  - {a['title']}" for a in world_articles[:10])

    market_label = "KOSPI 200 (한국 주식시장)" if market == "kospi200" else "NASDAQ 100 (미국 기술주)"

    prompt = f"""다음은 오늘의 네이버 뉴스 헤드라인입니다.

[경제 섹션]
{eco_block}

[세계 섹션]
{world_block}

위 뉴스를 바탕으로 {market_label} 투자 관점에서 분석해 주세요.
반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트 없이 JSON만 출력하세요.

{{
  "sentiment": <-1.0에서 1.0 사이의 숫자, 부정적일수록 낮음>,
  "label": "<강세|중립|약세>",
  "summary": "<현재 시장 상황을 2~3문장으로 요약>",
  "risks": ["<리스크1>", "<리스크2>", "<리스크3 (없으면 생략)>"],
  "opportunities": ["<기회1>", "<기회2>", "<기회3 (없으면 생략)>"],
  "strategy_fit": "<추세추종(이동평균 크로스) 전략이 현재 환경에서 유리한지 불리한지, 이유 포함 1~2문장>"
}}"""

    try:
        import json
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # JSON 블록만 추출
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text)
        return {
            "sentiment":     float(data.get("sentiment", 0.0)),
            "label":         data.get("label", "중립"),
            "summary":       data.get("summary", ""),
            "risks":         [r for r in data.get("risks", []) if r],
            "opportunities": [o for o in data.get("opportunities", []) if o],
            "strategy_fit":  data.get("strategy_fit", ""),
            "raw_articles":  len(all_articles),
        }
    except Exception as e:
        print(f"[시장맥락] Claude API 호출 실패: {e}")
        return _empty_context()


def _empty_context() -> dict:
    return {
        "sentiment": 0.0, "label": "중립",
        "summary": "", "risks": [], "opportunities": [],
        "strategy_fit": "", "raw_articles": 0,
    }
