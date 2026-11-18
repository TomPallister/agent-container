#!/usr/bin/env bash
# Idle-timeout watchdog for the encrypted ~/dev workspace (runs inside agent-container).
set -euo pipefail

RUN_DIR="${AGENT_CONTAINER_RUN_DIR:-/tmp/agent-container}"
TS_FILE="$RUN_DIR/unlock-ts"
PID_FILE="$RUN_DIR/watchdog.pid"
READY_FILE="$RUN_DIR/watchdog.ready"
PLAIN="${DEVCRYPT_PLAIN:-$HOME/dev}"
TIMEOUT="${DEVCRYPT_IDLE_SECS:-3600}"
INTERVAL="${DEVCRYPT_CHECK_SECS:-60}"
START_WAIT="${DEVCRYPT_WATCHDOG_START_SECS:-5}"

invalid_config() {
	echo "devcrypt-watchdog: invalid watchdog configuration" >&2
	exit 64
}
validate_uint() {
	local value=$1 minimum=$2 maximum=$3
	[[ "$value" =~ ^[0-9]+$ ]] || invalid_config
	(( value >= minimum && value <= maximum )) || invalid_config
}
validate_uint "$TIMEOUT" 1 604800
validate_uint "$INTERVAL" 1 3600
validate_uint "$START_WAIT" 1 30
(( INTERVAL <= TIMEOUT )) || invalid_config

umask 077
mkdir -p "$RUN_DIR" || { echo "devcrypt-watchdog: cannot create runtime directory" >&2; exit 1; }
chmod 700 "$RUN_DIR" || { echo "devcrypt-watchdog: cannot secure runtime directory" >&2; exit 1; }

read_pid() {
	local pid
	pid=$(cat "$PID_FILE" 2>/dev/null) || return 1
	[[ "$pid" =~ ^[0-9]+$ ]] || return 1
	printf '%s' "$pid"
}
alive() {
	local pid
	pid=$(read_pid) || return 1
	kill -0 "$pid" 2>/dev/null
}
ready() {
	local pid ready_pid
	pid=$(read_pid) || return 1
	ready_pid=$(cat "$READY_FILE" 2>/dev/null) || return 1
	[ "$ready_pid" = "$pid" ] && kill -0 "$pid" 2>/dev/null
}
atomic_state() {
	local destination=$1 value=$2 temporary
	temporary=$(mktemp "$RUN_DIR/.${destination##*/}.XXXXXX") || return 1
	printf '%s\n' "$value" >"$temporary" || { rm -f -- "$temporary"; return 1; }
	chmod 600 "$temporary" || { rm -f -- "$temporary"; return 1; }
	mv -f -- "$temporary" "$destination"
}
cleanup_own_state() {
	local current
	current=$(read_pid 2>/dev/null || true)
	if [ "$current" = "$$" ]; then
		rm -f -- "$READY_FILE" "$PID_FILE"
	fi
}

open_terminals() {
	local terminal name
	for terminal in /dev/pts/*; do
		name=${terminal##*/}
		case "$name" in ""|*[!0-9]*) continue ;; esac
		[ -c "$terminal" ] && return 0
	done
	return 1
}

case "${1:-run}" in
	start)
		if ready; then exit 0; fi
		if alive; then
			echo "devcrypt-watchdog: existing process is alive but not ready" >&2
			exit 1
		fi
		rm -f -- "$PID_FILE" "$READY_FILE"
		command -v setsid >/dev/null 2>&1 \
			|| { echo "devcrypt-watchdog: setsid is required" >&2; exit 1; }
		setsid "$0" run >/dev/null 2>&1 </dev/null &
		launcher_pid=$!
		attempts=$((START_WAIT * 10))
		while (( attempts > 0 )); do
			if ready; then exit 0; fi
			if ! kill -0 "$launcher_pid" 2>/dev/null; then break; fi
			sleep 0.1
			attempts=$((attempts - 1))
		done
		kill "$launcher_pid" >/dev/null 2>&1 || true
		wait "$launcher_pid" 2>/dev/null || true
		rm -f -- "$PID_FILE" "$READY_FILE"
		echo "devcrypt-watchdog: detached child failed to become ready" >&2
		exit 1
		;;
	stop)
		pid=$(read_pid 2>/dev/null || true)
		if [ -n "$pid" ]; then kill "$pid" 2>/dev/null || true; fi
		rm -f -- "$READY_FILE" "$PID_FILE"
		exit 0
		;;
	restart)
		"$0" stop
		exec "$0" start
		;;
	run) ;;
	*) echo "usage: $0 {start|stop|restart|run}" >&2; exit 64 ;;
esac

atomic_state "$PID_FILE" "$$" \
	|| { echo "devcrypt-watchdog: failed to publish pid" >&2; exit 1; }
sleep_pid=
terminate() {
	if [ -n "$sleep_pid" ]; then kill "$sleep_pid" 2>/dev/null || true; fi
	exit 0
}
trap cleanup_own_state EXIT
trap terminate HUP INT TERM
atomic_state "$READY_FILE" "$$" \
	|| { echo "devcrypt-watchdog: failed to publish readiness" >&2; exit 1; }

while true; do
	sleep "$INTERVAL" &
	sleep_pid=$!
	wait "$sleep_pid"
	sleep_pid=

	if ! mountpoint -q "$PLAIN"; then
		exit 0
	fi

	now=$(date +%s)
	if [ -f "$TS_FILE" ]; then
		ts=$(cat "$TS_FILE" 2>/dev/null || echo "$now")
	else
		ts=$now
		printf '%s\n' "$ts" >"$TS_FILE"
	fi
	case "$ts" in ''|*[!0-9]*) ts=$now;; esac

	if (( now - ts >= TIMEOUT )); then
		if open_terminals; then
			printf '%s\n' "$now" >"$TS_FILE"
			continue
		fi
		if ! /usr/local/bin/nested-podman-shutdown.sh; then
			echo 'devcrypt-watchdog: nested workloads remain — refusing encrypted unmount' >&2
			printf '%s\n' "$now" >"$TS_FILE"
			continue
		fi
		echo "devcrypt-watchdog: idle ${TIMEOUT}s — unmounting $PLAIN"
		if ! fusermount -u "$PLAIN"; then
			echo 'devcrypt-watchdog: workspace busy; leaving it mounted, not claiming it is locked' >&2
			printf '%s\n' "$now" >"$TS_FILE"
			continue
		fi
		rm -f -- "$TS_FILE"
		exit 0
	fi
done
