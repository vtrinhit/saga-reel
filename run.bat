@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

rem ---- Tìm uv (trên PATH hoặc ở %USERPROFILE%\.local\bin) ----
set "UV="
where uv >nul 2>nul && set "UV=uv"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"

rem ---- Tạo môi trường Python nếu chưa có ----
if exist ".venv\Scripts\python.exe" goto venv_ok
echo [SagaReel] Đang tạo môi trường Python 3.11 (.venv)...
if exist ".venv\.installed" del /q ".venv\.installed"
if defined UV goto venv_uv
py -3.11 -m venv .venv
goto venv_check
:venv_uv
"%UV%" venv -p 3.11 .venv
:venv_check
if exist ".venv\Scripts\python.exe" goto venv_ok
echo [SagaReel] Không tạo được môi trường Python. Hãy cài uv hoặc Python 3.11 rồi chạy lại.
pause
exit /b 1
:venv_ok

rem ---- Cài lại gói khi pyproject.toml mới hơn dấu .venv\.installed ----
powershell -NoProfile -Command "if ((-not (Test-Path '.venv\.installed')) -or ((Get-Item 'pyproject.toml').LastWriteTime -gt (Get-Item '.venv\.installed').LastWriteTime)) { exit 1 } else { exit 0 }"
if not errorlevel 1 goto install_ok
echo [SagaReel] Đang cài đặt thư viện...
if defined UV goto install_uv
".venv\Scripts\python.exe" -m pip install -e .
goto install_check
:install_uv
"%UV%" pip install -p .venv\Scripts\python.exe -e .
:install_check
if errorlevel 1 goto install_fail
type nul > ".venv\.installed"
goto install_ok
:install_fail
echo [SagaReel] Cài đặt thư viện thất bại. Xem lỗi ở trên rồi chạy lại.
pause
exit /b 1
:install_ok

rem ---- Build giao diện nếu có frontend mà chưa có bản build ----
if not exist "frontend\package.json" goto frontend_ok
if exist "frontend\dist\index.html" goto frontend_ok
where npm >nul 2>nul
if errorlevel 1 goto frontend_no_npm
echo [SagaReel] Đang build giao diện...
call npm --prefix frontend ci
if errorlevel 1 goto frontend_fail
call npm --prefix frontend run build
if errorlevel 1 goto frontend_fail
goto frontend_ok
:frontend_no_npm
echo [SagaReel] Không tìm thấy npm: bỏ qua bước build giao diện.
goto frontend_ok
:frontend_fail
echo [SagaReel] Build giao diện thất bại: máy chủ vẫn chạy nhưng có thể thiếu giao diện.
:frontend_ok

rem ---- Mở trình duyệt và chạy máy chủ ----
start "" http://127.0.0.1:8990
".venv\Scripts\python.exe" -m sagareel serve
endlocal
