@echo off
chcp 65001 > nul
echo ========================================
echo  평일 8:30 카카오톡 리포트 자동 등록
echo ========================================
echo.

:: Python 경로 확인
for /f "delims=" %%i in ('where python') do set PYTHON_PATH=%%i
if "%PYTHON_PATH%"=="" (
    echo [오류] Python을 찾을 수 없습니다.
    pause
    exit /b 1
)

echo [확인] Python: %PYTHON_PATH%
echo.

:: 현재 스크립트 디렉토리
set SCRIPT_DIR=%~dp0
set SCRIPT_DIR=%SCRIPT_DIR:~0,-1%

:: 시장 선택
echo 어떤 시장 리포트를 받으시겠습니까?
echo   1) KOSPI 200 (기본)
echo   2) NASDAQ 100
echo.
set /p MARKET_CHOICE="선택 (1 또는 2, 기본=1): "

if "%MARKET_CHOICE%"=="2" (
    set MARKET=nasdaq100
    set TASK_NAME=StockReport_NASDAQ100
) else (
    set MARKET=kospi200
    set TASK_NAME=StockReport_KOSPI200
)

echo.
echo [등록] 작업: %TASK_NAME%  /  시장: %MARKET%

:: 기존 작업 삭제 (있으면)
schtasks /delete /tn "%TASK_NAME%" /f > nul 2>&1

:: 작업 스케줄러 등록 (평일 08:30)
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "\"%PYTHON_PATH%\" \"%SCRIPT_DIR%\daily_report.py\" --market %MARKET%" ^
  /sc WEEKLY ^
  /d MON,TUE,WED,THU,FRI ^
  /st 08:30 ^
  /rl HIGHEST ^
  /f

if %errorlevel% equ 0 (
    echo.
    echo [완료] 작업 스케줄러 등록 성공!
    echo.
    echo  - 작업명  : %TASK_NAME%
    echo  - 시장    : %MARKET%
    echo  - 실행    : 평일 오전 08:30
    echo  - Python  : %PYTHON_PATH%
    echo  - 스크립트: %SCRIPT_DIR%\daily_report.py
    echo.
    echo [테스트] 지금 즉시 실행하려면:
    echo   python "%SCRIPT_DIR%\daily_report.py" --market %MARKET% --now
    echo.
    echo [제거]   등록된 스케줄을 삭제하려면:
    echo   schtasks /delete /tn "%TASK_NAME%" /f
) else (
    echo.
    echo [오류] 작업 스케줄러 등록 실패
    echo        관리자 권한으로 실행해 주세요 (마우스 우클릭 → 관리자 권한으로 실행)
)

echo.
pause
