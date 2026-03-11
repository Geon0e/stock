"""
카카오 나에게 보내기 초기 설정 (최초 1회만 실행)

실행:
    python kakao_setup.py

준비사항:
    1. https://developers.kakao.com 접속 → 로그인
    2. [내 애플리케이션] → [애플리케이션 추가하기]
    3. 앱 이름 입력 후 저장
    4. [앱 키] 탭에서 REST API 키 복사
    5. [카카오 로그인] → 활성화 ON
    6. [카카오 로그인] → [Redirect URI] → http://localhost 추가
    7. [동의항목] → '카카오톡 메시지 전송' 체크
"""

import os
import sys
import webbrowser
import urllib.parse
from pathlib import Path

import requests
import urllib3
urllib3.disable_warnings()

ENV_PATH = Path(__file__).parent / ".env"


def _load_env() -> dict:
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _save_env(updates: dict):
    env = _load_env()
    env.update(updates)
    lines = []
    # 기존 파일 구조 유지하며 값만 업데이트
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in updates:
                    lines.append(f"{k}={updates[k]}")
                    continue
            lines.append(line)
    # 새 키 추가
    existing_keys = set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            existing_keys.add(stripped.split("=", 1)[0].strip())
    for k, v in updates.items():
        if k not in existing_keys:
            lines.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def get_token(rest_api_key: str, auth_code: str, client_secret: str = "") -> dict:
    """인가 코드 → 토큰 교환"""
    data = {
        "grant_type":   "authorization_code",
        "client_id":    rest_api_key,
        "redirect_uri": "http://localhost",
        "code":         auth_code,
    }
    if client_secret:
        data["client_secret"] = client_secret
    resp = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data=data,
        verify=False,
        timeout=15,
    )
    return resp.json()


def refresh_token(rest_api_key: str, refresh_tk: str) -> dict:
    """리프레시 토큰으로 액세스 토큰 재발급"""
    resp = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data={
            "grant_type":    "refresh_token",
            "client_id":     rest_api_key,
            "refresh_token": refresh_tk,
        },
        verify=False,
        timeout=15,
    )
    return resp.json()


