<#
.SYNOPSIS
    Bootstrap for Windows PowerShell and PowerShell 7+.

.DESCRIPTION
    Finds a Python 3.9+ interpreter and hands off to the installer, which is
    standard-library only. uv is tried last: when a usable python is already
    on PATH, downloading a second one to run a stdlib script would be a
    strange thing to do to someone who just cloned a repo.

    Note on symlinks. The installer prefers symlinks so `git pull` updates
    every installed harness at once, and Windows only permits creating them
    with Developer Mode enabled or from an elevated prompt. It tests for that
    rather than assuming, and falls back to copying, so this works either way
    -- a copy just has to be re-run after a pull.

.EXAMPLE
    .\install.ps1

.EXAMPLE
    .\install.ps1 --harness codex --skills all --yes
#>

[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Test-UsablePython {
    param([string]$Exe)
    if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $false }
    & $Exe -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>$null
    return $LASTEXITCODE -eq 0
}

# `py` is the Windows launcher and is the most reliable way to reach a real
# interpreter; the `python`/`python3` names on Windows are often the Microsoft
# Store stubs, which exit without running anything.
foreach ($candidate in @('py', 'python', 'python3')) {
    if (Test-UsablePython $candidate) {
        & $candidate -m wow_tools @Args
        exit $LASTEXITCODE
    }
}

if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Warning 'No Python 3.9+ found on PATH; using uv to provide one.'
    & uv run --no-project --python 3.9 python -m wow_tools @Args
    exit $LASTEXITCODE
}

Write-Error @'
This installer needs Python 3.9 or newer, and none was found.

Install one of:
  uv       https://docs.astral.sh/uv/getting-started/installation/
  Python   https://www.python.org/downloads/

Then re-run this script.
'@
exit 1
