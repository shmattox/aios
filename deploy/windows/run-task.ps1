param([Parameter(Mandatory)][string]$TaskId,
      [Parameter(Mandatory)][string]$EnvRoot,
      [Parameter(Mandatory)][string]$PluginRoot,
      [switch]$ReportOnly)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$manifest = (Get-Content (Join-Path $PluginRoot 'deploy\tasks.manifest.json') -Raw | ConvertFrom-Json).tasks | Where-Object { $_.id -eq $TaskId }
if (-not $manifest) { throw "unknown task id $TaskId" }
$skill  = Join-Path $PluginRoot ($manifest.body_path -replace '/','\')
$logDir = Join-Path $EnvRoot "state\task-logs\$TaskId"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir 'last-run.log'; $out = Join-Path $logDir 'last-result.txt'
$stamp = (Get-Date).ToString('s')
$startEpoch = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command python3 -ErrorAction SilentlyContinue).Source }
# A21: run window floor for the post-run context-log check (120s slack for clock rounding).
$sinceUtc = (Get-Date).ToUniversalTime().AddSeconds(-120).ToString('yyyy-MM-ddTHH:mm:ssZ')

function Complete-Run([string]$resultText) {
  Set-Content -Path $out -Value $resultText -Encoding utf8
  # A21: dated result rotation - last-result.txt alone is overwritten each run, which left a
  # missing context-log line unreconstructable the next day. Keep a short forensic trail.
  $dated = Join-Path $logDir ('last-result-' + (Get-Date).ToString('yyyyMMdd-HHmmss') + '.txt')
  Copy-Item $out $dated -ErrorAction SilentlyContinue
  Get-ChildItem $logDir -Filter 'last-result-*.txt' -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -Skip 10 |
    Remove-Item -Force -ErrorAction SilentlyContinue
  # A21: deterministic post-run context-log check - the model self-reporting "line appended"
  # is not evidence (three live integrity failures). Asks disk: did a record for this stage
  # land inside the run window, and does the tail parse. WARN-only: the task's own exit code
  # stands; the WARN goes loud into last-run.log + the result text notifications surface.
  if ($manifest.context_stages -and -not $ReportOnly) {
    if (-not $py) {
      Add-Content $log "$stamp  ctx-check SKIPPED: python not found"
      return
    }
    $ctxTool = Join-Path $PluginRoot 'engine\tools\context_log.py'
    $ctxLog  = Join-Path $EnvRoot 'state\context-log.jsonl'
    $chk = & $py $ctxTool check --path $ctxLog --stage (@($manifest.context_stages) -join ',') --since $sinceUtc
    if ($LASTEXITCODE -ne 0) {
      $chkLine = (($chk | Out-String).Trim() -replace '\s+', ' ')
      if (-not $chkLine) { $chkLine = "exit=$LASTEXITCODE (no output captured)" }
      Add-Content $log "$stamp  ctx-check $chkLine"
      Add-Content $out "CTX-CHECK: $chkLine" -Encoding utf8
    } else {
      Add-Content $log "$stamp  ctx-check OK"
    }
  }
}
# A25: script-type tasks run a deterministic engine shell directly - no model in the loop.
if ($manifest.type -eq 'script') {
  if (-not $py) { Add-Content $log "$stamp  ERROR: python not found"; exit 1 }
  $script = Join-Path $PluginRoot ($manifest.script -replace '/','\')
  if (-not (Test-Path $script)) { Add-Content $log "$stamp  ERROR: script not found at $script"; exit 1 }
  Set-Location $EnvRoot
  $result = & $py $script --env-root $EnvRoot
  $code = $LASTEXITCODE
  Complete-Run ($result | Out-String)
  # script output is often multi-line JSON - log a compacted single line so the trailer stays readable
  $last = ((($result | Out-String).Trim() -replace '\s+', ' '))
  if ($last.Length -gt 220) { $last = $last.Substring(0, 220) + '...' }
  Add-Content $log "$stamp  exit=$code  $last"
  exit $code
}
$claude = Join-Path $env:USERPROFILE '.local\bin\claude.exe'
if (-not (Test-Path $claude)) { $claude = (Get-Command claude -ErrorAction SilentlyContinue).Source }
if (-not $claude) { Add-Content $log "$stamp  ERROR: claude not found"; exit 1 }
if (-not (Test-Path $skill))  { Add-Content $log "$stamp  ERROR: body not found at $skill"; exit 1 }
$tools = @($manifest.allowed_tools)
if ($ReportOnly -and $manifest.report_only_drops) { $tools = $tools | Where-Object { $manifest.report_only_drops -notcontains $_ } }
# A20: the git line is CONDITIONAL on the task's manifest grants. The old blanket "Do NOT run git."
# contradicted session-capture's read-only git grants - the model obeyed the prompt and reported
# itself "permission-blocked" without ever attempting git (4 consecutive nightlies). Tasks with
# grants get the exact invocation form the prefix-matcher accepts; tasks without keep the ban.
# ASCII ONLY in this block: the file is BOM-less UTF-8 and PS 5.1 decodes it as cp1252, where a
# multibyte dash inside a double-quoted string decodes to a smart quote that TERMINATES the string
# and kills the whole script at parse time (every scheduled task, before any logging).
$gitGrants = @($tools | Where-Object { $_ -like 'Bash(git *' })
$gitLine = if ($gitGrants.Count) {
  "Read-only git is allowed for this task ($($gitGrants -join ', ')): invoke it ONLY as 'cd <repo> && git log ...' / 'cd <repo> && git show ...' - each segment must match a grant; 'git -C <path> ...' matches NO grant and will be denied. NEVER any git write (add/commit/push/checkout/reset/rebase)."
} else {
  'Do NOT run git.'
}
$prompt = @"
You are the aios '$TaskId' stage running UNATTENDED as a scheduled native task (headless claude -p).
Env root: $EnvRoot   Plugin root: $PluginRoot
Read your full instructions from: $skill
Execute them completely NOW. $gitLine Do NOT ask questions or wait for input; follow the
instructions exactly and finish.$(if ($ReportOnly) { ' REPORT-ONLY MODE: propose, never write.' })
"@
Set-Location $EnvRoot
$claudeArgs = @('-p', $prompt, '--output-format', 'text')
if ($tools.Count) { $claudeArgs += @('--allowedTools') + $tools }
# Optional per-task model tier (manifest 'model' field): mechanical stages run a cheaper tier;
# judgment stages (ingest, gate-auto, session-capture) omit the field and inherit the default.
if ($manifest.model) { $claudeArgs += @('--model', $manifest.model) }
# A25: every scheduled model run carries a turn cap - a runaway guard, not the finish line.
if ($manifest.max_turns) { $claudeArgs += @('--max-turns', "$($manifest.max_turns)") }
# A16: mark this as a machine (fleet) run - the session-evidence hook stamps `machine_run: true`
# so session-capture never synthesizes the pipeline's own headless sessions into records. Set only
# around the claude invocation and always cleared: a MANUAL run of this script from an interactive
# terminal must not leave the var behind, or later human sessions in that terminal would be
# stamped machine and silently pruned without a record.
$env:AIOS_MACHINE_RUN = $TaskId
try {
  $result = & $claude @claudeArgs
  $code = $LASTEXITCODE
} finally {
  Remove-Item Env:\AIOS_MACHINE_RUN -ErrorAction SilentlyContinue
}
Complete-Run ($result | Out-String)
$last = (($result | Out-String).TrimEnd() -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 1)
Add-Content $log "$stamp  exit=$code  $last"
exit $code
