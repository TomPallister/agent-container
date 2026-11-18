#!/usr/bin/env bash
# Credential-free child proxy -> authenticated parent secret-proxy bridge.
set -euo pipefail

TEST_MODE=${AGENT_CONTAINER_NESTED_TEST_MODE:-0}
case "$TEST_MODE" in 0|1) ;; *) echo 'nested-proxy-bridge: invalid test mode' >&2; exit 64;; esac
if [ "$TEST_MODE" = 0 ]; then
	for override in AGENT_CONTAINER_RUN_DIR NESTED_TINYPROXY_BIN NESTED_PROXY_PORT; do
		[ -z "${!override+x}" ] \
			|| { echo "nested-proxy-bridge: $override is test-only" >&2; exit 64; }
	done
fi

RUN_DIR=${AGENT_CONTAINER_RUN_DIR:-/tmp/agent-container}
CONFIG=$RUN_DIR/nested-proxy.conf
PID_FILE=$RUN_DIR/nested-proxy.pid
TINYPROXY=${NESTED_TINYPROXY_BIN:-/usr/bin/tinyproxy}
PORT=${NESTED_PROXY_PORT:-10257}

mkdir -p "$RUN_DIR"
chmod 700 "$RUN_DIR"
umask 077

read_pid() {
	local pid
	pid=$(cat "$PID_FILE" 2>/dev/null) || return 1
	[[ "$pid" =~ ^[0-9]+$ ]] || return 1
	printf '%s' "$pid"
}

owned_pid() {
	local pid=$1
	kill -0 "$pid" 2>/dev/null || return 1
	grep -Faz -- "$TINYPROXY" "/proc/$pid/cmdline" >/dev/null 2>&1
}

stop_bridge() {
	local pid
	pid=$(read_pid 2>/dev/null || true)
	if [ -n "$pid" ] && owned_pid "$pid"; then
		kill "$pid" 2>/dev/null || true
		for _ in $(seq 1 20); do
			kill -0 "$pid" 2>/dev/null || break
			sleep 0.1
		done
		kill -9 "$pid" 2>/dev/null || true
	fi
	rm -f -- "$PID_FILE" "$CONFIG" "$RUN_DIR/tinyproxy.pid"
}

listening() {
	ss -ltn 2>/dev/null | grep -q "127.0.0.1:${PORT} "
}

case "${1:-start}" in
	stop)
		stop_bridge
		exit 0
		;;
	start) ;;
	*) echo 'usage: nested-proxy-bridge.sh {start|stop}' >&2; exit 64;;
esac

[ -x "$TINYPROXY" ] \
	|| { echo 'nested-proxy-bridge: tinyproxy is unavailable' >&2; exit 1; }
[ "$PORT" = 10257 ] \
	|| { echo 'nested-proxy-bridge: child proxy port must be 10257' >&2; exit 64; }

UPSTREAM=${HTTPS_PROXY:-${https_proxy:-}}
[ -n "$UPSTREAM" ] \
	|| { echo 'nested-proxy-bridge: authenticated upstream proxy is unavailable' >&2; exit 1; }

temporary=$(mktemp "$RUN_DIR/.nested-proxy.conf.XXXXXX")
cleanup_temp() { rm -f -- "${temporary:-}"; }
trap cleanup_temp EXIT HUP INT TERM
UPSTREAM="$UPSTREAM" RUN_DIR="$RUN_DIR" PORT="$PORT" python3 - "$temporary" <<'PY'
import os
import re
import sys
from urllib.parse import unquote, urlsplit

raw = os.environ["UPSTREAM"]
parsed = urlsplit(raw)
if (
    parsed.scheme != "http"
    or parsed.username != "secret-proxy"
    or parsed.password is None
    # Loopback is the secagent socket forwarder; the names are the proxy transport on the isolated client network.
    or parsed.hostname not in {"127.0.0.1", "localhost", "secret-proxy"}
    or parsed.port != 10256
    or parsed.path not in {"", "/"}
    or parsed.query
    or parsed.fragment
):
    raise SystemExit("nested-proxy-bridge: upstream proxy URL violates the fixed contract")
password = unquote(parsed.password)
if re.fullmatch(r"[0-9a-f]{48}", password) is None:
    raise SystemExit("nested-proxy-bridge: upstream proxy capability is invalid")
host = parsed.hostname
run_dir = os.environ["RUN_DIR"]
port = int(os.environ["PORT"])
config = f"""Port {port}
Listen 127.0.0.1
Timeout 600
MaxClients 100
Allow 127.0.0.1
DisableViaHeader Yes
Syslog On
LogLevel Critical
PidFile "{run_dir}/tinyproxy.pid"
upstream http secret-proxy:{password}@{host}:10256
"""
with open(sys.argv[1], "w", encoding="utf-8") as destination:
    destination.write(config)
PY
chmod 600 "$temporary"

stop_bridge
mv -f -- "$temporary" "$CONFIG"
temporary=
setsid "$TINYPROXY" -d -c "$CONFIG" >/dev/null 2>&1 </dev/null &
pid=$!
printf '%s\n' "$pid" >"$PID_FILE"
chmod 600 "$PID_FILE"

for _ in $(seq 1 50); do
	if ! kill -0 "$pid" 2>/dev/null; then
		stop_bridge
		echo 'nested-proxy-bridge: tinyproxy exited before readiness' >&2
		exit 1
	fi
	if listening; then
		trap - EXIT HUP INT TERM
		exit 0
	fi
	sleep 0.1
done
stop_bridge
echo 'nested-proxy-bridge: proxy did not listen on parent loopback' >&2
exit 1
