param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'SnowAutoPatch'),
    [string[]]$Target,
    [string]$SourceDirectory,
    [string]$TaskName = 'SnowVPN AutoPatch',
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($env:OS -ne 'Windows_NT' -or ![Environment]::Is64BitOperatingSystem) {
    throw 'Windows 10/11 x64 is required.'
}
if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') {
    throw 'ARM64 is not supported by this installer.'
}
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$installRoot = [IO.Path]::GetFullPath($InstallDir)
New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
$staging = Join-Path $installRoot ('.install-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $staging | Out-Null

function Get-VerifiedArchive($Spec, $Destination) {
    Invoke-WebRequest -UseBasicParsing -Uri $Spec.url -OutFile $Destination
    if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -ne $Spec.sha256) {
        throw ('Archive checksum mismatch: ' + $Spec.url)
    }
}

function Wait-Watcher($Root) {
    $state = Join-Path $Root 'state'
    New-Item -ItemType Directory -Path $state -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $state 'stop.request'), 'stop')
    & (Join-Path $installRoot 'runtime\python\python.exe') -X utf8 -c "import ctypes, hashlib, sys, time; k=ctypes.WinDLL('kernel32',use_last_error=True); k.CreateMutexW.restype=ctypes.c_void_p; name='Local\\SnowVPN-AutoPatch-'+hashlib.sha256(sys.argv[1].casefold().encode()).hexdigest()[:16]; deadline=time.time()+35; busy=True
while time.time()<deadline:
 handle=k.CreateMutexW(None,False,name); busy=ctypes.get_last_error()==183; k.CloseHandle(ctypes.c_void_p(handle))
 if not busy: break
 time.sleep(1)
sys.exit(1 if busy else 0)" $Root
    if ($LASTEXITCODE -ne 0) { throw 'Previous watcher did not finish; try installation again.' }
}

