@echo off
setlocal
set "ROOT=%~dp0"
set "ENV_FILE=%ROOT%.env"
set "CONTAINER=autonomous-research-crawl4ai"
set "IMAGE=unclecode/crawl4ai:0.9.2"

where docker >nul 2>nul || (echo Docker is required. Install Docker Desktop, then run this script again.& exit /b 1)
if not exist "%ENV_FILE%" (echo Missing .env. Copy .env.example to .env first.& exit /b 1)

for /f "tokens=1,* delims==" %%A in ('findstr /b "CRAWL4AI_API_TOKEN=" "%ENV_FILE%"') do set "CRAWL4AI_API_TOKEN=%%B"
if not defined CRAWL4AI_API_TOKEN goto generate_token
if "%CRAWL4AI_API_TOKEN%"=="generate_a_long_random_token" goto generate_token
goto start_container

:generate_token
for /f %%T in ('powershell -NoProfile -Command "[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLower()"') do set "CRAWL4AI_API_TOKEN=%%T"
if not defined CRAWL4AI_API_TOKEN (echo Could not generate a Crawl4AI token.& exit /b 1)
powershell -NoProfile -Command "$p='%ENV_FILE%'; $s=Get-Content -Raw $p; if ($s -match '(?m)^CRAWL4AI_API_TOKEN=') {$s=[regex]::Replace($s,'(?m)^CRAWL4AI_API_TOKEN=.*$',('CRAWL4AI_API_TOKEN='+$env:CRAWL4AI_API_TOKEN))} else {$s += [Environment]::NewLine+'CRAWL4AI_API_TOKEN='+$env:CRAWL4AI_API_TOKEN+[Environment]::NewLine}; Set-Content -NoNewline -Path $p -Value $s"

:start_container
docker container inspect "%CONTAINER%" >nul 2>nul
if %ERRORLEVEL%==0 (
  docker start "%CONTAINER%" >nul
) else (
  docker run -d --name "%CONTAINER%" --restart unless-stopped --shm-size=1g -p 11235:11235 -e CRAWL4AI_API_TOKEN=%CRAWL4AI_API_TOKEN% "%IMAGE%" >nul
)
if errorlevel 1 (echo Could not start Crawl4AI container.& exit /b 1)
echo Crawl4AI is starting at http://localhost:11235.
