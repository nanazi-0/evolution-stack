param(
    [Parameter(Mandatory = $true)][string]$DeviceCode,
    [string]$OutFile = 'D:\start\ds-harness\work\.secrets\gh-token.txt'
)

# Poll GitHub device-flow token endpoint until the user authorizes.
# On success writes the token to $OutFile and prints only a status line,
# so the secret never appears in logs or on screen.

$ErrorActionPreference = 'Stop'
$dir = Split-Path $OutFile -Parent
New-Item -ItemType Directory -Force -Path $dir | Out-Null

$body = "client_id=178c6fc778ccc68e1d6a&device_code=$DeviceCode&grant_type=urn:ietf:params:oauth:grant-type:device_code"

for ($i = 0; $i -lt 180; $i++) {
    Start-Sleep -Seconds 5
    $r = cmd /c "curl -s --max-time 20 -X POST https://github.com/login/oauth/access_token -H `"Accept: application/json`" -d `"$body`" 2>nul"
    if (-not $r) { continue }
    try { $j = $r | ConvertFrom-Json } catch { continue }
    if ($j.access_token) {
        [IO.File]::WriteAllText($OutFile, $j.access_token, (New-Object Text.UTF8Encoding($false)))
        Write-Output "TOKEN_SAVED"
        exit 0
    }
    if ($j.error -eq 'authorization_pending') { continue }
    if ($j.error -eq 'slow_down') { Start-Sleep -Seconds 5; continue }
    if ($j.error) { Write-Output ("FAILED: " + $j.error); exit 1 }
}
Write-Output "TIMEOUT"
exit 1
