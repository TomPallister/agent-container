#!/usr/bin/env bash
set -u

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P) || exit 1
WRAPPER=$SCRIPT_DIR/nested-podman-wrapper.sh
failures=0
fail() { printf 'FAIL: %s\n' "$1" >&2; failures=$((failures + 1)); }

[ -x "$WRAPPER" ] || { echo 'FAIL: nested-podman-wrapper.sh is missing' >&2; exit 1; }
sandbox=$(mktemp -d "${TMPDIR:-/tmp}/nested-podman-wrapper.XXXXXX") || exit 1
trap 'rm -rf "$sandbox"' EXIT HUP INT TERM
cat >"$sandbox/podman" <<'SH'
#!/usr/bin/env bash
printf '%s\0' "$@" >"${EVENTS:?}"
SH
chmod +x "$sandbox/podman"
printf 'public test CA\n' >"$sandbox/ca.pem"

plain_home=$sandbox/plain-home
mkdir -p "$plain_home/.config/containers"
printf 'graphroot="%s/storage"\n' "$sandbox" >"$plain_home/.config/containers/storage.conf"

run_wrapper() {
	env HOME="$plain_home" AGENT_CONTAINER_NESTED_TEST_MODE=1 NESTED_PODMAN_REAL="$sandbox/podman" \
		NESTED_CA_SOURCE="$sandbox/ca.pem" EVENTS="$sandbox/events" \
		bash "$WRAPPER" "$@"
}

run_wrapper ps --all || fail 'ordinary Podman command failed.'
python3 - "$sandbox/events" <<'PY' || fail 'ordinary Podman command was modified.'
import sys
args = open(sys.argv[1], 'rb').read().split(b'\0')[:-1]
raise SystemExit(0 if args == [b'ps', b'--all'] else 1)
PY

run_wrapper build --tag example:test . || fail 'Podman build command failed.'
python3 - "$sandbox/events" "$sandbox/ca.pem" <<'PY' || fail 'build safety arguments were incomplete or malformed.'
import sys
args = [x.decode() for x in open(sys.argv[1], 'rb').read().split(b'\0')[:-1]]
ca = sys.argv[2]
proxy = 'http://host.containers.internal:10257'
required = [
    'build',
    '--network=pasta',
    '--build-arg', f'HTTP_PROXY={proxy}',
    '--build-arg', f'HTTPS_PROXY={proxy}',
    '--build-arg', f'http_proxy={proxy}',
    '--build-arg', f'https_proxy={proxy}',
    '--volume', f'{ca}:/etc/ssl/certs/ca-certificates.crt:ro',
    '--env', 'NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt',
    '--tag', 'example:test', '.',
]
pos = 0
for expected in required:
    try:
        pos = args.index(expected, pos) + 1
    except ValueError:
        raise SystemExit(1)
if any('secretref:' in arg or 'proxy-authorization' in arg for arg in args):
    raise SystemExit(1)
PY

status=0
env NESTED_PODMAN_REAL="$sandbox/podman" NESTED_CA_SOURCE="$sandbox/ca.pem" \
	bash "$WRAPPER" ps >/dev/null 2>"$sandbox/override.err" || status=$?
[ "$status" -eq 64 ] || fail 'runtime accepted test-only path overrides.'

status=0
mv "$sandbox/ca.pem" "$sandbox/ca.pem.off"
run_wrapper build . >/dev/null 2>"$sandbox/ca.err" || status=$?
mv "$sandbox/ca.pem.off" "$sandbox/ca.pem"
[ "$status" -ne 0 ] || fail 'build accepted a missing CA bundle.'
grep -F 'CA bundle is unavailable' "$sandbox/ca.err" >/dev/null \
	|| fail 'missing CA failure was not clear.'

# Podman creates its graphroot eagerly. When that graphroot is configured inside
# the encrypted workspace, running while unmounted recreates it in the bare
# mountpoint and gocryptfs then refuses to mount over it at all.
guard_home=$sandbox/guard-home
mkdir -p "$guard_home/.config/containers" "$guard_home/dev"
mkdir -p "$sandbox/guard-bin"
cat >"$sandbox/guard-bin/mountpoint" <<'SH'
#!/usr/bin/env bash
[ "${MOUNTED:-0}" = 1 ]
SH
chmod +x "$sandbox/guard-bin/mountpoint"
printf 'graphroot="%s/.podman/storage-overlay"\n' "$guard_home/dev" \
	>"$guard_home/.config/containers/storage.conf"

run_guarded() {
	env PATH="$sandbox/guard-bin:$PATH" HOME="$guard_home" MOUNTED="$1" \
		AGENT_CONTAINER_NESTED_TEST_MODE=1 NESTED_PODMAN_REAL="$sandbox/podman" \
		NESTED_CA_SOURCE="$sandbox/ca.pem" DEVCRYPT_PLAIN="$guard_home/dev" \
		EVENTS="$sandbox/events" bash "$WRAPPER" "${@:2}"
}

status=0
run_guarded 0 ps >/dev/null 2>"$sandbox/guard.err" || status=$?
[ "$status" -ne 0 ] || fail 'nested Podman ran while the encrypted workspace was unmounted.'
grep -F 'not mounted' "$sandbox/guard.err" >/dev/null \
	|| fail 'unmounted-workspace refusal was not clear.'
# The whole point is that nothing may be created in the bare mountpoint.
[ -z "$(ls -A "$guard_home/dev")" ] \
	|| fail 'wrapper populated the bare mountpoint while refusing.'

run_guarded 1 ps >/dev/null 2>&1 \
	|| fail 'nested Podman was refused while the workspace was mounted.'

# A graphroot outside the encrypted workspace needs no mount, so it must not be
# gated on one.
printf 'graphroot="%s/elsewhere"\n' "$sandbox" \
	>"$guard_home/.config/containers/storage.conf"
run_guarded 0 ps >/dev/null 2>&1 \
	|| fail 'wrapper gated a graphroot that lives outside the encrypted workspace.'

# Before setup has written storage.conf, nothing may run: Podman would initialise
# default storage and create the namespace the keeper later joins.
unset_home=$sandbox/unset-home
mkdir -p "$unset_home"
status=0
env HOME="$unset_home" AGENT_CONTAINER_NESTED_TEST_MODE=1 NESTED_PODMAN_REAL="$sandbox/podman" \
	NESTED_CA_SOURCE="$sandbox/ca.pem" DEVCRYPT_PLAIN="$unset_home/dev" \
	EVENTS="$sandbox/events.unset" bash "$WRAPPER" ps >/dev/null 2>"$sandbox/unset.err" || status=$?
[ "$status" -ne 0 ] || fail 'nested Podman ran before setup wrote storage.conf.'
[ ! -e "$sandbox/events.unset" ] || fail 'the real Podman was invoked before setup.'
grep -F 'not set up yet' "$sandbox/unset.err" >/dev/null \
	|| fail 'pre-setup refusal was not clear.'

[ "$failures" -eq 0 ] || exit 1
printf 'PASS: nested Podman builds use the credential-free proxy and trusted CA.\n'
