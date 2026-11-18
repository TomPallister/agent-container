#!/usr/bin/env bash
# Bounded, fail-closed shutdown of workloads backed by encrypted nested storage.
set -euo pipefail

TEST_MODE=${AGENT_CONTAINER_NESTED_TEST_MODE:-0}
case "$TEST_MODE" in 0|1) ;; *) echo 'nested-podman-shutdown: invalid test mode' >&2; exit 64;; esac
if [ "$TEST_MODE" = 0 ]; then
	for override in DEVCRYPT_PLAIN NESTED_PROXY_BRIDGE; do
		[ -z "${!override+x}" ] \
			|| { echo "nested-podman-shutdown: $override is test-only" >&2; exit 64; }
	done
fi

PLAIN=${DEVCRYPT_PLAIN:-$HOME/dev}
BRIDGE=${NESTED_PROXY_BRIDGE:-/usr/local/bin/nested-proxy-bridge.sh}
RUNTIME=/tmp/agent-container-podman

stop_bridge() {
	[ -x "$BRIDGE" ] && "$BRIDGE" stop >/dev/null 2>&1 || true
}

if ! mountpoint -q "$PLAIN"; then
	stop_bridge
	exit 0
fi

running=$(XDG_RUNTIME_DIR="$RUNTIME" podman ps -q)
if [ -n "$running" ]; then
	# Podman IDs contain no shell metacharacters or whitespace.
	# shellcheck disable=SC2086
	timeout 15 env XDG_RUNTIME_DIR="$RUNTIME" podman stop --time 5 $running >/dev/null 2>&1 || true
fi
remaining=$(XDG_RUNTIME_DIR="$RUNTIME" podman ps -q)
if [ -n "$remaining" ]; then
	# shellcheck disable=SC2086
	timeout 10 env XDG_RUNTIME_DIR="$RUNTIME" podman kill $remaining >/dev/null 2>&1 || true
	sleep 0.2
fi
remaining=$(XDG_RUNTIME_DIR="$RUNTIME" podman ps -q)
stop_bridge
if [ -n "$remaining" ]; then
	echo 'nested-podman-shutdown: running workloads remain; refusing encrypted unmount' >&2
	exit 1
fi
