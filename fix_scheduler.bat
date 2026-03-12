@echo off
chcp 65001 > /dev/null
echo ========================================
echo  Task Scheduler Python 경로 수정
echo ========================================
echo.

:: 관리자 권한 확인
net session > /dev/null 2>&1
if %errorlevel% neq 0 (
    echo [오류] 관리자 권한으로 실행해 주세요.
    echo   이 파일에서 오른쪽 클릭 → "관리자 권한으로 실행"
    pause
    exit /b 1
)

:: 실제 Python 경로 가져오기
for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)"') do set PYTHON_PATH=%%i
if "%PYTHON_PATH%"=="" (
    echo [오류] Python을 찾을 수 없습니다.
    pause
    exit /b 1
)
echo [Python] %PYTHON_PATH%

set SCRIPT_DIR=C:\Users\USER\claude\stock

:: KOSPI200
schtasks /delete /tn "StockReport_KOSPI200" /f > /dev/null 2>&1
schtasks /create /tn "StockReport_KOSPI200" /tr "\"%PYTHON_PATH%\" \"%SCRIPT_DIR%\daily_report.py\" --market kospi200" /sc WEEKLY /d MON,TUE,WED,THU,FRI /st 08:30 /rl HIGHEST /f
if %errorlevel% equ 0 (
    echo [완료] StockReport_KOSPI200 재등록 성공
) else (
    echo [오류] StockReport_KOSPI200 재등록 실패
)

:: NASDAQ100
schtasks /delete /tn "StockReport_NASDAQ100" /f > /dev/null 2>&1
schtasks /create /tn "StockReport_NASDAQ100" /tr "\"%PYTHON_PATH%\" \"%SCRIPT_DIR%\daily_report.py\" --market nasdaq100" /sc WEEKLY /d MON,TUE,WED,THU,FRI /st 23:00 /rl HIGHEST /f
if %errorlevel% equ 0 (
    echo [완료] StockReport_NASDAQ100 재등록 성공
) else (
    echo [오류] StockReport_NASDAQ100 재등록 실패
)

echo.
echo [확인] 등록된 Task 목록:
schtasks /query /fo TABLE /tn "StockReport*" 2>&1

echo.
pause
