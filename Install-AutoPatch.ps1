param(
    [string]$PythonExe = (Join-Path $PSScriptRoot 'runtime\python\python.exe'),
    [string]$TaskName = 'SnowVPN AutoPatch'
)
$ErrorActionPreference = 'Stop'
$taskName = $TaskName
$patcherRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$scriptFile = Join-Path $patcherRoot 'autopatch.py'
$pythonWindowless = Join-Path (Split-Path -Parent $PythonExe) 'pythonw.exe'
if (!(Test-Path -LiteralPath $PythonExe) -or !(Test-Path -LiteralPath $pythonWindowless)) {
    throw 'Python and pythonw were not found. Supply -PythonExe with the installed Python path.'
}
# Apply the checked patch to configured installations before creating the watcher.
& $PythonExe -X utf8 $scriptFile
if ($LASTEXITCODE -ne 0) { throw 'Patch failed. Scheduled task has not been installed.' }
$stopFile = Join-Path $patcherRoot 'state\stop.request'
if (Test-Path -LiteralPath $stopFile) { Remove-Item -LiteralPath $stopFile }
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonWindowless -Argument ('-X utf8 "' + $scriptFile + '" --watch') -WorkingDirectory $patcherRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Reapply local SnowVPN subscription patch after client resource updates. Checks configured directories every 10 seconds. Does not stop the VPN.' -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
Write-Output ('Patch logs: ' + (Join-Path $patcherRoot 'autopatch.log'))
