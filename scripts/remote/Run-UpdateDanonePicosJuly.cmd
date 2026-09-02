@echo off
setlocal

powershell.exe -NoProfile -ExecutionPolicy Bypass ^
  -File "\\tsclient\H\Update-DanonePicosHistory.ps1" ^
  -FromYearMonth 202607 ^
  -ToYearMonth 202607 ^
  -TransferRoot "\\tsclient\H" ^
  -TrustServerCertificate > "\\tsclient\H\PICOS_JULY_UPDATE.log" 2>&1

set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" (
  echo PICOS за июль успешно обновлён.
) else (
  echo Обновление PICOS за июль завершилось с ошибкой. Код: %EXIT_CODE%
  echo Подробности сохранены в H:\PICOS_JULY_UPDATE.log
)
type "\\tsclient\H\PICOS_JULY_UPDATE.log"
pause
exit /b %EXIT_CODE%