def main():
    print("=" * 55)
    print("  카카오 나에게 보내기 초기 설정")
    print("=" * 55)
    print()

    env = _load_env()

    # ── REST API 키 입력 ──────────────────────────────────────────────────
    existing_key = env.get("KAKAO_REST_API_KEY", "")
    if existing_key and not existing_key.startswith("여기에"):
        print(f"[기존] REST API 키: {existing_key[:8]}...")
        change = input("새로 설정하시겠습니까? (y/N): ").strip().lower()
        if change != "y":
            rest_api_key = existing_key
        else:
            rest_api_key = input("REST API 키 입력: ").strip()
    else:
        print("kakao developers.kakao.com 에서 REST API 키를 복사하세요.")
        rest_api_key = input("REST API 키 입력: ").strip()

    if not rest_api_key:
        print("[오류] REST API 키가 없습니다.")
        sys.exit(1)

    # ── Client Secret (선택) ────────────────────────────────────────────
    print()
    print("[선택] 카카오 개발자 콘솔 → 보안 탭에 Client Secret이 활성화되어 있으면 입력하세요.")
    print("  없거나 모르면 그냥 Enter")
    client_secret = input("Client Secret (없으면 Enter): ").strip()

    # ── 브라우저로 카카오 로그인 ─────────────────────────────────────────
    auth_url = (
        "https://kauth.kakao.com/oauth/authorize"
        f"?client_id={rest_api_key}"
        "&redirect_uri=http://localhost"
        "&response_type=code"
        "&scope=talk_message"
    )

    print()
    print("[1단계] 브라우저에서 카카오 로그인을 진행합니다...")
    print(f"  URL: {auth_url}")
    print()
    webbrowser.open(auth_url)

    print("[2단계] 로그인 후 브라우저 주소창에 표시되는 URL을 전체 복사하세요.")
    print("  (주소창에 'localhost/?code=...' 또는 에러 페이지가 뜨는 게 정상)")
    print("  예시: http://localhost/?code=AbCdEf1234567890")
    print()
    redirect_url = input("리다이렉트 URL 붙여넣기: ").strip()

    # 인가 코드 추출
    parsed = urllib.parse.urlparse(redirect_url)
    params = urllib.parse.parse_qs(parsed.query)
    if "error" in params:
        err = params.get("error", ["?"])[0]
        desc = params.get("error_description", [""])[0]
        print(f"[오류] 카카오 인증 실패: {err} - {desc}")
        if err == "access_denied":
            print("  → 동의항목에서 '카카오톡 메시지 전송'을 체크했는지 확인하세요.")
        sys.exit(1)
    if "code" not in params:
        print("[오류] URL에서 code를 찾을 수 없습니다.")
        print(f"  입력된 URL: {redirect_url}")
        sys.exit(1)
    auth_code = params["code"][0]
    print(f"  인가 코드 확인: {auth_code[:10]}...")

    # ── 토큰 발급 ────────────────────────────────────────────────────────
    print()
    print("[3단계] 액세스 토큰 발급 중...")
    token_data = get_token(rest_api_key, auth_code, client_secret)

    if "error" in token_data or "access_token" not in token_data:
        error_code = token_data.get("error", "unknown")
        error_desc = token_data.get("error_description", "")
        print(f"[오류] 토큰 발급 실패")
        print(f"  에러 코드: {error_code}")
        print(f"  설명: {error_desc}")
        print()

        if error_code == "KOE320" or "authorization code" in error_desc.lower():
            print("  → 인가 코드가 만료되었습니다.")
            print("    처음부터 다시 실행해주세요 (코드는 1회만 사용 가능, 수분 내 만료)")
        elif error_code == "KOE010" or "client_secret" in error_desc.lower():
            print("  → Client Secret이 필요합니다.")
            print("    카카오 개발자 콘솔 → 내 앱 → 보안 탭에서 Client Secret 값을 복사해주세요.")
        elif "redirect_uri" in error_desc.lower():
            print("  → Redirect URI가 일치하지 않습니다.")
            print("    카카오 개발자 콘솔 → 카카오 로그인 → Redirect URI에")
            print("    정확히 'http://localhost' 가 등록되어 있는지 확인하세요.")
        sys.exit(1)

    access_token  = token_data.get("access_token", "")
    refresh_tk    = token_data.get("refresh_token", "")

    if not access_token:
        print(f"[오류] 액세스 토큰이 없습니다: {token_data}")
        sys.exit(1)

    print(f"  액세스 토큰: {access_token[:10]}...")
    print(f"  리프레시 토큰: {refresh_tk[:10]}...")

    # ── .env 저장 ────────────────────────────────────────────────────────
    _save_env({
        "KAKAO_REST_API_KEY":    rest_api_key,
        "KAKAO_CLIENT_SECRET":   client_secret,
        "KAKAO_ACCESS_TOKEN":    access_token,
        "KAKAO_REFRESH_TOKEN":   refresh_tk,
    })
    print()
    print("[완료] .env 파일에 저장했습니다.")

    # ── 테스트 메시지 전송 ────────────────────────────────────────────────
    print()
    print("[4단계] 테스트 메시지 전송...")
    from kakao_bot import KakaoBot
    bot = KakaoBot(rest_api_key, access_token, refresh_tk)
    ok  = bot.send_text("✅ KOSPI 200 스캐너 카카오톡 연결 테스트 성공!")
    if ok:
        print("[OK] 카카오톡에서 메시지를 확인하세요!")
    else:
        print("[오류] 메시지 전송에 실패했습니다. 동의항목을 다시 확인해주세요.")

    print()
    print("=" * 55)
    print("  설정 완료! 이제 아래 명령어로 알림을 보낼 수 있습니다:")
    print("  python kakao_bot.py")
    print("  python kakao_bot.py --schedule   # 매일 자동 전송")
    print("=" * 55)


if __name__ == "__main__":
    main()
