#!/usr/bin/env bash
# ============================================================================
# claude-context-plane — installer
# ============================================================================
# Copies skill/ into ~/.claude/skills/context-plane/ so Claude Code picks it up
# as a user-global skill. Run from the repo root:
#
#   ./install.sh
#
# Re-running is safe — replaces the installed copy.
# ============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$REPO_ROOT/skill"
DEST="$HOME/.claude/skills/context-plane"

if [[ ! -d "$SRC" ]]; then
    echo "ERROR: $SRC not found. Run this from the repo root."
    exit 1
fi

echo "Installing context-plane skill"
echo "  from: $SRC"
echo "  to:   $DEST"

mkdir -p "$HOME/.claude/skills"
rm -rf "$DEST"
cp -R "$SRC" "$DEST"

# Make scripts executable
find "$DEST/scripts" -name "*.py" -exec chmod +x {} \;

echo
echo "Installed. Next steps:"
echo
echo "  1. Copy .env.example to .env and fill in TiDB credentials:"
echo "       cp .env.example .env"
echo
echo "  2. Install Python dependencies:"
echo "       pip install -r requirements.txt"
echo
echo "  3. Set the .env path so scripts can find it (one option):"
echo "       ln -sf \"\$PWD/.env\" \"$DEST/.env\""
echo
echo "  4. Smoke test:"
echo "       python \"$DEST/scripts/load_context.py\" --focus \"install test\""
echo
echo "See INSTALL.md for the full walkthrough."
