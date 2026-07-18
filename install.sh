#!/usr/bin/env bash
# Corvus-Mind one-line installer
# Usage: curl -fsSL https://raw.githubusercontent.com/tylerbvogel/corvus/main/install.sh | bash

set -euo pipefail

REPO="tylerbvogel/corvus"
BRANCH="main"
INSTALL_DIR="${HOME}/Projects/corvus"
CLAUDE_SETTINGS="${HOME}/.claude/settings.json"

echo "🐦 Installing Corvus-Mind..."

# 1. Clone or update
if [[ -d "${INSTALL_DIR}/.git" ]]; then
    echo "  → Updating existing installation..."
    git -C "${INSTALL_DIR}" pull --ff-only
else
    echo "  → Cloning..."
    mkdir -p "$(dirname "${INSTALL_DIR}")"
    git clone --branch "${BRANCH}" "https://github.com/${REPO}.git" "${INSTALL_DIR}"
fi

# 2. Backend setup
echo "  → Setting up Python backend..."
cd "${INSTALL_DIR}/backend"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 3. Database
echo "  → Creating database..."
if command -v psql >/dev/null; then
    sudo -u postgres createdb -O "$(whoami)" corvus_mind 2>/dev/null || true
    sudo -u postgres psql -d corvus_mind -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>/dev/null || true
else
    echo "  ⚠ PostgreSQL not found. Install it and run:"
    echo "    sudo -u postgres createdb -O \$(whoami) corvus_mind"
    echo "    sudo -u postgres psql -d corvus_mind -c \"CREATE EXTENSION IF NOT EXISTS vector;\""
fi

# 4. Configure Claude Code hooks
echo "  → Configuring Claude Code hooks..."
mkdir -p "$(dirname "${CLAUDE_SETTINGS}")"

# Read existing settings or create empty
if [[ -f "${CLAUDE_SETTINGS}" ]]; then
    SETTINGS=$(cat "${CLAUDE_SETTINGS}")
else
    SETTINGS='{}'
fi

# Use Python to merge JSON (more reliable than jq)
python3 << PYEOF
import json, os, sys

settings_path = os.path.expanduser("${CLAUDE_SETTINGS}")
install_dir = os.path.expanduser("${INSTALL_DIR}")

hooks = {
    "SessionStart": [{
        "matcher": "",
        "hooks": [{"type": "command", "command": f"python3 {install_dir}/harness/claude-code/memory_inject_hook.py"}]
    }],
    "UserPromptSubmit": [{
        "matcher": "",
        "hooks": [{"type": "command", "command": f"python3 {install_dir}/harness/claude-code/memory_inject_hook.py"}]
    }],
    "PostToolUse": [{
        "matcher": "",
        "hooks": [{"type": "command", "command": f"python3 {install_dir}/harness/claude-code/episode_hook.py"}]
    }],
    "PreToolUse": [{
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": f"python3 {install_dir}/harness/claude-code/memory_inject_hook.py"}]
    }],
    "Stop": [{
        "matcher": "",
        "hooks": [{"type": "command", "command": f"python3 {install_dir}/harness/claude-code/episode_hook.py"}]
    }]
}

with open(settings_path, 'r') as f:
    try:
        existing = json.load(f)
    except json.JSONDecodeError:
        existing = {}

existing.setdefault("hooks", {}).update(hooks)

with open(settings_path, 'w') as f:
    json.dump(existing, f, indent=2)

print(f"  ✓ Updated {settings_path}")
PYEOF

# 5. Configure exclusions
CONFIG_DIR="${HOME}/.corvus-mind"
mkdir -p "${CONFIG_DIR}"
CONFIG_FILE="${CONFIG_DIR}/config.json"
if [[ ! -f "${CONFIG_FILE}" ]]; then
    cat > "${CONFIG_FILE}" << 'EOF'
{
  "excluded_cwd_prefixes": [
    "~/Projects/personal",
    "~/Projects/work-private",
    "~/Documents/private"
  ]
}
EOF
    echo "  ✓ Created ${CONFIG_FILE} (edit to add your private paths)"
fi

# 6. Register MCP server
echo "  → Registering MCP server..."
if command -v claude >/dev/null; then
    claude mcp add --scope user corvus-mind \
        "${INSTALL_DIR}/backend/venv/bin/python" \
        "${INSTALL_DIR}/harness/claude-code/mind_mcp_server.py" 2>/dev/null || true
    echo "  ✓ MCP server registered"
else
    echo "  ⚠ Claude CLI not found. Run after installing Claude Code:"
    echo "    claude mcp add --scope user corvus-mind \\"
    echo "      ${INSTALL_DIR}/backend/venv/bin/python \\"
    echo "      ${INSTALL_DIR}/harness/claude-code/mind_mcp_server.py"
fi

# 7. Start backend (instructions)
echo ""
echo "✅ Installation complete!"
echo ""
echo "📋 Next steps:"
echo "  1. Start the backend:"
echo "     cd ${INSTALL_DIR}/backend && source venv/bin/activate"
echo "     TENANT_ID=corvus-mind PORT=8005 uvicorn app.main:app --port 8005 --reload"
echo ""
echo "  2. (Optional) Start the demo frontend:"
echo "     cd ${INSTALL_DIR}/frontend && npm install"
echo "     VITE_API_PORT=8005 npm run dev"
echo ""
echo "  3. Verify:"
echo "     curl http://localhost:8005/health"
echo ""
echo "  4. Open Claude Code — ambient memory is now active! 🧠"
echo ""
echo "📖 Full docs: ${INSTALL_DIR}/README.md"
echo "⚙️  Config: ${CONFIG_FILE} (add your private/excluded paths)"