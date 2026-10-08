param([string]$TaskName = 'SnowVPN AutoPatch')
$ErrorActionPreference = 'Stop'
$stateRoot = Join-Path $PSScriptRoot 'state'
New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $stateRoot 'stop.request'), 'stop')
# The watcher finishes any in-progress patch and exits at its next 10-second poll.
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
Write-Output 'Automatic patching disabled. Backups and the applied client patch are retained.'
