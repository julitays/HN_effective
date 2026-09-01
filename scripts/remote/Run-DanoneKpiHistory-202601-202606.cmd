@echo off
setlocal

powershell.exe -NoProfile -ExecutionPolicy Bypass ^
  -File "\\tsclient\H\Export-DanoneKpiHistory.ps1" ^
  -FromYearMonth 202601 ^
  -ToYearMonth 202606 ^
  -TransferRoot "\\tsclient\H" ^
  -TrustServerCertificate

set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" (
  echo Историческая выгрузка завершена успешно.
) else (
  echo Историческая выгрузка завершилась с ошибкой. Код: %EXIT_CODE%
)
pause
exit /b %EXIT_CODE%
