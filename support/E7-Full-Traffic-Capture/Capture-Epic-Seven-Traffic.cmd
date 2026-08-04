@echo off
setlocal EnableExtensions
title Epic Seven Full Traffic Capture

if /i "%~1"=="--check" goto check

fltmc >nul 2>&1
if errorlevel 1 (
    echo Requesting Administrator access...
    powershell.exe -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)

echo.
echo ================================================================
echo  WARNING: THIS RECORDS FULL NETWORK PACKETS FROM EVERY NIC
echo ================================================================
echo.
echo The output may contain account traffic, visited services, local IP
echo addresses, DNS requests, and other sensitive information. Close all
echo unrelated apps first. Only share the result with someone you trust.
echo.
choice /c YN /n /m "Continue with unrestricted capture? [Y/N] "
if errorlevel 2 exit /b 1

for /f %%I in ('powershell.exe -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "STAMP=%%I"
set "OUTPUT_DIR=%~dp0E7-Capture-%STAMP%"
set "ETL=%OUTPUT_DIR%\EpicSeven-Full-Traffic.etl"
set "PCAP=%OUTPUT_DIR%\EpicSeven-Full-Traffic.pcapng"

mkdir "%OUTPUT_DIR%" || goto failed

rem End an abandoned PktMon session, then remove filters so capture is unrestricted.
pktmon stop >nul 2>&1
pktmon filter remove >nul 2>&1

echo.
echo Starting full-packet capture on all physical and virtual NICs...
pktmon start --capture --comp nics --type all --pkt-size 0 --file-name "%ETL%" --file-size 1024 --log-mode circular
if errorlevel 1 goto failed

echo.
echo CAPTURE IS RUNNING.
echo.
echo 1. Start the Meowtoko E7 Tool capture.
echo 2. Fully force-stop Epic Seven inside the emulator.
echo 3. Launch Epic Seven and wait at the lobby for 20-30 seconds.
echo 4. Finish capture in the Meowtoko E7 Tool and wait for the error.
echo 5. Return here and press any key.
echo.
pause

echo.
echo Stopping capture...
pktmon stop
if errorlevel 1 goto failed

echo Converting capture to pcapng...
pktmon etl2pcap "%ETL%" --out "%PCAP%"
if errorlevel 1 goto failed

echo.
echo CAPTURE COMPLETE
echo Send this sensitive file to your trusted debugger:
echo "%PCAP%"
echo.
echo Keep this window open until you have copied the path above.
pause
exit /b 0

:check
where pktmon.exe >nul 2>&1 || (
    echo ERROR: pktmon.exe is unavailable. Windows 10 version 2004 or newer is required.
    exit /b 1
)
pktmon start help >nul 2>&1 || (
    echo ERROR: This pktmon version does not support capture.
    exit /b 1
)
pktmon etl2pcap help >nul 2>&1 || (
    echo ERROR: This pktmon version cannot create pcapng files.
    exit /b 1
)
echo Check passed: this computer supports full traffic capture and pcapng conversion.
exit /b 0

:failed
echo.
echo ERROR: Capture did not complete. Run this command as Administrator to stop
echo any capture still in progress, then run this script again:
echo.
echo     pktmon stop
echo.
pause
exit /b 1
