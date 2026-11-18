#!/usr/bin/env bash
set -u

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P) || exit 1
SETUP=$SCRIPT_DIR/nested-podman-setup.sh
DOCKERFILE=$SCRIPT_DIR/../Dockerfile
failures=0
fail() { printf 'FAIL: %s\n' "$1" >&2; failures=$((failures + 1)); }
file_mode() {
	if stat -c '%a' "$1" >/dev/null 2>&1; then stat -c '%a' "$1"; else stat -f '%Lp' "$1"; fi
}

[ -x "$SETUP" ] || { echo 'FAIL: nested-podman-setup.sh is missing' >&2; exit 1; }
[ -f "$DOCKERFILE" ] || { echo 'FAIL: Dockerfile is missing' >&2; exit 1; }
grep -F "dev:1:999" "$DOCKERFILE" >/dev/null \
	|| fail 'image does not expose the low outer subordinate-ID range to nested Podman.'
grep -F "dev:1001:64536" "$DOCKERFILE" >/dev/null \
	|| fail 'image does not expose the high outer subordinate-ID range to nested Podman.'

sandbox=$(mktemp -d "${TMPDIR:-/tmp}/nested-setup.XXXXXX") || exit 1
trap 'rm -rf "$sandbox"' EXIT HUP INT TERM
mkdir -p "$sandbox/bin" "$sandbox/system-bin" "$sandbox/home/dev" "$sandbox/run"
for utility in bash cat chmod mkdir mktemp mv rm; do
	ln -s "$(command -v "$utility")" "$sandbox/system-bin/$utility"
done
events=$sandbox/events
boot_id_file=$sandbox/boot-id
printf '%s\n' '11111111-1111-4111-8111-111111111111' >"$boot_id_file"

cat >"$sandbox/bin/mountpoint" <<'SH'
#!/usr/bin/env bash
[ "${MOUNTED:-0}" = 1 ]
SH
cat >"$sandbox/bin/podman" <<'SH'
#!/usr/bin/env bash
printf 'podman %s\n' "$*" >>"${EVENTS:?}"
if [ "${1:-}" = unshare ] && [ "${2:-}" = mountpoint ]; then
	[ "${NESTED_SEES_MOUNT:-1}" = 1 ]; exit
fi
[ "${PODMAN_FAIL:-0}" = 0 ]
SH
cat >"$sandbox/bin/bridge" <<'SH'
#!/usr/bin/env bash
printf 'bridge %s\n' "$*" >>"${EVENTS:?}"
SH
cat >"$sandbox/bin/fuse-overlayfs" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$sandbox/bin/"*

run_setup() {
	env PATH="$sandbox/bin:$sandbox/system-bin" HOME="$sandbox/home" EVENTS="$events" \
		MOUNTED="${MOUNTED:-1}" PODMAN_FAIL="${PODMAN_FAIL:-0}" NESTED_SEES_MOUNT="${NESTED_SEES_MOUNT:-1}" \
		AGENT_CONTAINER_NESTED_TEST_MODE=1 DEVCRYPT_PLAIN="$sandbox/home/dev" \
		AGENT_CONTAINER_PODMAN_RUNROOT="$sandbox/run" AGENT_CONTAINER_BOOT_ID_FILE="$boot_id_file" \
		NESTED_PROXY_BRIDGE="$sandbox/bin/bridge" \
		bash "$SETUP"
}

status=0
MOUNTED=0 run_setup >"$sandbox/unmounted.out" 2>"$sandbox/unmounted.err" || status=$?
[ "$status" -ne 0 ] || fail 'setup accepted an unmounted encrypted workspace.'
grep -F 'encrypted workspace is not mounted' "$sandbox/unmounted.err" >/dev/null \
	|| fail 'unmounted failure was not clear.'

status=0
mv "$sandbox/bin/fuse-overlayfs" "$sandbox/bin/fuse-overlayfs.off"
run_setup >"$sandbox/no-overlay.out" 2>"$sandbox/no-overlay.err" || status=$?
mv "$sandbox/bin/fuse-overlayfs.off" "$sandbox/bin/fuse-overlayfs"
[ "$status" -ne 0 ] || fail 'setup accepted a missing fuse-overlayfs helper.'
grep -F 'fuse-overlayfs is unavailable' "$sandbox/no-overlay.err" >/dev/null \
	|| fail 'missing fuse-overlayfs failure was not clear.'

: >"$events"
mkdir -p "$sandbox/home/dev/.podman/storage"
printf 'preserve me\n' >"$sandbox/home/dev/.podman/storage/legacy-vfs-marker"
run_setup || fail 'valid setup failed.'
storage=$sandbox/home/.config/containers/storage.conf
containers=$sandbox/home/.config/containers/containers.conf
[ "$(file_mode "$storage")" = 600 ] \
	|| fail 'storage.conf is not mode 0600.'
