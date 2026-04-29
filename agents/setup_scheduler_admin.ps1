# Run once as Administrator — right-click this file -> Run with PowerShell (as admin)
# Registers both INDEX-LAB tasks to run as YOU even when logged out.

$base    = "C:\Users\sidda\Downloads\TradingBot"
$python  = "python3"

Write-Host "Enter your Windows login password (stored securely by Task Scheduler):"
$cred    = Get-Credential -UserName $env:USERNAME -Message "Windows password for scheduled tasks"
$user    = $cred.UserName
$pass    = $cred.GetNetworkCredential().Password

$action1  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c cd /d `"$base`" && $python agents\email_agent.py >> logs\monitor.log 2>&1"
$action2  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c cd /d `"$base`" && $python agents\excel_agent.py >> logs\excel.log 2>&1"
$action3  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c cd /d `"$base`" && $python agents\metrics_agent.py >> logs\metrics.log 2>&1"
$trigger1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "11:30PM"
$trigger2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "11:35PM"
$trigger3 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday -At "5:00PM"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 4) -StartWhenAvailable

Register-ScheduledTask -TaskName "INDEX-LAB Email Agent"   -Action $action1 -Trigger $trigger1 -Settings $settings -RunLevel Highest -User $user -Password $pass -Force
Register-ScheduledTask -TaskName "INDEX-LAB Excel Agent"   -Action $action2 -Trigger $trigger2 -Settings $settings -RunLevel Highest -User $user -Password $pass -Force
Register-ScheduledTask -TaskName "INDEX-LAB Metrics Agent" -Action $action3 -Trigger $trigger3 -Settings $settings -RunLevel Highest -User $user -Password $pass -Force

Write-Host ""
Write-Host "Done. Verifying:"
Get-ScheduledTask -TaskName "INDEX-LAB Email Agent"   | Select-Object TaskName, State
Get-ScheduledTask -TaskName "INDEX-LAB Excel Agent"   | Select-Object TaskName, State
Get-ScheduledTask -TaskName "INDEX-LAB Metrics Agent" | Select-Object TaskName, State
Write-Host ""
Write-Host "All three tasks will run weekly whether you're logged in or not."
Write-Host "  - Email   Mon 11:30 PM"
Write-Host "  - Excel   Mon 11:35 PM"
Write-Host "  - Metrics Tue 5:00 PM"
