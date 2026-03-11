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