[ "$(file_mode "$containers")" = 600 ] \
	|| fail 'containers.conf is not mode 0600.'
grep -F 'driver="overlay"' "$storage" >/dev/null || fail 'overlay storage is not configured.'
grep -F 'mount_program=' "$storage" | grep -F "$sandbox/bin/fuse-overlayfs" >/dev/null \
	|| fail 'fuse-overlayfs mount program is not configured.'
grep -F "$sandbox/home/dev/.podman/storage-overlay" "$storage" >/dev/null \
	|| fail 'fresh overlay graphroot is not under the encrypted workspace.'
[ -f "$sandbox/home/dev/.podman/storage/legacy-vfs-marker" ] \
	|| fail 'legacy VFS graphroot was modified during the storage transition.'
grep -F "$sandbox/run/runroot" "$storage" >/dev/null \
	|| fail 'runroot is not ephemeral.'
grep -F 'host_containers_internal_ip="10.0.2.2"' "$containers" >/dev/null \
	|| fail 'deterministic parent gateway is missing.'
grep -F 'pasta_options=[' "$containers" >/dev/null \
	|| fail 'deterministic pasta options are missing.'
grep -F 'HTTP_PROXY=http://host.containers.internal:10257' "$containers" >/dev/null \
	|| fail 'credential-free child proxy is missing.'
grep -F '/etc/agent-container-ca/ca-certificates.crt' "$containers" >/dev/null \
	|| fail 'nested CA mount/defaults are missing.'
for trust_path in /etc/agent-container-ca/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt /cacert.pem; do
	grep -F "destination=${trust_path},ro" "$containers" >/dev/null \
		|| fail "nested CA mounts omit $trust_path."
done
for trust_var in SSL_CERT_FILE REQUESTS_CA_BUNDLE NODE_EXTRA_CA_CERTS AWS_CA_BUNDLE CURL_CA_BUNDLE GIT_SSL_CAINFO; do
	grep -F "${trust_var}=/etc/agent-container-ca/ca-certificates.crt" "$containers" >/dev/null \
		|| fail "nested trust defaults omit $trust_var."
done
if grep -Ei '(secretref:|proxy-authorization|secret-proxy:|[0-9a-f]{48})' "$storage" "$containers" >/dev/null; then
	fail 'persistent nested configuration contains a secret/proxy capability.'
fi
grep -F 'bridge start' "$events" >/dev/null || fail 'proxy bridge was not started.'
grep -F 'podman info' "$events" >/dev/null || fail 'podman health was not checked.'
[ "$(cat "$sandbox/run/host-boot-id")" = "$(cat "$boot_id_file")" ] \
	|| fail 'current host boot ID was not cached in ephemeral runtime state.'

mkdir -p "$sandbox/run/runroot/stale" "$sandbox/run/libpod/stale"
printf '%s\n' '00000000-0000-0000-0000-000000000000' >"$sandbox/run/host-boot-id"
run_setup || fail 'setup did not recover runtime metadata after a host reboot.'
[ ! -e "$sandbox/run/runroot/stale" ] && [ ! -e "$sandbox/run/libpod/stale" ] \
	|| fail 'stale nested runtime metadata survived a host boot-ID change.'

before=$(shasum -a 256 "$storage" "$containers")
run_setup || fail 'idempotent setup rerun failed.'
after=$(shasum -a 256 "$storage" "$containers")
[ "$before" = "$after" ] || fail 'idempotent setup changed configuration bytes.'

status=0
PODMAN_FAIL=1 run_setup >"$sandbox/podman.out" 2>"$sandbox/podman.err" || status=$?
[ "$status" -ne 0 ] || fail 'setup ignored podman info failure.'

# A nested namespace created before the workspace mount sees the bare mountpoint
# and would store images there unencrypted, so setup must refuse it.
grep -F "podman unshare mountpoint -q $sandbox/home/dev" "$events" >/dev/null \
	|| fail 'setup did not check that nested Podman sees the encrypted workspace.'
status=0
NESTED_SEES_MOUNT=0 run_setup >"$sandbox/hidden.out" 2>"$sandbox/hidden.err" || status=$?
[ "$status" -ne 0 ] || fail 'setup accepted a nested namespace that cannot see the workspace.'
grep -F 'cannot see the encrypted workspace' "$sandbox/hidden.err" >/dev/null \
	|| fail 'a hidden workspace is not reported actionably.'

status=0
env HOME="$sandbox/home" DEVCRYPT_PLAIN="$sandbox/home/dev" \
	AGENT_CONTAINER_PODMAN_RUNROOT="$sandbox/run" bash "$SETUP" >/dev/null 2>"$sandbox/override.err" || status=$?
[ "$status" -eq 64 ] || fail 'runtime path overrides were not rejected outside test mode.'

[ "$failures" -eq 0 ] || exit 1
printf 'PASS: nested Podman setup is encrypted, credential-free, strict, and idempotent.\n'
