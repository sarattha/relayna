Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$repoRoot = $null

try {
    $repoRoot = (& git -C $scriptDir rev-parse --show-toplevel 2>$null)
} catch {
    $repoRoot = $null
}

if (-not $repoRoot) {
    $repoRoot = Resolve-Path (Join-Path $scriptDir "..\..\..\..")
}

Set-Location $repoRoot

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command
    )

    Write-Host "Running $Label..."
    & $Command
    if ($LASTEXITCODE -ne 0) {
        Write-Error "code-change-verification: $Label failed with exit code $LASTEXITCODE."
        exit $LASTEXITCODE
    }
}

Invoke-Step -Label "make format" -Command { make format }
Invoke-Step -Label "make lint" -Command { make lint }
Invoke-Step -Label "make typecheck" -Command { make typecheck }
Invoke-Step -Label "make test" -Command { make test }
Invoke-Step -Label "make -C studio/backend format" -Command { make -C studio/backend format }
Invoke-Step -Label "make -C studio/backend lint" -Command { make -C studio/backend lint }
Invoke-Step -Label "make -C studio/backend typecheck" -Command { make -C studio/backend typecheck }
Invoke-Step -Label "make -C studio/backend test" -Command { make -C studio/backend test }

Write-Host "code-change-verification: all commands passed."
