@echo off
chcp 65001 > nul
echo ========================================
echo  카카오톡 리포트 자동 스케줄 등록
echo ========================================
echo.

:: Python 경로 확인 (실제 실행 파일 경로, WindowsApps 스텁 제외)
for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)"') do set PYTHON_PATH=%%i
if "%PYTHON_PATH%"=="" (
    echo [오류] Python을 찾을 수 없습니다.
    pause
    exit /b 1
)

:: WindowsApps 스텁 경고
echo %PYTHON_PATH% | findstr /i "WindowsApps" > nul
if %errorlevel% equ 0 (
    echo [경고] WindowsApps 스텁이 감지되었습니다. Task Scheduler에서 동작하지 않을 수 있습니다.
    echo        Microsoft Store Python 앱을 비활성화하거나 정식 Python을 설치해 주세요.
    pause
)

echo [확인] Python: %PYTHON_PATH%
echo.

:: 현재 스크립트 디렉토리
set SCRIPT_DIR=%~dp0
set SCRIPT_DIR=%SCRIPT_DIR:~0,-1%

:: 등록 선택
echo 어떤 스케줄을 등록하시겠습니까?
echo   1) KOSPI 200 리포트    - 평일 08:30
echo   2) NASDAQ 100 리포트   - 평일 23:00
echo   3) 리포트 둘 다 등록
echo   4) 매도 모니터         - 평일 09:00 (장마감 15:30 자동 종료) [선택사항]
echo   5) 전체 등록 (1+2+4)
echo.
echo   * 매도 모니터(4번)는 리포트 전송 후 자동 시작됩니다.
echo     별도 등록 없이도 동작하므로 1번 또는 3번만 등록하면 됩니다.
echo.
set /p CHOICE="선택 (1/2/3/4/5): "

if "%CHOICE%"=="1" goto REGISTER_KOSPI
if "%CHOICE%"=="2" goto REGISTER_NASDAQ
if "%CHOICE%"=="3" goto REGISTER_BOTH
if "%CHOICE%"=="4" goto REGISTER_MONITOR
if "%CHOICE%"=="5" goto REGISTER_ALL
goto REGISTER_KOSPI

:REGISTER_KOSPI
call :DO_REGISTER kospi200 StockReport_KOSPI200 08:30
goto DONE

:REGISTER_NASDAQ
call :DO_REGISTER nasdaq100 StockReport_NASDAQ100 23:00
goto DONE

:REGISTER_BOTH
call :DO_REGISTER kospi200 StockReport_KOSPI200 08:30
call :DO_REGISTER nasdaq100 StockReport_NASDAQ100 23:00
goto DONE

:REGISTER_MONITOR
call :DO_REGISTER_MONITOR
goto DONE

:REGISTER_ALL
call :DO_REGISTER kospi200 StockReport_KOSPI200 08:30
call :DO_REGISTER nasdaq100 StockReport_NASDAQ100 23:00
call :DO_REGISTER_MONITOR
goto DONE

:: ── 리포트 등록 함수 ────────────────────────────────────────
:DO_REGISTER
set _MARKET=%1
set _TASK=%2
set _TIME=%3

echo.
echo [등록] %_TASK%  (%_MARKET%, 평일 %_TIME%)

schtasks /delete /tn "%_TASK%" /f > nul 2>&1

schtasks /create ^
  /tn "%_TASK%" ^
  /tr "\"%PYTHON_PATH%\" \"%SCRIPT_DIR%\daily_report.py\" --market %_MARKET%" ^
  /sc WEEKLY ^
  /d MON,TUE,WED,THU,FRI ^
  /st %_TIME% ^
  /rl HIGHEST ^
  /f

if %errorlevel% equ 0 (
    echo [완료] %_TASK% 등록 성공 - 평일 %_TIME% 자동 실행
) else (
    echo [오류] %_TASK% 등록 실패 - 관리자 권한으로 실행해 주세요
)
goto :eof

:: ── 매도 모니터 등록 함수 ───────────────────────────────────
:DO_REGISTER_MONITOR
set _TASK=StockSellMonitor

echo.
echo [등록] %_TASK%  (매도 모니터, 평일 09:00 시작 / 15:30 자동 종료)

schtasks /delete /tn "%_TASK%" /f > nul 2>&1

schtasks /create ^
  /tn "%_TASK%" ^
  /tr "\"%PYTHON_PATH%\" \"%SCRIPT_DIR%\notifications\sell_monitor.py\"" ^
  /sc WEEKLY ^
  /d MON,TUE,WED,THU,FRI ^
  /st 09:00 ^
  /rl HIGHEST ^
  /f

if %errorlevel% equ 0 (
    echo [완료] %_TASK% 등록 성공 - 평일 09:00 자동 시작, 15:30 자동 종료
) else (
    echo [오류] %_TASK% 등록 실패 - 관리자 권한으로 실행해 주세요
)
goto :eof

:: ── 완료 ───────────────────────────────────────────────────
:DONE
echo.
echo ========================================
echo [테스트] 즉시 실행:
echo   python "%SCRIPT_DIR%\daily_report.py" --market kospi200 --now
echo   python "%SCRIPT_DIR%\daily_report.py" --market nasdaq100 --now
echo.
echo [확인]   작업 스케줄러 확인:
echo   Win+R → taskschd.msc
echo.
echo [제거]   스케줄 삭제:
echo   schtasks /delete /tn "StockReport_KOSPI200" /f
echo   schtasks /delete /tn "StockReport_NASDAQ100" /f
echo   schtasks /delete /tn "StockSellMonitor" /f
echo ========================================
echo.
pause
