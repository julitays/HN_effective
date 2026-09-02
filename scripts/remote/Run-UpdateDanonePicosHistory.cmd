@echo off
setlocal

powershell.exe -NoProfile -ExecutionPolicy Bypass ^
  -File "\\tsclient\H\Update-DanonePicosHistory.ps1" ^
  -FromYearMonth 202601 ^
  -ToYearMonth 202607 ^
  -TransferRoot "\\tsclient\H" ^
  -TrustServerCertificate

set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" (
  echo PICOS успешно обновлён во всех пакетах.
) else (
  echo Обновление PICOS завершилось с ошибкой. Код: %EXIT_CODE%
)
pause
exit /b %EXIT_CODE%
