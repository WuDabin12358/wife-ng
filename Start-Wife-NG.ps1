[CmdletBinding()]
param(
    [string]$Server = '127.0.0.1:25565',
    [switch]$NoDeepSeek,
    [switch]$OpMode,
    [switch]$CreativeBuild
)

$ErrorActionPreference = 'Stop'
$wifeRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$wrapper = Join-Path $wifeRoot 'headlessmc\headlessmc-launcher-wrapper.jar'
$java = $env:WIFE_NG_HEADLESS_JAVA
if (-not $java -and $env:JAVA_HOME) { $java = Join-Path $env:JAVA_HOME 'bin\java.exe' }
if (-not $java) { $java = Join-Path $wifeRoot '.runtime\jdk21\jdk-21.0.12.1+1\bin\java.exe' }
$profile = if ($env:WIFE_NG_HEADLESS_PROFILE) { $env:WIFE_NG_HEADLESS_PROFILE } else { 'fabric-loader-0.19.3-1.16.5' }
$botName = if ($env:WIFE_NG_BOT_NAME) { $env:WIFE_NG_BOT_NAME } else { 'wife' }
$apiBase = if ($env:WIFE_NG_API) { $env:WIFE_NG_API.TrimEnd('/') } else { 'http://127.0.0.1:8766' }
$apiHeaders = @{}
if ($env:WIFE_NG_TOKEN) { $apiHeaders.Authorization = "Bearer $env:WIFE_NG_TOKEN" }
$agent = Join-Path $wifeRoot 'WifeAgent.py'
$agentLog = Join-Path $wifeRoot 'headlessmc\wife-agent.log'
$agentErrorLog = Join-Path $wifeRoot 'headlessmc\wife-agent-error.log'
$worldId = $Server -replace '[^a-zA-Z0-9_.-]', '_'
if ($Server -match '^(127\.0\.0\.1|localhost):25565$') {
    $propertiesPath = Join-Path (Split-Path -Parent $wifeRoot) 'Local-Test-Server-1.16.5\server.properties'
    if (Test-Path -LiteralPath $propertiesPath) {
        $levelLine = Get-Content -LiteralPath $propertiesPath | Where-Object { $_ -match '^level-name=' } | Select-Object -First 1
        if ($levelLine) { $worldId += '-' + ($levelLine.Substring(11) -replace '[^a-zA-Z0-9_.-]', '_') }
    }
}
$env:WIFE_NG_WORLD_ID = $worldId
if ($CreativeBuild) { $OpMode = $true }

foreach ($required in @($wrapper, $java, $agent)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Missing runtime file: $required"
    }
}

function Test-WifeApi {
    try {
        $state = Invoke-RestMethod -Uri "$apiBase/v1/state" -Headers $apiHeaders -TimeoutSec 2
        return $null -ne $state
    }
    catch {
        return $false
    }
}

function Find-Python {
    $configured = [Environment]::GetEnvironmentVariable('WIFE_NG_PYTHON')
    if ($configured -and (Test-Path -LiteralPath $configured)) {
        return $configured
    }
    $codexPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $codexPython) {
        return $codexPython
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) {
        return $command.Source
    }
    return $null
}

if (Test-WifeApi) {
    throw 'Wife NG is already listening on 127.0.0.1:8766.'
}

$startInfo = [Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $java
$startInfo.Arguments = "-jar `"$wrapper`""
$startInfo.WorkingDirectory = Join-Path $wifeRoot 'headlessmc'
$startInfo.UseShellExecute = $false
$startInfo.RedirectStandardInput = $true
$startInfo.CreateNoWindow = $false
$startInfo.EnvironmentVariables['WIFE_NG_SERVER'] = $Server
$startInfo.EnvironmentVariables['WIFE_NG_OP_MODE'] = $(if ($OpMode) { 'true' } else { 'false' })
$startInfo.EnvironmentVariables['WIFE_NG_WORLD_ID'] = $worldId

$headless = [Diagnostics.Process]::new()
$headless.StartInfo = $startInfo
$headlessStarted = $false
$planner = $null

try {
    $modeLabel = if ($OpMode) { 'OP mode' } else { 'standard mode' }
    Write-Host "Starting Wife NG headless client for $Server ($modeLabel) ..." -ForegroundColor Cyan
    if (-not $headless.Start()) {
        throw 'HeadlessMC failed to start.'
    }
    $headlessStarted = $true
    Start-Sleep -Seconds 2
    $headless.StandardInput.WriteLine("launch $profile -lwjgl -paulscode -jndi")
    $headless.StandardInput.Flush()

    $deadline = [DateTime]::UtcNow.AddMinutes(3)
    while (-not (Test-WifeApi)) {
        if ($headless.HasExited) {
            throw "HeadlessMC exited early with code $($headless.ExitCode)."
        }
        if ([DateTime]::UtcNow -ge $deadline) {
            throw 'Timed out waiting for Wife NG API. Check headlessmc\game\logs\latest.log.'
        }
        Start-Sleep -Seconds 2
    }

    Write-Host 'Wife NG body and control API are ready.' -ForegroundColor Green
    if ($CreativeBuild) {
        $creativeDeadline = [DateTime]::UtcNow.AddMinutes(2)
        do {
            $state = Invoke-RestMethod -Uri "$apiBase/v1/state" -Headers $apiHeaders -TimeoutSec 3
            if ($state.connected) { break }
            if ([DateTime]::UtcNow -ge $creativeDeadline) { throw 'Creative build startup timed out waiting for a world.' }
            Start-Sleep -Seconds 2
        } while ($true)
        $body = @{tool='server_command'; arguments=@{command="gamemode creative $botName"}} | ConvertTo-Json -Depth 3
        $null = Invoke-RestMethod -Uri 'http://127.0.0.1:8766/v1/tools' -Method Post -ContentType 'application/json' -Body $body
        do {
            Start-Sleep -Seconds 1
            $state = Invoke-RestMethod -Uri "$apiBase/v1/state" -Headers $apiHeaders -TimeoutSec 3
            if ([DateTime]::UtcNow -ge $creativeDeadline) { throw 'Creative mode was not confirmed; check server OP permissions.' }
        } while (-not $state.self.creative)
        Write-Host "Creative building ready in world $worldId." -ForegroundColor Green
    }
    if (-not $NoDeepSeek) {
        $python = Find-Python
        if (-not $python) {
            throw 'Python was not found. Install Python 3 or set WIFE_NG_PYTHON.'
        }
        $planner = Start-Process -FilePath $python -ArgumentList @($agent) `
            -WorkingDirectory $wifeRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $agentLog -RedirectStandardError $agentErrorLog
        Write-Host "DeepSeek planner started (PID $($planner.Id))." -ForegroundColor Green
    }

    Write-Host 'Running. Close this window or press Ctrl+C to stop.' -ForegroundColor Yellow
    $headless.WaitForExit()
}
finally {
    if ($planner -and -not $planner.HasExited) {
        Stop-Process -Id $planner.Id -ErrorAction SilentlyContinue
    }
    if ($headlessStarted -and -not $headless.HasExited) {
        $headless.Kill()
    }
}
