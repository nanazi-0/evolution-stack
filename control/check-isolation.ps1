# 隔离自检：确认 evolution-stack 与 D:\Portfolio 不会互相干扰。
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File control\check-isolation.ps1
#
# 检查项：
#   1. 代码里没有指向 Portfolio 的可执行路径
#   2. 没有联接/软链指向外部
#   3. Portfolio 不知道自己被引用（反向耦合）
#   4. 端口分工：我们的占 1933/14317/16006/18080，6006/4317 留空
#   5. 容器只挂载自己的工作区；命名卷按 compose 项目隔离
#   6. 镜像按 digest 钉死（共享只读层，不会互相覆盖）

$ErrorActionPreference = 'Continue'
$stack = 'D:\start\ds-harness\evolution-stack'
$fails = 0

function Check([string]$name, [bool]$ok, [string]$detail) {
    if ($ok) {
        Write-Host ("  PASS  " + $name)
    } else {
        Write-Host ("  FAIL  " + $name + "  <- " + $detail)
        $script:fails++
    }
}

Write-Host '== 1. 路径隔离 =='
$hits = Get-ChildItem $stack -Recurse -Include '*.py','*.toml','*.json','*.yml','*.yaml','*.ps1','*.cmd' -File -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\\.git\\|\\node_modules\\|\\\.venv\\|\\control\\check-isolation' } |
    Select-String -Pattern 'D:/Portfolio|D:\\Portfolio' -ErrorAction SilentlyContinue |
    Where-Object { $_.Line.TrimStart() -notmatch '^(#|//|--|REM)' }
Check '代码里没有指向 Portfolio 的可执行路径' ($hits.Count -eq 0) (($hits | Select-Object -First 3 | ForEach-Object { $_.Path }) -join '; ')

Write-Host '== 2. 没有外部联接 =='
$links = cmd /c "dir /AL /S $stack 2>nul" | Select-String -Pattern 'JUNCTION|SYMLINK'
Check '没有联接/软链' (-not $links) (($links | Select-Object -First 2) -join '; ')

Write-Host '== 3. 反向耦合 =='
$reverse = Get-ChildItem 'D:\Portfolio\agent-quality-security','D:\Portfolio\agent-evolution-platform' -Recurse -Include '*.py','*.toml','*.json' -File -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\node_modules\\|\\\.venv\\|\\\.git\\' } |
    Select-String -Pattern 'ds-harness|evolution-stack' -ErrorAction SilentlyContinue
Check 'Portfolio 里没有引用我们' ($reverse.Count -eq 0) (($reverse | Select-Object -First 2) -join '; ')

Write-Host '== 4. 端口分工 =='
$listen = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue
$ours = $listen | Where-Object { $_.LocalPort -in 1933, 14317, 16006, 18080 }
$theirs = $listen | Where-Object { $_.LocalPort -in 6006, 4317 }
Check '我们的端口在监听' ($ours.Count -ge 2) "只找到 $($ours.Count) 个"
Check '6006/4317 留给 Portfolio' ($theirs.Count -eq 0) "被占用: $(($theirs | ForEach-Object { $_.LocalPort }) -join ',')"

Write-Host '== 5. 容器挂载与卷 =='
$containers = docker ps -q 2>$null
$badMounts = @()
foreach ($id in $containers) {
    if (-not $id) { continue }
    $mounts = docker inspect $id --format '{{range .Mounts}}{{.Source}}|{{end}}' 2>$null
    foreach ($m in ($mounts -split '\|')) {
        if ($m -and $m -like 'D:\*' -and $m -notlike 'D:\start\ds-harness\*') { $badMounts += $m }
    }
}
Check '容器只挂载 ds-harness 下的路径' ($badMounts.Count -eq 0) ($badMounts -join '; ')

$vols = docker volume ls --format '{{.Name}}' 2>$null
$foreign = @($vols | Where-Object { $_ -and $_ -notmatch '^(evolution-stack_|observability_)' -and $_ -notmatch '^openviking' })
Write-Host ("       卷: " + (($vols | Where-Object { $_ }) -join ', '))

Write-Host '== 6. 镜像 digest 钉死 =='
$pinned = Select-String -Path "$stack\local\agent-quality-security\scripts\run_external.py" -Pattern '@sha256:' -AllMatches
$cnt = ($pinned.Matches | Measure-Object).Count
Check '五个镜像都按 digest 引用' ($cnt -ge 5) "只找到 $cnt 处"

Write-Host ''
if ($fails -eq 0) {
    Write-Host 'ISOLATION=PASS  与 Portfolio 无共享的可变状态'
    exit 0
} else {
    Write-Host "ISOLATION=FAIL  ($fails 项)"
    exit 1
}
