@echo off
rem Windows launcher for the native nesting app -- double-click to start.
rem Uses the project's .venv when there is one, else Python from PATH (or
rem the py launcher). Without a venv it runs in this console window, so a
rem missing package shows its error here instead of failing silently.
cd /d "%~dp0.."
if exist .venv\Scripts\pythonw.exe (
    start "" .venv\Scripts\pythonw.exe native_app\main.py %*
    exit /b
)
where python >nul 2>nul && (python native_app\main.py %*) || (py -3 native_app\main.py %*)
if errorlevel 1 (
    echo.
    echo AlphaNest did not start. Install its packages with:
    echo     python -m pip install -r requirements.txt
    pause
)