$stoppedRoots = @()
$previousTask = $null
$installed = $false
try {
    if ($SourceDirectory) {
        $source = (Resolve-Path -LiteralPath $SourceDirectory).Path
    } else {
        Write-Host 'Downloading SnowAutoPatch...'
        $archive = Join-Path $staging 'source.zip'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://codeload.github.com/Onarous/SnowAutoPatch/zip/refs/heads/main' -OutFile $archive
        Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $staging 'source')
        $source = Join-Path $staging 'source\SnowAutoPatch-main'
    }
    $specs = Get-Content -LiteralPath (Join-Path $source 'runtimes.json') -Raw | ConvertFrom-Json
    foreach ($kind in @('python', 'node')) {
        $runtimeRoot = Join-Path $installRoot ('runtime\' + $kind)
        $versionFile = Join-Path $runtimeRoot 'version.txt'
        $exe = Join-Path $runtimeRoot ($kind + '.exe')
        if ((Test-Path -LiteralPath $exe) -and (Test-Path -LiteralPath $versionFile) -and
            (Get-Content -LiteralPath $versionFile -Raw).Trim() -eq $specs.$kind.version) { continue }
        Write-Host ('Downloading private ' + $kind + ' runtime...')
        $archive = Join-Path $staging ($kind + '.zip')
        Get-VerifiedArchive $specs.$kind $archive
        $expanded = Join-Path $staging $kind
        if ($kind -eq 'node') {
            # Only node.exe is needed for syntax checks; npm adds thousands of files.
            New-Item -ItemType Directory -Path $expanded | Out-Null
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            $zip = [IO.Compression.ZipFile]::OpenRead($archive)
            try {
                foreach ($name in @('node.exe', 'LICENSE')) {
                    $entry = $zip.GetEntry('node-v' + $specs.node.version + '-win-x64/' + $name)
                    if (!$entry) { throw ('Node archive is missing ' + $name) }
                    [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $expanded $name))
                }
            } finally { $zip.Dispose() }
        } else {
            Expand-Archive -LiteralPath $archive -DestinationPath $expanded
        }
        New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
        Copy-Item -Path (Join-Path $expanded '*') -Destination $runtimeRoot -Recurse -Force
        [IO.File]::WriteAllText($versionFile, $specs.$kind.version)
    }
    $python = Join-Path $installRoot 'runtime\python\python.exe'
    # Also migrate an earlier installation using the same named task.
    if (!$CheckOnly) {
        $previousTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($previousTask) {
            $oldRoot = $previousTask.Actions[0].WorkingDirectory
            if (!$oldRoot -or $previousTask.Actions[0].Arguments -notlike '*autopatch.py*' -or
                !(Test-Path -LiteralPath (Join-Path $oldRoot 'autopatch.py'))) {
                throw 'The task name belongs to another program. Choose a different -TaskName.'
            }
            $oldRoot = [IO.Path]::GetFullPath($oldRoot)
            $stoppedRoots += $oldRoot
            Wait-Watcher $oldRoot
        }
        if ($stoppedRoots -notcontains $installRoot) {
            $stoppedRoots += $installRoot
            Wait-Watcher $installRoot
        }
    }
    foreach ($file in @('autopatch.py', 'Install-AutoPatch.ps1', 'Disable-AutoPatch.ps1', 'Patch-Now.cmd', 'README.md', 'runtimes.json')) {
        Copy-Item -LiteralPath (Join-Path $source $file) -Destination (Join-Path $installRoot $file) -Force
    }
    $payloadRoot = Join-Path $installRoot 'payload'
    New-Item -ItemType Directory -Path $payloadRoot -Force | Out-Null
    foreach ($file in @('subscriptionFormat.js', 'fetch-wrapper.js')) {
        Copy-Item -LiteralPath (Join-Path $source ('payload\' + $file)) -Destination (Join-Path $payloadRoot $file) -Force
    }
    $configFile = Join-Path $installRoot 'config.json'
    if ($Target -or !(Test-Path -LiteralPath $configFile)) {
        $targets = if ($Target) { @($Target | ForEach-Object {[IO.Path]::GetFullPath($_)}) } else { @(Join-Path $env:LOCALAPPDATA 'Programs\snowvpn-next') }
        [IO.File]::WriteAllText($configFile, (@{targets=@($targets)} | ConvertTo-Json -Depth 3), (New-Object Text.UTF8Encoding($false)))
    }
    if ($CheckOnly) {
        & $python -X utf8 (Join-Path $installRoot 'autopatch.py') --check
        if ($LASTEXITCODE -ne 0) { throw 'Compatibility check failed. No client files were changed.' }
        Write-Host ('Check complete. No scheduled task was created. Tools: ' + $installRoot)
    } else {
        & (Join-Path $installRoot 'Install-AutoPatch.ps1') -PythonExe $python -TaskName $TaskName
        $installed = $true
        Write-Host ('Installed. Tools and backups: ' + $installRoot)
        Write-Host 'If SnowVPN is already running, exit it through the tray and start it again.'
    }
} finally {
    if (!$installed -and !$CheckOnly) {
        foreach ($root in $stoppedRoots) {
            $stopFile = Join-Path $root 'state\stop.request'
            if (Test-Path -LiteralPath $stopFile) { Remove-Item -LiteralPath $stopFile -Force }
        }
        if ($previousTask -and $previousTask.State -eq 'Running') {
            Start-ScheduledTask -TaskName $TaskName -ErrorAction Continue
        }
    }
    $verifiedStaging = [IO.Path]::GetFullPath($staging)
    if ((Split-Path -Parent $verifiedStaging) -ne $installRoot -or (Split-Path -Leaf $verifiedStaging) -notmatch '^\.install-[a-f0-9]{32}$') {
        throw 'Unexpected staging cleanup path'
    }
    if (Test-Path -LiteralPath $verifiedStaging) { Remove-Item -LiteralPath $verifiedStaging -Recurse -Force }
}
