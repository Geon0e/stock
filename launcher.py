"""
exe 실행 진입점
PyInstaller로 빌드 후 이 파일이 실행됨
"""

import os
import sys
import threading
import time
import webbrowser
import subprocess

PORT = 8501


def _open_browser():
    time.sleep(3)
    webbrowser.open(f"http://localhost:{PORT}")


def _get_base():
    """exe 내부 번들 경로 또는 소스 폴더 경로 반환"""
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def main():
    base = _get_base()
    app_path = os.path.join(base, "app.py")

    # 브라우저 자동 오픈 (별도 스레드)
    threading.Thread(target=_open_browser, daemon=True).start()

    # Streamlit 실행
    os.environ.setdefault("STREAMLIT_SERVER_HEADLESS", "true")
    os.environ.setdefault("STREAMLIT_SERVER_PORT", str(PORT))
    os.environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")

    from streamlit.web import cli as stcli
    sys.argv = [
        "streamlit", "run", app_path,
        "--server.port", str(PORT),
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
        "--global.developmentMode", "false",
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
