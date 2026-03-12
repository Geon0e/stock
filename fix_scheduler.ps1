$python = 'C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe'
$script = 'C:\Users\USER\claude\stock\daily_report.py'

# KOSPI200 - 08:30
schtasks /delete /tn "StockReport_KOSPI200" /f
schtasks /create /tn "StockReport_KOSPI200" /tr "`"$python`" `"$script`" --market kospi200 --now" /sc WEEKLY /d MON,TUE,WED,THU,FRI /st 08:30 /rl HIGHEST /f
Write-Host "[완료] StockReport_KOSPI200 → 08:30"

# NASDAQ100 - 22:30
schtasks /delete /tn "StockReport_NASDAQ100" /f
schtasks /create /tn "StockReport_NASDAQ100" /tr "`"$python`" `"$script`" --market nasdaq100 --now" /sc WEEKLY /d MON,TUE,WED,THU,FRI /st 22:30 /rl HIGHEST /f
Write-Host "[완료] StockReport_NASDAQ100 → 22:30"

Write-Host ""
Write-Host "=== 등록 결과 ==="
Write-Host "KOSPI200 NextRun:"
(Get-ScheduledTask -TaskName "StockReport_KOSPI200" | Get-ScheduledTaskInfo).NextRunTime.ToString()
Write-Host "NASDAQ100 NextRun:"
(Get-ScheduledTask -TaskName "StockReport_NASDAQ100" | Get-ScheduledTaskInfo).NextRunTime.ToString()
