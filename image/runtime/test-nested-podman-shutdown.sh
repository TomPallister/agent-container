#!/usr/bin/env bash
set -u
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P) || exit 1
SHUTDOWN=$SCRIPT_DIR/nested-podman-shutdown.sh
WATCHDOG=$SCRIPT_DIR/devcrypt-watchdog.sh
failures=0
fail() { printf 'FAIL: %s\n' "$1" >&2; failures=$((failures + 1)); }

[ -x "$SHUTDOWN" ] || { echo 'FAIL: nested-podman-shutdown.sh is missing' >&2; exit 1; }
sandbox=$(mktemp -d "${TMPDIR:-/tmp}/nested-shutdown.XXXXXX") || exit 1
trap 'rm -rf "$sandbox"' EXIT HUP INT TERM
mkdir -p "$sandbox/bin" "$sandbox/home/dev"
events=$sandbox/events

cat >"$sandbox/bin/mountpoint" <<'SH'
#!/usr/bin/env bash
exit 0
SH
cat >"$sandbox/bin/bridge" <<'SH'
#!/usr/bin/env bash
printf 'bridge %s\n' "$*" >>"${EVENTS:?}"
SH
cat >"$sandbox/bin/timeout" <<'SH'
#!/usr/bin/env bash
shift
exec "$@"
SH
cat >"$sandbox/bin/podman" <<'SH'
#!/usr/bin/env bash
printf 'podman %s\n' "$*" >>"${EVENTS:?}"
case "$*" in
  'ps -q')
    count_file=${COUNT_FILE:?}
    count=$(cat "$count_file" 2>/dev/null || echo 0)
    if [ "$count" -eq 0 ]; then
      printf 'one\ntwo\n'
    elif [ "$count" -eq 1 ] && [ "${STUBBORN:-0}" = 1 ]; then
      printf 'two\n'
    elif [ "$count" -ge 2 ] && [ "${UNSTOPPABLE:-0}" = 1 ]; then
      printf 'two\n'
    fi
    printf '%s\n' $((count + 1)) >"$count_file"
    ;;
esac
SH
chmod +x "$sandbox/bin/"*

run_shutdown() {
	: >"$events"; rm -f "$sandbox/count"
	env PATH="$sandbox/bin:/usr/bin:/bin" HOME="$sandbox/home" EVENTS="$events" \
		COUNT_FILE="$sandbox/count" STUBBORN="${STUBBORN:-0}" UNSTOPPABLE="${UNSTOPPABLE:-0}" \
		AGENT_CONTAINER_NESTED_TEST_MODE=1 DEVCRYPT_PLAIN="$sandbox/home/dev" \
		NESTED_PROXY_BRIDGE="$sandbox/bin/bridge" bash "$SHUTDOWN"
}

run_shutdown || fail 'normal nested shutdown failed.'
grep -F 'podman stop --time 5 one two' "$events" >/dev/null || fail 'running workloads were not stopped.'
grep -F 'bridge stop' "$events" >/dev/null || fail 'proxy bridge was not stopped.'

STUBBORN=1 run_shutdown || fail 'stubborn workload shutdown failed after kill.'
grep -F 'podman kill two' "$events" >/dev/null || fail 'stubborn workload was not killed.'

status=0
STUBBORN=1 UNSTOPPABLE=1 run_shutdown >/dev/null 2>"$sandbox/fail.err" || status=$?
[ "$status" -ne 0 ] || fail 'unstoppable workload did not fail closed.'

shutdown_line=$(grep -n -m1 '/usr/local/bin/nested-podman-shutdown.sh' "$WATCHDOG" | cut -d: -f1)
unmount_line=$(grep -n -m1 'fusermount -u ' "$WATCHDOG" | cut -d: -f1)
if grep -Eq 'fusermount -uz|umount -l' "$WATCHDOG"; then
	fail 'watchdog must not claim a lazy unmount is locked.'
fi
if [ -z "$shutdown_line" ] || [ -z "$unmount_line" ] || [ "$shutdown_line" -ge "$unmount_line" ]; then
	fail 'watchdog does not verify nested shutdown before encrypted unmount.'
fi

[ "$failures" -eq 0 ] || exit 1
printf 'PASS: nested workloads stop before encrypted workspace unmount.\n'
