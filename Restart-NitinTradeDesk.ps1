$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path.TrimEnd('\')
$statusUrl = 'http://127.0.0.1:8081/api/app/status'
$getListenerPid = {
    $line = & netstat.exe -ano -p tcp | Select-String '^\s*TCP\s+127\.0\.0\.1:8081\s+.*\s+LISTENING\s+(\d+)\s*$' | Select-Object -First 1
    if ($line -and $line.Line -match 'LISTENING\s+(\d+)\s*$') { return [int]$Matches[1] }
    return 0
}
$listenerPid = & $getListenerPid

if ($listenerPid) {
    try {
        $status = Invoke-RestMethod -Uri $statusUrl -TimeoutSec 3
    } catch {
        throw 'Port 8081 is occupied by a process that is not verified as this Deep Trading Desk workspace. It was left untouched.'
    }

    $statusRoot = [System.IO.Path]::GetFullPath([string]$status.workspace).TrimEnd('\')
    if ($status.application -ne 'DeepTradingDesk' -or $statusRoot -ine $root) {
        throw 'Port 8081 belongs to a different application or workspace. It was left untouched.'
    }

    $versionMatch = [regex]::Match((Get-Content -LiteralPath (Join-Path $root 'app_version.py') -Raw), 'APP_VERSION\s*=\s*(\d+)')
    if (-not $versionMatch.Success) { throw 'Could not determine the local application version.' }
    $requiredVersion = [int]$versionMatch.Groups[1].Value

    if ([int]$status.backendVersion -lt $requiredVersion) {
        Stop-Process -Id $listenerPid -Force
        $deadline = (Get-Date).AddSeconds(15)
        do {
            Start-Sleep -Milliseconds 250
            $listenerPid = & $getListenerPid
        } while ($listenerPid -and (Get-Date) -lt $deadline)
        if ($listenerPid) { throw 'The old Deep Trading Desk server did not release port 8081.' }
    }
}

& python (Join-Path $root 'launch.py')
if ($LASTEXITCODE -ne 0) { throw 'Deep Trading Desk did not start successfully.' }
