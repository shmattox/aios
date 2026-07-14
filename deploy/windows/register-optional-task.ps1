# Register a single OPTIONAL (enabled:false) native task by id — the supported opt-in path.
# The shipped manifest keeps optional tasks (e.g. aios-brief-cache) enabled:false so
# register-tasks.ps1 skips them by default; this registers one on demand WITHOUT editing
# the manifest, so the product default stays opt-in. Mirrors register-tasks.ps1's action/
# trigger construction exactly so an opted-in task is identical to the always-on five.
#
#   powershell -File deploy\windows\register-optional-task.ps1 `
#     -TaskId aios-brief-cache -EnvRoot "<env_root>" -PluginRoot "<plugin_root>" [-DryRun]
#
# Reversible: Unregister-ScheduledTask -TaskName 'AIOS <id>'.
param([Parameter(Mandatory)][string]$TaskId,
      [Parameter(Mandatory)][string]$EnvRoot,
      [Parameter(Mandatory)][string]$PluginRoot,
      [switch]$DryRun)
$ErrorActionPreference = 'Stop'
$t = (Get-Content (Join-Path $PluginRoot 'deploy\tasks.manifest.json') -Raw | ConvertFrom-Json).tasks |
  Where-Object { $_.id -eq $TaskId }
if (-not $t) { throw "unknown task id '$TaskId'" }
if ($t.substrate -ne 'native') { throw "'$TaskId' substrate is '$($t.substrate)'; only native tasks register here" }
$parts   = $t.cron -split ' '   # min hour dom mon dow
$runner  = Join-Path $PluginRoot 'deploy\windows\run-task.ps1'
$vbs     = Join-Path $PluginRoot 'deploy\windows\run-hidden.vbs'
$arg     = """$vbs"" ""$runner"" ""-TaskId"" ""$($t.id)"" ""-EnvRoot"" ""$EnvRoot"" ""-PluginRoot"" ""$PluginRoot"""
$time    = "{0:D2}:{1:D2}" -f [int]$parts[1], [int]$parts[0]
if ($parts[4] -ne '*') {
  $dow     = @('Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday')[[int]$parts[4]]
  $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dow -At $time
} else {
  $trigger = New-ScheduledTaskTrigger -Daily -At $time
}
$action   = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument $arg
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun
if ($DryRun) { Write-Output "WOULD register 'AIOS $($t.id)' at $time ($($t.cron)) [manifest enabled=$($t.enabled), opt-in]"; return }
Register-ScheduledTask -TaskName "AIOS $($t.id)" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Output "registered 'AIOS $($t.id)' at $time (opt-in; manifest enabled=$($t.enabled) unchanged)"
