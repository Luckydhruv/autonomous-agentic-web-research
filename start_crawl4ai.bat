@echo off
setlocal
set "ROOT=%~dp0"
set "ENV_FILE=%ROOT%.env"
set "PYTHON=%ROOT%venv\Scripts\python.exe"
if not exist "%ENV_FILE%" (echo Missing .env. Copy .env.example to .env first.& exit /b 1)
if not exist "%PYTHON%" set "PYTHON=python"
set "CRAWL4_AI_BASE_DIRECTORY=%ROOT%"
"%PYTHON%" -c "import crawl4ai, uvicorn" >nul 2>nul || (echo Install dependencies with: %PYTHON% -m pip install -r backend\requirements.txt& exit /b 1)

for /f "tokens=1,* delims==" %%A in ('findstr /b "CRAWL4AI_API_TOKEN=" "%ENV_FILE%"') do set "CRAWL4AI_API_TOKEN=%%B"
if not defined CRAWL4AI_API_TOKEN goto generate_token
if "%CRAWL4AI_API_TOKEN%"=="generate_a_long_random_token" goto generate_token
goto start_server

:generate_token
for /f %%T in ('powershell -NoProfile -Command "[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLower()"') do set "CRAWL4AI_API_TOKEN=%%T"
if not defined CRAWL4AI_API_TOKEN (echo Could not generate a Crawl4AI token.& exit /b 1)
powershell -NoProfile -Command "$p='%ENV_FILE%'; $s=Get-Content -Raw $p; if ($s -match '(?m)^CRAWL4AI_API_TOKEN=') {$s=[regex]::Replace($s,'(?m)^CRAWL4AI_API_TOKEN=.*$',('CRAWL4AI_API_TOKEN='+$env:CRAWL4AI_API_TOKEN))} else {$s += [Environment]::NewLine+'CRAWL4AI_API_TOKEN='+$env:CRAWL4AI_API_TOKEN+[Environment]::NewLine}; Set-Content -NoNewline -Path $p -Value $s"

:start_server
curl.exe -fsS http://127.0.0.1:11235/health >nul 2>nul && (echo Local Crawl4AI API is already running.& exit /b 0)
set "CRAWL4AI_API_TOKEN=%CRAWL4AI_API_TOKEN%"
start "Local Crawl4AI API" /MIN cmd /c "cd /d "%ROOT%backend" ^&^& set CRAWL4AI_API_TOKEN=%CRAWL4AI_API_TOKEN% ^&^& set CRAWL4_AI_BASE_DIRECTORY=%ROOT% ^&^& "%PYTHON%" -m uvicorn crawl4ai_server:app --host 127.0.0.1 --port 11235"
timeout /t 10 /nobreak >nul
curl.exe -fsS http://127.0.0.1:11235/health >nul 2>nul && (echo Local Crawl4AI API is ready at http://127.0.0.1:11235.& exit /b 0)
echo Crawl4AI has not become healthy yet. Check the Local Crawl4AI API window.
exit /b 1
