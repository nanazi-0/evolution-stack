# 启动/重启 evolution-stack 依赖的本地服务。
#
# 默认行为：已在运行的不动（避免打断已建好的向量索引），只补没起的。
#
#   .\start-services.ps1                # 补齐缺的服务
#   .\start-services.ps1 -Restart       # 强制重启两个
#   .\start-services.ps1 -RestartEmbedding
#   .\start-services.ps1 -SkipEmbedding -SkipOpenViking
#   .\start-services.ps1 -Status        # 只看状态，不动任何东西
#
# 配置在 control\services.toml（端口、模型、autostart、restart_always）。

param(
    [switch]$Restart,
    [switch]$RestartEmbedding,
    [switch]$RestartOpenViking,
    [switch]$SkipEmbedding,
    [switch]$SkipOpenViking,
    [switch]$Status
)

$ErrorActionPreference = 'Continue'
$stack  = 'D:\start\ds-harness\evolution-stack'
$work   = 'D:\start\ds-harness\work'
$ovdir  = Join-Path $work '_openviking'
$py     = 'D:\cpython3.14\python.exe'
$conf   = Join-Path $stack 'control\services.toml'

# --- 读配置（极简 TOML 解析：只认 key = value）---
$cfg = @{}
$section = ''
foreach ($line in (Get-Content $conf -Encoding UTF8 -ErrorAction SilentlyContinue)) {
    $l = $line.Trim()
    if ($l -match '^\[(.+)\]$') { $section = $matches[1]; continue }
    if ($l -match '^([A-Za-z_]+)\s*=\s*(.+)$') {
        $k = $matches[1]; $v = $matches[2].Trim().Trim('"')
        $cfg["$section.$k"] = $v
    }
}
function Get-Cfg([string]$key, [string]$fallback) {
    if ($cfg.ContainsKey($key) -and $cfg[$key]) { return [string]$cfg[$key] }
    return $fallback
}
$embPort  = [int](Get-Cfg 'embedding.port' '18080')
$ovPort   = [int](Get-Cfg 'openviking.port' '1933')
$embAuto  = (Get-Cfg 'embedding.autostart' 'true') -eq 'true'
$ovAuto   = (Get-Cfg 'openviking.autostart' 'true') -eq 'true'
$embForce = (Get-Cfg 'embedding.restart_always' 'false') -eq 'true'
$ovForce  = (Get-Cfg 'openviking.restart_always' 'false') -eq 'true'

function Test-Url([string]$url) {
    try { return (Invoke-WebRequest -Uri $url -TimeoutSec 3 -UseBasicParsing).StatusCode -eq 200 } catch { return $false }
}

Write-Host "=== 服务状态 ==="
Write-Host ("embedding  {0}:{1}  {2}" -f '127.0.0.1', $embPort, $(if (Test-Url "http://127.0.0.1:$embPort/health") { 'UP' } else { 'DOWN' }))
Write-Host ("openviking {0}:{1}  {2}" -f '127.0.0.1', $ovPort, $(if (Test-Url "http://127.0.0.1:$ovPort/health") { 'UP' } else { 'DOWN' }))
if ($Status) { exit 0 }

# --- embedding ---
if ($SkipEmbedding) {
    Write-Host '[skip] embedding（命令行指定跳过）'
} elseif (-not $embAuto -and -not ($Restart -or $RestartEmbedding)) {
    Write-Host "[skip] embedding（services.toml 里 autostart=false）"
} else {
    $up = Test-Url "http://127.0.0.1:$embPort/health"
    $force = $Restart -or $RestartEmbedding -or $embForce
    if ($up -and -not $force) {
        Write-Host '[keep] embedding 已在运行，不重启（要重启加 -RestartEmbedding）'
    } else {
        if ($up) {
            Write-Host '[stop] 结束旧 embedding 进程'
            Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -like '*embeddings_server.py*' } |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
            Start-Sleep -Seconds 2
        }
        Write-Host '[start] embedding'
        $env:HF_ENDPOINT = 'https://hf-mirror.com'
        $env:EMBED_MODEL = [string]$cfg['embedding.model']
        $env:TMP = "$work\_tmp"; $env:TEMP = "$work\_tmp"
        New-Item -ItemType Directory -Force -Path "$work\_tmp" | Out-Null
        Start-Process -FilePath $py `
            -ArgumentList @("$stack\control\adapters\embeddings_server.py", '--port', "$embPort") `
            -WindowStyle Hidden -RedirectStandardOutput "$work\_tmp\emb.log" -RedirectStandardError "$work\_tmp\emb.err.log"
        for ($i = 0; $i -lt 15; $i++) {
            Start-Sleep -Seconds 2
            if (Test-Url "http://127.0.0.1:$embPort/health") { Write-Host '[ok] embedding UP'; break }
        }
    }
}

# --- openviking ---
if ($SkipOpenViking) {
    Write-Host '[skip] openviking（命令行指定跳过）'
} elseif (-not $ovAuto -and -not ($Restart -or $RestartOpenViking)) {
    Write-Host "[skip] openviking（services.toml 里 autostart=false）"
} else {
    $up = Test-Url "http://127.0.0.1:$ovPort/health"
    $force = $Restart -or $RestartOpenViking -or $ovForce
    if ($up -and -not $force) {
        Write-Host '[keep] openviking 已在运行，不重启（要重启加 -RestartOpenViking）'
    } else {
        if (-not (Test-Path "$ovdir\ov.conf")) { Write-Host '[abort] 缺少 ov.conf'; exit 1 }
        docker rm -f ([string]$cfg['openviking.container']) 2>$null | Out-Null
        Write-Host '[start] openviking'
        docker run -d --name ([string]$cfg['openviking.container']) -e OPENVIKING_WITH_BOT=0 `
            -p "${ovPort}:${ovPort}" -v "${ovdir}:/app/.openviking" ([string]$cfg['openviking.image']) | Out-Null
        for ($i = 0; $i -lt 20; $i++) {
            Start-Sleep -Seconds 3
            if (Test-Url "http://127.0.0.1:$ovPort/health") { Write-Host '[ok] openviking UP'; break }
        }
    }
}

Write-Host ''
Write-Host ("embedding  {0}" -f $(if (Test-Url "http://127.0.0.1:$embPort/health") { 'UP' } else { 'DOWN' }))
Write-Host ("openviking {0}" -f $(if (Test-Url "http://127.0.0.1:$ovPort/health") { 'UP' } else { 'DOWN' }))
