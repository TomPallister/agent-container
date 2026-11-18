#!/usr/bin/env bash
set -u
[ "$(uname -s)" = Linux ] || { echo 'SKIP: bridge process ownership requires Linux /proc'; exit 0; }

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P) || exit 1
BRIDGE=$SCRIPT_DIR/nested-proxy-bridge.sh
failures=0
fail() { printf 'FAIL: %s\n' "$1" >&2; failures=$((failures + 1)); }
file_mode() {
	if stat -c '%a' "$1" >/dev/null 2>&1; then stat -c '%a' "$1"; else stat -f '%Lp' "$1"; fi
}

[ -x "$BRIDGE" ] || { echo 'FAIL: nested-proxy-bridge.sh is missing' >&2; exit 1; }

sandbox=$(mktemp -d "${TMPDIR:-/tmp}/nested-bridge.XXXXXX") || exit 1
trap 'rm -rf "$sandbox"' EXIT HUP INT TERM
mkdir -p "$sandbox/bin" "$sandbox/run"
events=$sandbox/events

cat >"$sandbox/bin/tinyproxy" <<'SH'
#!/usr/bin/env bash
printf 'tinyproxy %s\n' "$*" >>"${EVENTS:?}"
# Remain alive so the bridge can publish and stop the fake PID.
while :; do sleep 60; done
SH
cat >"$sandbox/bin/ss" <<'SH'
#!/usr/bin/env bash
[ -f "${LISTEN_FILE:?}" ] && printf 'LISTEN 0 128 127.0.0.1:10257 0.0.0.0:*\n'
SH
cat >"$sandbox/bin/setsid" <<'SH'
#!/usr/bin/env bash
exec "$@"
SH
chmod +x "$sandbox/bin/"*

test_token=$(printf '0123456789abcdef%.0s' 1 2 3)
common_env=(
	PATH="$sandbox/bin:/usr/bin:/bin"
	EVENTS="$events"
	LISTEN_FILE="$sandbox/listening"
	AGENT_CONTAINER_NESTED_TEST_MODE=1
	AGENT_CONTAINER_RUN_DIR="$sandbox/run"
	NESTED_TINYPROXY_BIN="$sandbox/bin/tinyproxy"
)

status=0
env "${common_env[@]}" HTTPS_PROXY=http://bad.invalid:10256 \
	bash "$BRIDGE" start >"$sandbox/bad.out" 2>"$sandbox/bad.err" || status=$?
[ "$status" -ne 0 ] || fail 'bridge accepted an unauthenticated upstream proxy URL.'

# The fake listener becomes ready shortly after launch.
( sleep 0.2; : >"$sandbox/listening" ) &
env "${common_env[@]}" \
	HTTPS_PROXY="http://secret-proxy:${test_token}@127.0.0.1:10256" \
	bash "$BRIDGE" start || fail 'valid bridge start failed.'
config=$sandbox/run/nested-proxy.conf
[ "$(file_mode "$config")" = 600 ] \
	|| fail 'bridge config is not mode 0600.'
grep -F 'Port 10257' "$config" >/dev/null || fail 'child bridge port is wrong.'
grep -F 'Listen 127.0.0.1' "$config" >/dev/null || fail 'bridge does not bind parent loopback.'
grep -F "upstream http secret-proxy:${test_token}@127.0.0.1:10256" "$config" >/dev/null \
	|| fail 'authenticated catch-all upstream is missing.'
for _ in $(seq 1 20); do [ -s "$events" ] && break; sleep 0.05; done
if grep -Ei '^(upstream none|ConnectPort|Allow [^1])' "$config" >/dev/null; then
	fail 'bridge configuration permits a direct fallback or broad client source.'
fi
grep -F -- '-d -c' "$events" >/dev/null || fail 'tinyproxy was not launched foreground with a config path.'
if grep -F "$test_token" "$events" >/dev/null; then
	fail 'proxy capability appeared in process arguments.'
fi

rm -f "$sandbox/listening"
env "${common_env[@]}" bash "$BRIDGE" stop || fail 'bridge stop failed.'
[ ! -e "$config" ] || fail 'bridge config survived stop.'

# The container reaches only the fixed proxy transport name.
# shellcheck disable=SC2043 # Explicit one-name allowlist; negative cases below.
for upstream_host in secret-proxy; do
	( sleep 0.2; : >"$sandbox/listening" ) &
	env "${common_env[@]}" \
		HTTPS_PROXY="http://secret-proxy:${test_token}@${upstream_host}:10256" \
		bash "$BRIDGE" start >/dev/null 2>"$sandbox/named.err" \
		|| fail "bridge rejected the profile proxy name $upstream_host."
	grep -F "upstream http secret-proxy:${test_token}@${upstream_host}:10256" "$config" >/dev/null 2>&1 \
		|| fail "bridge did not route upstream to $upstream_host."
	rm -f "$sandbox/listening"
	env "${common_env[@]}" bash "$BRIDGE" stop >/dev/null 2>&1 || fail 'bridge stop failed.'
done
for upstream_host in secret-proxy-evil.invalid unexpected-host; do
	status=0
	env "${common_env[@]}" \
		HTTPS_PROXY="http://secret-proxy:${test_token}@${upstream_host}:10256" \
		bash "$BRIDGE" start >/dev/null 2>&1 || status=$?
	[ "$status" -ne 0 ] || fail "bridge accepted an unexpected upstream host $upstream_host."
done

status=0
env AGENT_CONTAINER_RUN_DIR="$sandbox/run" NESTED_TINYPROXY_BIN="$sandbox/bin/tinyproxy" \
	bash "$BRIDGE" stop >/dev/null 2>"$sandbox/override.err" || status=$?
[ "$status" -eq 64 ] || fail 'runtime path overrides were not rejected outside test mode.'

[ "$failures" -eq 0 ] || exit 1
printf 'PASS: nested proxy bridge keeps upstream credentials ephemeral and child-facing access credential-free.\n'
