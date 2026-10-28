# 在 Linux 容器里运行 Harness²（vendor/harness2）。
#
# 为什么需要容器：Harness² 的 ledger 用了 fcntl，Windows 上直接 import 就报
# ModuleNotFoundError: No module named 'fcntl'。它在 python:3.13-slim 里跑正常
# （它的 pyproject 要求 >=3.12,<3.14，本机是 3.14，即便有 fcntl 也超出范围）。
#
#   .\harness2.ps1                 # 跑自带 demo
#   .\harness2.ps1 -K 3            # 指定分支数
#   .\harness2.ps1 -Module harness2.cli.run_sweep -Args '--help'
#
# 产物写到 work\_h2\<时间戳>\。

param(
    [string]$Module = 'harness2.cli.demo',
    [int]$K = 2,
    [string[]]$Args = @(),
    [string]$Image = 'python:3.13-slim'
)

$ErrorActionPreference = 'Stop'
$h2  = 'D:\start\ds-harness\evolution-stack\vendor\harness2'
$out = Join-Path 'D:\start\ds-harness\work\_h2' (Get-Date -Format 'yyyyMMdd-HHmmss')
New-Item -ItemType Directory -Force -Path $out | Out-Null

$inner = @('-m', $Module)
if ($Module -eq 'harness2.cli.demo') { $inner += @('--output', '/out', '--k', "$K") }
if ($Args.Count -gt 0) { $inner += $Args }

Write-Host "image : $Image"
Write-Host "module: $Module"
Write-Host "out   : $out"

# Windows 路径要转成容器内路径
$cmd = "docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v `"$h2`":/app -v `"$out`":/out -w /app $Image python " + ($inner -join ' ')
Write-Host "run   : $cmd"
& cmd /c $cmd
$code = $LASTEXITCODE
Write-Host "exit  : $code"
if ($code -eq 0) { Write-Host "done  : $out" }
exit $code
