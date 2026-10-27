param(
    [Parameter(Mandatory = $true)][string]$User,
    [string]$PlatformRepo = 'agent-evolution-platform',
    [string]$SecurityRepo = 'agent-quality-security',
    [string]$Branch = 'main'
)

# Push the two local projects to GitHub.
# Credentials are handled by Git Credential Manager (a popup window),
# so no token ever needs to be typed into a chat or a file.
#
# BEFORE RUNNING: create two EMPTY repositories on github.com
#   https://github.com/<your-name>/agent-evolution-platform   (no README, no .gitignore)
#   https://github.com/<your-name>/agent-quality-security     (same)
#
# Then:
#   powershell -NoProfile -ExecutionPolicy Bypass -File push-to-github.ps1 -User <your-name>

$ErrorActionPreference = 'Stop'
$root = 'D:\start\ds-harness\evolution-stack\local'

function Test-RemoteExists {
    param([string]$Url)
    $code = cmd /c "curl -s -o NUL -w %{http_code} --max-time 15 $Url 2>nul"
    return ($code -eq '200')
}

foreach ($name in @($PlatformRepo, $SecurityRepo)) {
    $dir = Join-Path $root $name
    Write-Host ""
    Write-Host "=== $name ===" -ForegroundColor Cyan

    if (-not (Test-Path (Join-Path $dir '.git'))) {
        Write-Host "  SKIP: not a git repo -> $dir" -ForegroundColor Yellow
        continue
    }

    $url = "https://github.com/$User/$name"
    if (-not (Test-RemoteExists $url)) {
        Write-Host "  MISSING: $url" -ForegroundColor Red
        Write-Host "  Create it first (empty repo, no README), then re-run."
        continue
    }

    $existing = git -C $dir remote 2>$null
    if ($existing -contains 'origin') {
        git -C $dir remote set-url origin "$url.git"
    } else {
        git -C $dir remote add origin "$url.git"
    }

    $branchNow = git -C $dir rev-parse --abbrev-ref HEAD 2>$null
    if ($branchNow -ne $Branch) {
        git -C $dir branch -M $Branch
    }

    Write-Host "  pushing to $url.git ..."
    Write-Host "  (Git Credential Manager will pop up - use a Personal Access Token, NOT your password)" -ForegroundColor Yellow
    git -C $dir push -u origin $Branch
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  OK: $url" -ForegroundColor Green
    } else {
        Write-Host "  FAILED (exit $LASTEXITCODE). If it asked for a password:" -ForegroundColor Red
        Write-Host "  GitHub no longer accepts passwords. Create a PAT with 'repo' scope:"
        Write-Host "  https://github.com/settings/tokens"
    }
}

Write-Host ""
Write-Host "Done. Verify at https://github.com/$User?tab=repositories"
