param([Parameter(Mandatory)][string]$EnvRoot,
      [Parameter(Mandatory)][string]$PluginRoot,
      [switch]$DryRun)
$ErrorActionPreference = 'Stop'
$manifest = (Get-Content (Join-Path $PluginRoot 'deploy\tasks.manifest.json') -Raw | ConvertFrom-Json).tasks |
  Where-Object { $_.substrate -eq 'native' -and $_.enabled }
foreach ($t in $manifest) {
  $parts = $t.cron -split ' '   # min hour dom mon dow
  $runner = Join-Path $PluginRoot 'deploy\windows\run-task.ps1'
  $vbs    = Join-Path $PluginRoot 'deploy\windows\run-hidden.vbs'
  $arg    = """$vbs"" ""$runner"" ""-TaskId"" ""$($t.id)"" ""-EnvRoot"" ""$EnvRoot"" ""-PluginRoot"" ""$PluginRoot"""
  $time   = "{0:D2}:{1:D2}" -f [int]$parts[1], [int]$parts[0]
  if ($parts[4] -ne '*') {
    $dow = @('Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday')[[int]$parts[4]]
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dow -At $time
  } else {
    $trigger = New-ScheduledTaskTrigger -Daily -At $time
  }
  $action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument $arg
  $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun
  if ($DryRun) { Write-Output "WOULD register 'AIOS $($t.id)' at $time ($($t.cron))"; continue }
  Register-ScheduledTask -TaskName "AIOS $($t.id)" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
  Write-Output "registered 'AIOS $($t.id)' at $time"
}
