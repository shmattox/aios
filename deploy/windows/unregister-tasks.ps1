param([switch]$WhatIfMode)
Get-ScheduledTask -TaskName 'AIOS *' -ErrorAction SilentlyContinue | ForEach-Object {
  if ($WhatIfMode) { Write-Output "WOULD unregister $($_.TaskName)" }
  else { Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false; Write-Output "unregistered $($_.TaskName)" }
}
