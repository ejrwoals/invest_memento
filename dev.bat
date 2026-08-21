@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"

REM 개발 서버 한 줄 실행. 몇 번을 돌려도 결과가 같다 - 없는 것만 만들고 채운다.
REM   dev.bat            웹 + API
REM   dev.bat --worker   웹 + API + 배치 워커

REM OneDrive 폴더에서는 하드링크가 막힌다 - uv 는 복사 모드로 고정한다.
set "UV_LINK_MODE=copy"

set "WITH_WORKER="
if /i "%~1"=="--worker" set "WITH_WORKER=1"

echo.
echo === [1/4] 도구 확인 ===

where node >nul 2>&1
if errorlevel 1 (
  echo   x Node.js 가 없다. https://nodejs.org 에서 22 이상을 설치하고 다시 실행하라.
  goto :fail
)
for /f "delims=" %%v in ('node -v') do echo   - node %%v

where uv >nul 2>&1
if errorlevel 1 (
  echo   x uv 가 없다. PowerShell 에서 다음을 실행하고 터미널을 새로 열어라.
  echo       winget install --id astral-sh.uv
  goto :fail
)
for /f "delims=" %%v in ('uv --version') do echo   - %%v

where pnpm >nul 2>&1
if errorlevel 1 (
  echo   - pnpm 이 없어 설치한다...
  call npm install -g pnpm@11.15.1
  if errorlevel 1 goto :fail
)
for /f "delims=" %%v in ('pnpm -v') do echo   - pnpm %%v

echo.
echo === [2/4] 의존성 동기화 ===
call pnpm install --prefer-offline
if errorlevel 1 goto :fail
pushd apps\api
call uv sync
set "UVRC=%errorlevel%"
popd
if not "%UVRC%"=="0" goto :fail

echo.
echo === [3/4] 환경변수 확인 ===
if not exist "apps\web\.env.local" (
  copy /y "apps\web\.env.example" "apps\web\.env.local" >nul
  echo   - apps\web\.env.local 을 새로 만들었다.
)
if not exist "apps\api\.env" (
  copy /y "apps\api\.env.example" "apps\api\.env" >nul
  echo   - apps\api\.env 를 새로 만들었다.
)

set "ENV_OK=1"
call :needvalue "apps\web\.env.local" "NEXT_PUBLIC_SUPABASE_URL"
call :needvalue "apps\web\.env.local" "NEXT_PUBLIC_SUPABASE_ANON_KEY"
call :needvalue "apps\web\.env.local" "NEXT_PUBLIC_API_URL"
call :nofiller  "apps\web\.env.local" "project-ref"
call :needvalue "apps\api\.env" "SUPABASE_URL"
call :needvalue "apps\api\.env" "DATABASE_URL"
call :nofiller  "apps\api\.env" "project-ref"
call :nofiller  "apps\api\.env" "YOUR-PASSWORD"

if not defined ENV_OK (
  echo.
  echo   위 값을 채운 뒤 dev.bat 을 다시 실행하라. 값은 Supabase 대시보드에 있다.
  echo     NEXT_PUBLIC_SUPABASE_URL / SUPABASE_URL   Settings - API Keys - Project URL
  echo     NEXT_PUBLIC_SUPABASE_ANON_KEY             Settings - API Keys - anon key
  echo     DATABASE_URL                              Connect - Transaction pooler
  echo     NEXT_PUBLIC_API_URL                       http://127.0.0.1:8003 그대로 두면 된다
  goto :fail
)

findstr /r /c:"^ANTHROPIC_API_KEY=..*" "apps\api\.env" >nul 2>&1
if errorlevel 1 echo   ! ANTHROPIC_API_KEY 가 비어 있다. /write 대화형 작성만 동작하지 않는다.
echo   - 환경변수 준비됨

echo.
echo === [4/4] 개발 서버 기동 ===
echo   웹    http://localhost:3003
echo   API   http://127.0.0.1:8003/docs
if defined WITH_WORKER echo   워커  배치 크론 동봉
echo   중지  이 창에서 Ctrl+C
echo.
if defined WITH_WORKER (
  call pnpm dev:all
) else (
  call pnpm dev
)
popd
endlocal
exit /b 0

:needvalue
findstr /r /c:"^%~2=..*" "%~1" >nul 2>&1
if errorlevel 1 (
  echo   x %~1 - %~2 값이 비어 있다.
  set "ENV_OK="
)
exit /b 0

:nofiller
findstr /c:"%~2" "%~1" >nul 2>&1
if not errorlevel 1 (
  echo   x %~1 - 예시값 %~2 이 그대로 남아 있다.
  set "ENV_OK="
)
exit /b 0

:fail
echo.
echo === 중단됨 ===
popd
endlocal
pause
exit /b 1
