<#
.SYNOPSIS
    Windows equivalent of the Makefile, with the same targets.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File make.ps1 fast
.NOTES
    Run inside the project's virtual environment, or set $env:PYTHON to an interpreter.
#>
param(
    [ValidateSet('help', 'fast', 'test', 'arch', 'fuzz', 'chaos')]
    [string]$Target = 'help'
)

$ErrorActionPreference = 'Stop'
$Python = if ($env:PYTHON) { $env:PYTHON } else { 'python' }

function Invoke-Python([string[]]$Arguments) {
    Write-Host ">> $Python $($Arguments -join ' ')"
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

# Placeholders fail loudly instead of passing silently (plan section 16.5, 5.3).
function Stop-NotYet([string]$Name, [string]$When) {
    [Console]::Error.WriteLine("make.ps1 ${Name}: not implemented yet (arrives $When)")
    exit 2
}

switch ($Target) {
    'help' {
        Write-Host 'make.ps1 fast   lint, types and fast tests (< 2 min; run before every push)'
        Write-Host 'make.ps1 test   full local suite: lint, types, all tests incl. slow (end of every session)'
        Write-Host 'make.ps1 arch   architecture checks (not implemented yet)'
        Write-Host 'make.ps1 fuzz   fuzzers (not implemented yet)'
        Write-Host 'make.ps1 chaos  kill-at-random-point tests (not implemented yet)'
    }
    'fast' {
        Invoke-Python @('-m', 'ruff', 'check', '.')
        Invoke-Python @('-m', 'ruff', 'format', '--check', '.')
        Invoke-Python @('-m', 'mypy')
        Invoke-Python @('-m', 'pytest', '-m', 'not slow', '-q')
    }
    'test' {
        # Full local suite (AGENTS.md step 8): everything in fast, plus slow tests.
        Invoke-Python @('-m', 'ruff', 'check', '.')
        Invoke-Python @('-m', 'ruff', 'format', '--check', '.')
        Invoke-Python @('-m', 'mypy')
        Invoke-Python @('-m', 'pytest', '-q')
    }
    'arch' { Stop-NotYet 'arch' 'in Discovery part 2, plan section 5.2' }
    'fuzz' { Stop-NotYet 'fuzz' 'with v0.1-alpha, plan section 15.5' }
    'chaos' { Stop-NotYet 'chaos' 'with v0.1-alpha, plan section 15.5' }
}
