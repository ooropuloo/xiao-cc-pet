# Register the two daily evolve jobs (04:00 survey, 09:30 ask) for the current user.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$vbs = Join-Path $here 'run-hidden.vbs'
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
foreach ($j in @(@{n='xiao-evolve-survey'; job='survey'; at='04:00'}, @{n='xiao-evolve-ask'; job='ask'; at='09:30'})) {
  $act = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"{0}" {1}' -f $vbs, $j.job) -WorkingDirectory $here
  $trg = New-ScheduledTaskTrigger -Daily -At $j.at
  Register-ScheduledTask -TaskName $j.n -Action $act -Trigger $trg -Settings $set -Description "XIAO 小圓盤每日自我進化 ($($j.job))" -Force | Out-Null
  Get-ScheduledTask -TaskName $j.n | Select-Object TaskName, State, @{n='Next';e={(Get-ScheduledTaskInfo $_).NextRunTime}}
}
