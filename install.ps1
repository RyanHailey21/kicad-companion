# KiCad Companion Cross-Agent Installer
# Configures Antigravity, Claude (Desktop & Code), and Codex with Junctions and MCP definitions.

$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$skillSource = Join-Path $repoRoot "skills\kicad-companion"

Write-Host "=== Setting up KiCad Companion across all agents ===" -ForegroundColor Cyan

# 1. Setup Skills via Directory Junctions
$skillDestinations = @(
    "$env:USERPROFILE\.gemini\config\skills\kicad-companion",
    "$env:USERPROFILE\.claude\skills\kicad-companion",
    "$env:USERPROFILE\.agents\skills\kicad-companion"
)

foreach ($dest in $skillDestinations) {
    $parent = Split-Path $dest -Parent
    if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    if (Test-Path $dest) {
        Write-Host "Removing existing skill folder at $dest" -ForegroundColor Yellow
        # If it's a junction or dir, remove cleanly
        $item = Get-Item $dest
        if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            [System.IO.Directory]::Delete($dest)
        } else {
            Remove-Item -Path $dest -Recurse -Force
        }
    }
    Write-Host "Creating Directory Junction: $dest -> $skillSource" -ForegroundColor Green
    New-Item -ItemType Junction -Path $dest -Target $skillSource | Out-Null
}

# 2. Register MCP Server in Antigravity (~/.gemini/config/mcp_config.json)
$geminiConfigPath = "$env:USERPROFILE\.gemini\config\mcp_config.json"
if (Test-Path $geminiConfigPath) {
    Write-Host "Registering in Antigravity config..." -ForegroundColor Green
    $gJson = Get-Content $geminiConfigPath -Raw | ConvertFrom-Json
    if (-not $gJson.mcpServers) {
        $gJson | Add-Member -MemberType NoteProperty -Name "mcpServers" -Value ([PSCustomObject]@{})
    }
    $gJson.mcpServers | Add-Member -MemberType NoteProperty -Name "kicad-companion" -Value ([PSCustomObject]@{
        command = "uv"
        args = @("--directory", $repoRoot, "run", "kicad-companion")
    }) -Force
    $gJson | ConvertTo-Json -Depth 10 | Set-Content $geminiConfigPath
}

# 3. Register MCP Server in Claude Desktop (%APPDATA%\Claude\claude_desktop_config.json)
$claudeConfigPath = "$env:APPDATA\Claude\claude_desktop_config.json"
if (Test-Path $claudeConfigPath) {
    Write-Host "Registering in Claude Desktop config..." -ForegroundColor Green
    $cJson = Get-Content $claudeConfigPath -Raw | ConvertFrom-Json
    if (-not $cJson.mcpServers) {
        $cJson | Add-Member -MemberType NoteProperty -Name "mcpServers" -Value ([PSCustomObject]@{})
    }
    $cJson.mcpServers | Add-Member -MemberType NoteProperty -Name "kicad-companion" -Value ([PSCustomObject]@{
        command = "uv"
        args = @("--directory", $repoRoot, "run", "kicad-companion")
    }) -Force
    $cJson | ConvertTo-Json -Depth 10 | Set-Content $claudeConfigPath
}

# 4. Register MCP Server in Codex (~/.codex/config.toml)
$codexConfigPath = "$env:USERPROFILE\.codex\config.toml"
if (Test-Path $codexConfigPath) {
    Write-Host "Registering in Codex config..." -ForegroundColor Green
    $codexContent = Get-Content $codexConfigPath -Raw
    if ($codexContent -notmatch "\[mcp_servers\.kicad_companion\]") {
        $codexBlock = @"

[mcp_servers.kicad_companion]
command = "uv"
args = ["--directory", "$($repoRoot.Replace('\', '\\'))", "run", "kicad-companion"]
enabled = true
"@
        Add-Content -Path $codexConfigPath -Value $codexBlock
        Write-Host "Appended kicad_companion to Codex config.toml" -ForegroundColor Green
    } else {
        Write-Host "kicad_companion already present in Codex config.toml" -ForegroundColor Yellow
    }
}

Write-Host "=== Installation Complete! All agents now have access to KiCad Companion ===" -ForegroundColor Cyan
