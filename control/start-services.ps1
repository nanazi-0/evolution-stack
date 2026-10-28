# 启动 evolution-stack 依赖的两个本地服务：
#   1) 本地 embedding 服务（OpenViking 需要 embedding API，本机没有服务商）
#   2) OpenViking 容器（端口 1933，api_key 模式）
#
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File control\start-services.ps1
#
# 停止：docker rm -f ov-evolution；再结束 embedding 的 python 进程。

$ErrorActionPreference = 'Continue'
$stack = 'D:\start\ds-harness\evolution-stack'
$work  = 'D:\start\ds-harness\work'
$ovdir = Join-Path $work '_openviking'
$py    = 'D:\cpython3.14\python.exe'

# --- 1. embedding 服务 ---
$embRunning = $false
try { $embRunning = (Invoke-WebRequest -Uri 'http://127.0.0.1:18080/health' -TimeoutSec 3 -UseBasicParsing).StatusCode -eq 200 } catch { }
if ($embRunning) {
    Write-Host '[skip] embedding 服务已在 18080'
} else {
    Write-Host '[start] embedding 服务 18080'
    $env:HF_ENDPOINT = 'https://hf-mirror.com'
    $env:EMBED_MODEL = 'BAAI/bge-small-zh-v1.5'
    $env:TMP = "$work\_tmp"; $env:TEMP = "$work\_tmp"
    Start-Process -FilePath $py -ArgumentList @("$stack\control\adapters\embeddings_server.py", '--port', '18080') `
        -WindowStyle Hidden -RedirectStandardOutput "$work\_tmp\emb.log" -RedirectStandardError "$work\_tmp\emb.err.log"
    Start-Sleep -Seconds 8
}

# --- 2. OpenViking ---
$exists = (docker ps -a --filter name=^ov-evolution$ --format '{{.Names}}') 2>$null
if ($exists) {
    Write-Host '[restart] OpenViking'
    docker rm -f ov-evolution | Out-Null
}
if (-not (Test-Path "$ovdir\ov.conf")) {
    Write-Host '[abort] 缺少 ov.conf，先跑一次 docker run ... openviking-server init'
    exit 1
}
Write-Host '[start] OpenViking 1933'
docker run -d --name ov-evolution -e OPENVIKING_WITH_BOT=0 -p 1933:1933 -v "${ovdir}:/app/.openviking" `
    openviking-cn-beijing.cr.volces.com/volcengine/openviking:latest | Out-Null

for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 3
    try {
        $code = (Invoke-WebRequest -Uri 'http://127.0.0.1:1933/health' -TimeoutSec 3 -UseBasicParsing).StatusCode
        if ($code -eq 200) { Write-Host '[ok] OpenViking healthy'; break }
    } catch { }
}

Write-Host ''
Write-Host 'user key: ' (Get-Content "$ovdir\.user_key" -ErrorAction SilentlyContinue)
Write-Host 'health :' (try { (Invoke-WebRequest -Uri 'http://127.0.0.1:1933/health' -UseBasicParsing -TimeoutSec 3).StatusCode } catch { 'down' })
