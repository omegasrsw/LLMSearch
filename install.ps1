# free-web-search installer (Windows / PowerShell) — copies (or junctions) this
# skill into an AI agent's skill directory, installs Python deps, runs the check.
#
#   .\install.ps1                       # auto-detect an agent skill root
#   .\install.ps1 -All                  # install into every agent root found
#   .\install.ps1 -Dest "$env:USERPROFILE\.claude\skills"
#   .\install.ps1 -Project              # <repo>\.agents\skills\free-web-search
#   .\install.ps1 -Deps -Link           # pip install + directory junction
#
# No API key and no account. The only network use is pip (optional) and the
# search engines at runtime.
[CmdletBinding()]
param(
    [string[]]$Dest,
    [string]$Project,          # install into <Project>\.agents\skills
    [switch]$All,              # install into every recognised root that exists
    [switch]$Link,             # junction/symlink instead of a copy
    [switch]$Deps,             # python -m pip install -r requirements.txt
    [switch]$NoCheck,
    [switch]$DryRun,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$SkillName = "free-web-search"
$Src = $PSScriptRoot
if (-not $Src) { $Src = (Get-Location).Path }

function Test-Python {
    foreach ($c in @($Python, "python", "python3", "py")) {
        if (Get-Command $c -ErrorAction SilentlyContinue) { return $c }
    }
    throw "No Python found. Install Python 3.9+ and re-run (or pass -Python <path>)."
}

# Recognised skill roots, newest convention first.
$Roots = @(
    (Join-Path $HOME ".agents\skills"),            # agentskills.dev convention
    (Join-Path $HOME ".dsh\skills"),               # DeepSeek Harness
    (Join-Path $HOME ".claude\skills"),            # Claude Code (personal)
    (Join-Path $HOME ".codex\skills"),             # OpenAI Codex CLI
    (Join-Path $HOME ".config\opencode\skills"),   # OpenCode
    (Join-Path $HOME ".hermes\skills"),            # Hermes Agent
    (Join-Path $HOME ".cursor\skills"),            # Cursor
    (Join-Path $HOME ".openclaw\skills")           # OpenClaw
)

function Resolve-Root([string]$Root) {
    $Root = $Root.TrimEnd('\', '/')
    # Hermes stores skills under a category: ~/.hermes/skills/<category>/<skill>
    if ($Root -match '[\\/]\.hermes[\\/]skills$') { return (Join-Path $Root "tools") }
    return $Root
}

function Install-Into([string]$Root) {
    $Root = Resolve-Root $Root
    $target = Join-Path $Root $SkillName
    Write-Host "-> $target"
    if ($DryRun) { return }
    New-Item -ItemType Directory -Force -Path $Root | Out-Null
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
    if ($Link) {
        try {
            New-Item -ItemType Junction -Path $target -Target $Src | Out-Null
        } catch {
            Write-Warning "junction failed ($($_.Exception.Message)); falling back to a copy"
            Copy-Item -Path $Src -Destination $target -Recurse
        }
    } else {
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        Copy-Item -Path (Join-Path $Src "SKILL.md") -Destination $target -Force
        Copy-Item -Path (Join-Path $Src "scripts") -Destination $target -Recurse -Force
        Get-ChildItem -Path $target -Recurse -Directory -Filter "__pycache__" -Force -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Get-ChildItem -Path $target -Recurse -Include "*.pyc" -Force -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
}

$installed = @()
if ($Dest) {
    foreach ($d in $Dest) { $r = Resolve-Root $d; Install-Into $r; $installed += $r }
} elseif ($Project) {
    $root = Join-Path $Project ".agents\skills"; Install-Into $root; $installed += $root
} elseif ($All) {
    foreach ($r in $Roots) { if (Test-Path $r) { $n = Resolve-Root $r; Install-Into $n; $installed += $n } }
    if (-not $installed) { throw "no recognised agent skill root found; pass -Dest <dir>" }
} else {
    $root = $Roots | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $root) {
        $root = Join-Path $HOME ".agents\skills"
        Write-Warning "no recognised agent skill root found; defaulting to $root"
    }
    $root = Resolve-Root $root
    Install-Into $root; $installed += $root
}

if ($Deps -and -not $DryRun) {
    $py = Test-Python
    Write-Host "-> installing python dependencies with $py"
    & $py -m pip install --disable-pip-version-check -r (Join-Path $Src "requirements.txt")
}

if (-not $NoCheck -and -not $DryRun) {
    $py = Test-Python
    $entry = Join-Path (Join-Path $installed[0] $SkillName) "scripts\fws.py"
    if (-not (Test-Path $entry)) { $entry = Join-Path $Src "scripts\fws.py" }
    Write-Host "-> health check"
    & $py $entry check
    Write-Host "-> smoke test"
    & $py $entry search "what is a SearXNG instance" --max-results 3
}

Write-Host ""
Write-Host "Installed. Restart the agent (or start a new session) so it re-scans its skill roots."
