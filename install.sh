#!/usr/bin/env bash
# KiCad Companion Cross-Agent Installer for macOS & Linux
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_SOURCE="$SCRIPT_DIR/skills/kicad-companion"

echo -e "\033[1;36m=== Setting up KiCad Companion across all agents ===\033[0m"

# 1. Symlink Skills
SKILL_DESTS=(
    "$HOME/.gemini/config/skills/kicad-companion"
    "$HOME/.claude/skills/kicad-companion"
    "$HOME/.agents/skills/kicad-companion"
)

for DEST in "${SKILL_DESTS[@]}"; do
    PARENT_DIR="$(dirname "$DEST")"
    mkdir -p "$PARENT_DIR"
    if [ -L "$DEST" ] || [ -e "$DEST" ]; then
        echo -e "\033[1;33mRemoving existing skill link/folder at $DEST\033[0m"
        rm -rf "$DEST"
    fi
    echo -e "\033[1;32mCreating symlink: $DEST -> $SKILL_SOURCE\033[0m"
    ln -s "$SKILL_SOURCE" "$DEST"
done

# Python helper to safely inject mcpServers into JSON
inject_mcp_json() {
    local config_file="$1"
    if [ -f "$config_file" ]; then
        echo -e "\033[1;32mUpdating $config_file...\033[0m"
        python3 - <<EOF
import json, sys

config_path = "$config_file"
repo_dir = "$SCRIPT_DIR"

try:
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    data = {}

if "mcpServers" not in data:
    data["mcpServers"] = {}

data["mcpServers"]["kicad-companion"] = {
    "command": "uv",
    "args": ["--directory", repo_dir, "run", "kicad-companion"]
}

with open(config_path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
EOF
    fi
}

# 2. Register in Antigravity (~/.gemini/config/mcp_config.json)
inject_mcp_json "$HOME/.gemini/config/mcp_config.json"

# 3. Register in Claude Desktop
if [[ "$OSTYPE" == "darwin"* ]]; then
    inject_mcp_json "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
else
    inject_mcp_json "$HOME/.config/Claude/claude_desktop_config.json"
fi

# 4. Register in Codex (~/.codex/config.toml)
CODEX_CONF="$HOME/.codex/config.toml"
if [ -f "$CODEX_CONF" ]; then
    if ! grep -q "\[mcp_servers\.kicad_companion\]" "$CODEX_CONF"; then
        echo -e "\033[1;32mRegistering in Codex config ($CODEX_CONF)...\033[0m"
        cat <<EOF >> "$CODEX_CONF"

[mcp_servers.kicad_companion]
command = "uv"
args = ["--directory", "$SCRIPT_DIR", "run", "kicad-companion"]
enabled = true
EOF
    else
        echo -e "\033[1;33mkicad_companion already in $CODEX_CONF\033[0m"
    fi
fi

echo -e "\033[1;36m=== Installation Complete! All agents now have access to KiCad Companion ===\033[0m"
