@echo off
chcp 65001 > nul
echo ========================================
echo  주식 신호 스캐너 - 설치 및 실행
echo ========================================
echo.

:: Python 설치 확인
python --version > nul 2>&1
if %errorlevel% neq 0 (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo.
    echo Python 3.11 이상을 설치해주세요:
    echo https://www.python.org/downloads/
    echo.
    echo 설치 시 "Add Python to PATH" 반드시 체크!
    pause
    exit /b 1
)

python --version
echo.

:: 패키지 설치 확인
python -c "import streamlit" > nul 2>&1
if %errorlevel% neq 0 (
    echo [설치] 필요 패키지 설치 중... (최초 1회, 3~5분 소요)
    pip install -r requirements.txt
    echo.
)

echo [실행] 브라우저가 자동으로 열립니다...
echo       수동 접속: http://localhost:8501
echo       종료: 이 창을 닫으세요
echo.
streamlit run app.py --server.headless true --browser.gatherUsageStats false
pause
