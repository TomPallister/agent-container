#!/usr/bin/env bash
# Link only this command. Never modifies host shell/editor/Pi configuration.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
DEST="${AGENT_CONTAINER_BIN_DIR:-$HOME/.local/bin}"
mkdir -p "$DEST"
LINK="$DEST/agent-container"
if [ -e "$LINK" ] || [ -L "$LINK" ]; then
	if [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$ROOT/agent-container" ]; then
		printf 'Already installed: %s\n' "$LINK"
		exit 0
	fi
	printf 'Refusing to overwrite %s\n' "$LINK" >&2
	exit 1
fi
ln -s "$ROOT/agent-container" "$LINK"
printf 'Installed %s\nAdd %s to PATH if needed. Then run: agent-container setup\n' "$LINK" "$DEST"
