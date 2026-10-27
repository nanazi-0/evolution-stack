param(
    [string]$TokenFile = 'D:\start\ds-harness\work\.secrets\gh-token.txt',
    [string]$KeyFile   = 'D:\start\ds-harness\evolution-stack\control\.ssh\id_ed25519.pub'
)

# 用设备流临时令牌完成 GitHub 侧的全部准备：建仓库、装公钥、推送。
# 令牌只从文件读取，绝不打印。

$ErrorActionPreference = 'Stop'
$root = 'D:\start\ds-harness\evolution-stack'
if (-not (Test-Path $TokenFile)) { Write-Output 'ABORT: token file missing'; exit 1 }
$tok = ([IO.File]::ReadAllText($TokenFile)).Trim()
$hdr = @("-H", "Authorization: Bearer $tok", "-H", "Accept: application/vnd.github+json", "-H", "User-Agent: dsh-evolution-stack")

function Api($method, $path, $jsonBody) {
    $args = @('-s', '--max-time', '30', '-X', $method) + $hdr + @("https://api.github.com$path")
    if ($jsonBody) { $args = $args[0..($args.Count-1)] + @('-H', 'Content-Type: application/json', '-d', $jsonBody) }
    $out = & cmd /c ("curl " + ($args -join ' ') + " 2>nul")
    return $out
}

Write-Host '=== 1. 校验令牌 ==='
$me = Api 'GET' '/user' $null | ConvertFrom-Json
if (-not $me.login) { Write-Output 'ABORT: token invalid'; exit 1 }
$user = $me.login
Write-Host "  登录身份: $user"

Write-Host '=== 2. 建仓库（已存在则跳过）==='
foreach ($n in 'agent-evolution-platform','agent-quality-security') {
    $body = "{`"name`":`"$n`",`"private`":false,`"auto_init`":false,`"description`":`"$n (governed RSI stack component)`"}"
    $res = Api 'POST' '/user/repos' $body | ConvertFrom-Json
    if ($res.full_name) { Write-Host "  created: $($res.full_name)" }
    elseif ($res.errors) { Write-Host "  exists or refused: $n -> $($res.errors[0].message)" }
    else { Write-Host "  unknown: $n" }
}

Write-Host '=== 3. 装 SSH 公钥 ==='
$pub = ([IO.File]::ReadAllText($KeyFile)).Trim()
$body = "{`"title`":`"dsh-evolution-stack`",`"key`":`"$pub`"}"
$res = Api 'POST' '/user/keys' $body | ConvertFrom-Json
if ($res.id) { Write-Host "  key id=$($res.id) installed" }
elseif ($res.errors) { Write-Host "  refused: $($res.errors[0].message)" }
else { Write-Host '  unknown response' }

Write-Host '=== 4. 推送 ==='
Start-Sleep -Seconds 3   # 等 GitHub 侧生效
foreach ($n in 'agent-evolution-platform','agent-quality-security') {
    $d = Join-Path $root "local\$n"
    $url = "git@github.com:$user/$n.git"
    $has = (git -C $d remote 2>$null) -contains 'origin'
    if ($has) { git -C $d remote set-url origin $url } else { git -C $d remote add origin $url }
    git -C $d branch -M main 2>$null
    git -C $d push -u origin main 2>&1 | Select-Object -Last 3
    if ($LASTEXITCODE -eq 0) { Write-Host "  pushed: $url" } else { Write-Host "  FAILED: $n (exit $LASTEXITCODE)" }
}

Write-Host ''
Write-Host "USER=$user"
