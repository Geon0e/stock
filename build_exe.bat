@echo off
chcp 65001 > nul
echo ========================================
echo  주식 신호 스캐너 EXE 빌드
echo ========================================
echo.

:: PyInstaller 설치 확인
pip show pyinstaller > nul 2>&1
if %errorlevel% neq 0 (
    echo [설치] PyInstaller 설치 중...
    pip install pyinstaller
)

:: Streamlit 경로 확인
for /f "tokens=*" %%i in ('python -c "import streamlit; import os; print(os.path.dirname(streamlit.__file__))"') do set ST_PATH=%%i
echo [확인] Streamlit 경로: %ST_PATH%

:: 빌드 실행
echo.
echo [빌드] PyInstaller 실행 중... (5~10분 소요)
echo.

pyinstaller ^
  --noconfirm ^
  --onedir ^
  --windowed ^
  --name "StockScanner" ^
  --icon NONE ^
  --add-data "app.py;." ^
  --add-data "scanner.py;." ^
  --add-data "config.py;." ^
  --add-data "signals;signals" ^
  --add-data "data;data" ^
  --add-data "backtest;backtest" ^
  --add-data "%ST_PATH%;streamlit" ^
  --hidden-import streamlit ^
  --hidden-import streamlit.web.cli ^
  --hidden-import streamlit.runtime ^
  --hidden-import plotly ^
  --hidden-import pandas ^
  --hidden-import numpy ^
  --hidden-import requests ^
  --hidden-import bs4 ^
  --hidden-import pykrx ^
  --collect-all streamlit ^
  --collect-all plotly ^
  launcher.py

echo.
if exist "dist\StockScanner\StockScanner.exe" (
    echo ========================================
    echo  빌드 성공!
    echo  위치: dist\StockScanner\StockScanner.exe
    echo  폴더째로 복사해서 사용하세요
    echo ========================================
) else (
    echo [오류] 빌드 실패 - 위 오류 메시지를 확인하세요
)
pause
