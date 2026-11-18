#!/usr/bin/env python3
"""Mirror explicit nested-Podman TCP publications to host loopback relays.

The watcher is event-driven: it follows container start/died/remove events from
the outer engine and from nested Podman's event log, and reconciles only when
one arrives, plus a slow safety-net reconcile in case an event is missed.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
import pathlib
import selectors
import signal
import socket
import subprocess
import sys
import time
from typing import Any, NamedTuple

VERSION = 1
SOURCE_SHA256 = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
PROFILE = os.environ.get("AGENT_CONTAINER_PROFILE", "default")
CONTAINER = os.environ.get("AGENT_CONTAINER_CONTAINER", "agent-container")
NETWORK = os.environ.get("AGENT_CONTAINER_INTERNAL_NETWORK", "agent-container-internal")
IMAGE = os.environ.get("AGENT_CONTAINER_IMAGE", "localhost/agent-container:dev")
OAUTH_PORT = int(os.environ.get("AGENT_CONTAINER_OAUTH_PORT", "8899"))
# Reserve common callback and proxy bridge ports across instances.
# No callback service or OAuth login is enabled by this reservation.
RESERVED_PORTS = {8899, 8900, OAUTH_PORT, 10256, 10257, 10258, 10259, 10260, 10261}
MANAGED_LABEL = "agent-container.port-relay.managed"
NAME_PREFIX = f"agent-container-{PROFILE}-port-relay-tcp-"


class SnapshotError(RuntimeError):
    pass


class Mapping(NamedTuple):
    host_port: int
    container_port: int
    protocol: str
    container_name: str
    container_id: str


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise SnapshotError(f"invalid {field}")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise SnapshotError(f"invalid {field}") from error
    return result


def _field(entry: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in entry:
            return entry[name]
    return default


def parse_publications(snapshot: Any) -> tuple[dict[int, Mapping], list[dict[str, Any]]]:
    if not isinstance(snapshot, list):
        raise SnapshotError("nested publication snapshot is not a list")
    desired: dict[int, Mapping] = {}
    rejected: list[dict[str, Any]] = []
    for container in snapshot:
        if not isinstance(container, dict):
            raise SnapshotError("nested container entry is malformed")
        identifier = _field(container, "Id", "ID")
        names = _field(container, "Names", "names")
        ports = _field(container, "Ports", "ports")
        if ports is None:
            ports = []
        if not isinstance(identifier, str) or not identifier:
            raise SnapshotError("nested container id is missing")
        if isinstance(names, list) and names and isinstance(names[0], str):
            name = names[0]
        elif isinstance(names, str) and names:
            name = names
        else:
            raise SnapshotError("nested container name is missing")
        if not isinstance(ports, list):
            raise SnapshotError("nested publication list is malformed")
        for publication in ports:
            if not isinstance(publication, dict):
                raise SnapshotError("nested publication entry is malformed")
            host = _integer(_field(publication, "host_port", "HostPort"), "host port")
            target = _integer(
                _field(publication, "container_port", "ContainerPort"), "container port"
            )
            range_size = _integer(_field(publication, "range", "Range", default=1), "range")
            protocol = str(_field(publication, "protocol", "Protocol", default="tcp")).lower()
            host_ip = str(_field(publication, "host_ip", "HostIP", default=""))
            if range_size < 1 or range_size > 65535:
                raise SnapshotError("nested publication range is invalid")
            for offset in range(range_size):
                current_host = host + offset
                current_target = target + offset
                reason: str | None = None
                if not (1 <= current_host <= 65535) or not (1 <= current_target <= 65535):
                    reason = "invalid-port"
                elif current_host < 1024:
                    reason = "privileged-port"
                elif current_host in RESERVED_PORTS:
                    reason = "reserved-port"
                elif protocol != "tcp":
                    reason = "unsupported-protocol"
                elif host_ip not in {"", "0.0.0.0"}:
                    reason = "inner-loopback-bind" if host_ip in {"127.0.0.1", "::1"} else "unsupported-bind"
                if reason:
                    rejected.append(
                        {"port": current_host, "reason": reason, "container": name}
                    )
                    continue
                mapping = Mapping(current_host, current_target, protocol, name, identifier)
                existing = desired.get(current_host)
                if existing is not None and existing != mapping:
                    raise SnapshotError(f"conflicting nested publication for {current_host}")
                desired[current_host] = mapping
    return desired, rejected


def validate_target_ip(address: str, network: Any) -> str:
    try:
        target = ipaddress.ip_address(address)
    except ValueError as error:
        raise SnapshotError("agent-container internal address is not numeric") from error
    if not isinstance(network, dict):
        raise SnapshotError("internal network snapshot is malformed")
    subnets = network.get("subnets", network.get("Subnets"))
    if not isinstance(subnets, list) or not subnets:
        raise SnapshotError("internal network has no subnet")
    parsed_subnets: list[ipaddress._BaseNetwork] = []
    for item in subnets:
        raw = item.get("subnet", item.get("Subnet")) if isinstance(item, dict) else None
        try:
            parsed_subnets.append(ipaddress.ip_network(str(raw), strict=False))
        except ValueError as error:
            raise SnapshotError("internal network subnet is malformed") from error
    if target.is_loopback or not any(target in subnet for subnet in parsed_subnets):
        raise SnapshotError("agent-container address is outside the internal subnet")
    return str(target)


def relay_name(port: int) -> str:
    return f"{NAME_PREFIX}{port}"


def relay_command(
    engine: str, mapping: Mapping, target_ip: str, image: str = IMAGE, network: str = NETWORK
) -> list[str]:
    port = mapping.host_port
    return [
        engine,
        "run",
        "-d",
        "--rm",
        "--name",
        relay_name(port),
        "--label",
        f"{MANAGED_LABEL}=true",
        "--label",
        f"agent-container.port-relay.profile={PROFILE}",
        "--label",
        f"agent-container.port-relay.port={port}",
        "--label",
        f"agent-container.port-relay.target={target_ip}",
        "--network",
        network,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--pids-limit",
        "64",
        "--memory",
        "32m",
        "--cpus",
        "0.25",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=1m,notmpcopyup",
        "-p",
        f"127.0.0.1:{port}:{port}/tcp",
        "--entrypoint",
        "/usr/bin/socat",
        image,
        f"TCP-LISTEN:{port},bind=0.0.0.0,fork,reuseaddr",
        f"TCP:{target_ip}:{port}",
    ]


def make_status(
    desired: dict[int, Mapping],
    rejected: list[dict[str, Any]],
    states: dict[int, str],
    error: str | None = None,
) -> dict[str, Any]:
    ports = [
        {
            "host_port": mapping.host_port,
            "container_port": mapping.container_port,
            "protocol": mapping.protocol,
            "container": mapping.container_name,
            "state": states.get(port, "pending"),
        }
        for port, mapping in sorted(desired.items())
    ]
    result: dict[str, Any] = {
        "version": VERSION,
        "ports": ports,
        "rejected": sorted(rejected, key=lambda item: (item.get("port", 0), item.get("reason", ""))),
    }
    if error:
        result["error"] = error
    return result


class Engine:
    def __init__(self, executable: str):
        resolved = pathlib.Path(executable)
        if not resolved.is_absolute() or not resolved.exists():
            raise SnapshotError("container engine path must be an existing absolute path")
        self.executable = str(resolved)

    def run(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.executable, *arguments],
            check=check,
            text=True,
            capture_output=True,
            timeout=30,
        )

    def json(self, *arguments: str) -> Any:
        result = self.run(*arguments)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise SnapshotError("container engine returned malformed JSON") from error


def _one(value: Any, description: str) -> dict[str, Any]:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise SnapshotError(f"{description} snapshot is malformed")
    return value[0]


def _outer_running(engine: Engine) -> bool:
    result = engine.run("container", "inspect", "-f", "{{.State.Running}}", CONTAINER, check=False)
    return result.returncode == 0 and result.stdout.strip() == "true"


def _target(engine: Engine) -> str:
    container = _one(engine.json("container", "inspect", CONTAINER), "agent-container")
    networks = container.get("NetworkSettings", {}).get("Networks", {})
    entry = networks.get(NETWORK) if isinstance(networks, dict) else None
    address = entry.get("IPAddress", "") if isinstance(entry, dict) else ""
    network = _one(engine.json("network", "inspect", NETWORK), "internal network")
    if network.get("internal", network.get("Internal")) is not True:
        raise SnapshotError("relay network is not internal")
    return validate_target_ip(address, network)


def _nested_snapshot(engine: Engine) -> Any:
    return engine.json(
        "exec",
        CONTAINER,
        "env",
        "XDG_RUNTIME_DIR=/tmp/agent-container-podman",
        "podman",
        "ps",
        "--format",
        "json",
    )


def _managed(engine: Engine) -> list[dict[str, Any]]:
    value = engine.json(
        "ps", "-a",
        "--filter", f"label={MANAGED_LABEL}=true",
        "--format", "json",
    )
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise SnapshotError("managed relay snapshot is malformed")
    selected = []
    for item in value:
        relay_profile = _labels(item).get("agent-container.port-relay.profile", "")
        if relay_profile == PROFILE:
            selected.append(item)
    return selected


def _labels(item: dict[str, Any]) -> dict[str, str]:
    labels = item.get("Labels", item.get("labels", {}))
    return labels if isinstance(labels, dict) else {}


def _names(item: dict[str, Any]) -> list[str]:
    names = item.get("Names", item.get("names", []))
    if isinstance(names, str):
        return [names]
    return [name for name in names if isinstance(name, str)] if isinstance(names, list) else []


def _remove(engine: Engine, name: str) -> None:
    if name.startswith(NAME_PREFIX):
        engine.run("rm", "-f", name, check=False)


def _port_available(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def reconcile(engine: Engine) -> dict[str, Any]:
    if not _outer_running(engine):
        for item in _managed(engine):
            for name in _names(item):
                _remove(engine, name)
        return make_status({}, [], {}, "agent-container-not-running")

    # Complete, valid snapshots are required before any stale relay is changed.
    desired, rejected = parse_publications(_nested_snapshot(engine))
    target_ip = _target(engine)
    existing: dict[int, tuple[str, dict[str, str], str]] = {}
    for item in _managed(engine):
        labels = _labels(item)
        names = _names(item)
        raw_port = labels.get("agent-container.port-relay.port", "")
        if not raw_port.isdigit() or len(names) != 1 or not names[0].startswith(NAME_PREFIX):
            raise SnapshotError("managed relay metadata is malformed")
        port = int(raw_port)
        if port in existing:
            raise SnapshotError("managed relay port is duplicated")
        existing[port] = (names[0], labels, str(item.get("State", item.get("state", ""))))

    states: dict[int, str] = {}
    for port, (name, _labels_value, _state) in list(existing.items()):
        if port not in desired:
            _remove(engine, name)
            del existing[port]

    for port, mapping in sorted(desired.items()):
        current = existing.get(port)
        if current:
            name, labels, state = current
            if labels.get("agent-container.port-relay.target") == target_ip and state.lower() in {"running", "up"}:
                states[port] = "active"
                continue
            _remove(engine, name)
        if not _port_available(port):
            rejected.append({"port": port, "reason": "host-port-in-use", "container": mapping.container_name})
            states[port] = "rejected"
            continue
        result = subprocess.run(
            relay_command(engine.executable, mapping, target_ip),
            text=True,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        states[port] = "active" if result.returncode == 0 else "relay-start-failed"
    return make_status(desired, rejected, states)


def _runtime_dir() -> pathlib.Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/agent-container-port-reconciler-{os.getuid()}"
    directory = pathlib.Path(base) / f"agent-container-port-reconciler-{PROFILE}"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    return directory


def _atomic_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as destination:
        json.dump(value, destination, sort_keys=True)
        destination.write("\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def _identity() -> dict[str, Any]:
    return {"version": VERSION, "source_sha256": SOURCE_SHA256, "pid": os.getpid()}


def _current(identity: dict[str, Any] | None) -> bool:
    return (
        isinstance(identity, dict)
        and identity.get("version") == VERSION
        and identity.get("source_sha256") == SOURCE_SHA256
    )


class WatcherLock:
    """The singleton lock, which also carries the holder's identity.

    Liveness is the lock itself, not a separate marker file: a marker can be
    deleted while its watcher keeps running, and then nothing can tell that a
    watcher exists. The identity lives inside the locked file, is never
    truncated by readers, and is rewritten if the file is ever replaced.
    """

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.fd: int | None = None

    def acquire(self) -> bool:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        if self.fd is not None:
            os.close(self.fd)
        self.fd = fd
        self._write_identity()
        return True

    def _write_identity(self) -> None:
        assert self.fd is not None
        data = (json.dumps(_identity(), sort_keys=True) + "\n").encode("ascii")
        os.ftruncate(self.fd, 0)
        os.pwrite(self.fd, data, 0)

    def heal(self) -> bool:
        """Repair a deleted, replaced, or emptied lock; False if another watcher now owns it."""
        assert self.fd is not None
        try:
            on_disk = os.stat(self.path)
        except FileNotFoundError:
            return self.acquire()
        held = os.fstat(self.fd)
        if (on_disk.st_dev, on_disk.st_ino) != (held.st_dev, held.st_ino):
            return self.acquire()
        if held.st_size == 0:
            self._write_identity()
        return True

    def release(self) -> None:
        if self.fd is None:
            return
        try:
            if self.heal():
                os.ftruncate(self.fd, 0)
        except OSError:
            pass
        os.close(self.fd)
        self.fd = None


def _lock_held(path: pathlib.Path) -> bool:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False


def _read_identity(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _lock_holders(path: pathlib.Path) -> list[int]:
    """PIDs with the lock file open, for a holder that published no identity."""
    target = str(path.resolve())
    holders: set[int] = set()
    proc = pathlib.Path("/proc")
    if (proc / "self").exists():
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                for descriptor in (entry / "fd").iterdir():
                    if os.readlink(descriptor) == target:
                        holders.add(int(entry.name))
                        break
            except OSError:
                continue
    else:
        try:
            result = subprocess.run(
                ["/usr/sbin/lsof", "-t", "--", target],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        holders.update(int(line) for line in result.stdout.split() if line.isdigit())
    holders.discard(os.getpid())
    return sorted(holders)


# Outer events: agent-container itself and this profile's relays. Event filters of the
# same key are ORed. exec events are excluded, because every reconcile execs into
# agent-container and would otherwise wake the watcher again.
OUTER_EVENT_ARGUMENTS = (
    "events", "--format", "json",
    "--filter", "type=container",
    "--filter", "event=start",
    "--filter", "event=died",
    "--filter", "event=remove",
)
NESTED_EVENTS_LOG = "/tmp/agent-container-podman/libpod/events/events.log"
NESTED_EVENTS_PIDFILE = "/tmp/agent-container/port-reconciler-events.pid"
NESTED_EVENT_STATUSES = {"start", "died", "remove"}
# Nested events are read by tailing nested Podman's file event log rather than
# running `podman events`: a long-lived nested Podman holds its database open
# inside the encrypted workspace, which would pin gocryptfs after an idle
# unmount. tail touches only /tmp. A remote exec outlives its client, so each
# stream replaces the previous one through a pidfile instead of accumulating.
NESTED_EVENTS_SCRIPT = f"""
f={NESTED_EVENTS_PIDFILE}
old=$(cat "$f" 2>/dev/null || true)
case "$old" in ''|*[!0-9]*) ;; *)
    if [ "$old" != "$$" ] && tr '\\0' ' ' <"/proc/$old/cmdline" 2>/dev/null | grep -qF -- '-F {NESTED_EVENTS_LOG}'; then
        kill "$old" 2>/dev/null || true
    fi;;
esac
mkdir -p "${{f%/*}}" && echo "$$" >"$f"
exec tail -n 0 -F {NESTED_EVENTS_LOG}
"""
NESTED_EVENTS_STOP_SCRIPT = f"""
f={NESTED_EVENTS_PIDFILE}
old=$(cat "$f" 2>/dev/null || true)
case "$old" in ''|*[!0-9]*) exit 0;; esac
if tr '\\0' ' ' <"/proc/$old/cmdline" 2>/dev/null | grep -qF -- '-F {NESTED_EVENTS_LOG}'; then
    kill "$old" 2>/dev/null || true
fi
rm -f "$f"
"""
DEBOUNCE_SECONDS = 0.5
MAX_BACKOFF_SECONDS = 60.0
# A stream that ran this long before ending was healthy; restart it promptly.
HEALTHY_STREAM_SECONDS = 60.0
# Wall time racing ahead of monotonic time means the host slept, and a stream
# can stall across sleep without ever reporting end-of-file.
SLEEP_GAP_SECONDS = 5.0


def _event(line: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def outer_event_relevant(line: bytes) -> bool:
    event = _event(line)
    if event is None:
        return True
    name = event.get("Name", "")
    return name == CONTAINER or (isinstance(name, str) and name.startswith(NAME_PREFIX))


def outer_event_started_agent_container(line: bytes) -> bool:
    event = _event(line)
    return event is not None and event.get("Name") == CONTAINER and event.get("Status") == "start"


def nested_event_relevant(line: bytes) -> bool:
    event = _event(line)
    if event is None:
        return True
    return event.get("Type") == "container" and event.get("Status") in NESTED_EVENT_STATUSES


class EventStream:
    def __init__(self, arguments: list[str], relevant: Any):
        self.arguments = arguments
        self.relevant = relevant
        self.process: subprocess.Popen[bytes] | None = None
        self.buffer = b""
        self.retry_at = 0.0
        self.backoff = 1.0
        self.started_at = 0.0

    @property
    def running(self) -> bool:
        return self.process is not None

    def start(self, selector: selectors.BaseSelector, now: float) -> None:
        try:
            self.process = subprocess.Popen(
                self.arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError:
            self._schedule_retry(now, lived=0.0)
            return
        assert self.process.stdout is not None
        os.set_blocking(self.process.stdout.fileno(), False)
        selector.register(self.process.stdout, selectors.EVENT_READ, self)
        self.buffer = b""
        self.started_at = now

    def read(self) -> tuple[list[bytes], bool]:
        assert self.process is not None and self.process.stdout is not None
        try:
            data = os.read(self.process.stdout.fileno(), 65536)
        except BlockingIOError:
            return [], False
        except OSError:
            return [], True
        if not data:
            return [], True
        self.buffer += data
        *lines, self.buffer = self.buffer.split(b"\n")
        return [line for line in lines if line.strip()], False

    def stop(self, selector: selectors.BaseSelector, now: float, failed: bool) -> None:
        if self.process is None:
            return
        process, self.process = self.process, None
        assert process.stdout is not None
        try:
            selector.unregister(process.stdout)
        except (KeyError, ValueError):
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()
        if failed:
            self._schedule_retry(now, lived=now - self.started_at)

    def restart_now(self, selector: selectors.BaseSelector, now: float) -> None:
        self.stop(selector, now, failed=False)
        self.backoff = 1.0
        self.retry_at = now

    def _schedule_retry(self, now: float, lived: float) -> None:
        if lived >= HEALTHY_STREAM_SECONDS:
            self.backoff = 1.0
        self.retry_at = now + self.backoff
        self.backoff = min(self.backoff * 2, MAX_BACKOFF_SECONDS)


def _reconcile_status(engine: Engine) -> dict[str, Any]:
    try:
        return reconcile(engine)
    except (SnapshotError, subprocess.SubprocessError, OSError):
        return make_status({}, [], {}, "snapshot-unavailable")


def _publish_status(path: pathlib.Path, status: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if status != previous or not path.exists():
        _atomic_json(path, status)
    return status


def watch(engine_path: str, interval: float) -> int:
    runtime = _runtime_dir()
    lock = WatcherLock(runtime / "lock")
    # `dev` probes the lock briefly to see whether a watcher exists.
    for _ in range(20):
        if lock.acquire():
            break
        time.sleep(0.05)
    else:
        return 0
    # Superseded markers from the polling watcher; liveness is the lock now.
    for filename in ("ready.json", "pid"):
        (runtime / filename).unlink(missing_ok=True)

    engine = Engine(engine_path)
    stopping = False
    owner = True
    wake_read, wake_write = socket.socketpair()
    wake_read.setblocking(False)
    wake_write.setblocking(False)

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    def request_reconcile(_signum: int, _frame: Any) -> None:
        nonlocal poked
        poked = True

    poked = False
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    # `dev` pokes a running watcher once nested Podman is set up: a snapshot
    # taken earlier is refused, and no event follows setup itself.
    signal.signal(signal.SIGUSR1, request_reconcile)
    signal.set_wakeup_fd(wake_write.fileno(), warn_on_full_buffer=False)

    selector = selectors.DefaultSelector()
    selector.register(wake_read, selectors.EVENT_READ, None)
    outer = EventStream([engine.executable, *OUTER_EVENT_ARGUMENTS], outer_event_relevant)
    nested = EventStream(
        [engine.executable, "exec", CONTAINER, "sh", "-c", NESTED_EVENTS_SCRIPT],
        nested_event_relevant,
    )
    status_path = runtime / "status.json"
    status: dict[str, Any] | None = None
    agent_container_running = False
    now = time.monotonic()
    reconcile_at: float | None = now
    next_poll = now + interval
    last_wall, last_monotonic = time.time(), now
    try:
        while not stopping:
            now = time.monotonic()
            wall = time.time()
            if (wall - last_wall) - (now - last_monotonic) > SLEEP_GAP_SECONDS:
                outer.restart_now(selector, now)
                nested.restart_now(selector, now)
                reconcile_at = now
            last_wall, last_monotonic = wall, now
            if poked:
                poked = False
                reconcile_at = now

            # A stream reports only what happens after it starts, so anything that
            # changed while it was down is picked up by one reconcile right after.
            if not outer.running and now >= outer.retry_at:
                outer.start(selector, now)
                if outer.running and reconcile_at is None:
                    reconcile_at = now + DEBOUNCE_SECONDS
            if agent_container_running and not nested.running and now >= nested.retry_at:
                nested.start(selector, now)
                if nested.running and reconcile_at is None:
                    reconcile_at = now + DEBOUNCE_SECONDS

            if (reconcile_at is not None and now >= reconcile_at) or now >= next_poll:
                status = _publish_status(status_path, _reconcile_status(engine), status)
                agent_container_running = status.get("error") != "agent-container-not-running"
                if not agent_container_running:
                    nested.stop(selector, now, failed=False)
                if not lock.heal():
                    owner = False
                    return 0
                now = time.monotonic()
                reconcile_at = None
                next_poll = now + interval

            deadlines = [next_poll]
            if reconcile_at is not None:
                deadlines.append(reconcile_at)
            if not outer.running:
                deadlines.append(outer.retry_at)
            if agent_container_running and not nested.running:
                deadlines.append(nested.retry_at)
            timeout = max(0.0, min(deadlines) - time.monotonic())
            for key, _mask in selector.select(timeout):
                now = time.monotonic()
                if key.data is None:
                    try:
                        while wake_read.recv(4096):
                            pass
                    except (BlockingIOError, InterruptedError):
                        pass
                    continue
                stream = key.data
                lines, ended = stream.read()
                if any(stream.relevant(line) for line in lines):
                    soon = now + DEBOUNCE_SECONDS
                    reconcile_at = soon if reconcile_at is None else min(reconcile_at, soon)
                if stream is outer and any(outer_event_started_agent_container(line) for line in lines):
                    nested.restart_now(selector, now)
                if ended:
                    stream.stop(selector, now, failed=True)
                    soon = now + DEBOUNCE_SECONDS
                    reconcile_at = soon if reconcile_at is None else min(reconcile_at, soon)
    finally:
        now = time.monotonic()
        outer.stop(selector, now, failed=False)
        nested.stop(selector, now, failed=False)
        if owner:
            try:
                engine.run("exec", CONTAINER, "sh", "-c", NESTED_EVENTS_STOP_SCRIPT, check=False)
            except (subprocess.SubprocessError, OSError):
                pass
            try:
                for item in _managed(engine):
                    for name in _names(item):
                        _remove(engine, name)
            except (SnapshotError, subprocess.SubprocessError, OSError):
                pass
            lock.release()
        signal.set_wakeup_fd(-1)
        selector.close()
        wake_read.close()
        wake_write.close()
    return 0


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def _process_arguments(pid: int) -> list[bytes] | None:
    if pathlib.Path("/proc/self").exists():
        try:
            return pathlib.Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        except OSError:
            return None
    # macOS has no procfs; the watcher runs on the Mac host there, so read its
    # command line from ps instead.
    try:
        result = subprocess.run(
            ["/bin/ps", "-o", "command=", "-p", str(pid)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.split()


def _owned_watcher(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid() or not _pid_alive(pid):
        return False
    arguments = _process_arguments(pid)
    if arguments is None:
        return False
    return any(argument.endswith(b"port_reconciler.py") for argument in arguments)


def _watch_environment() -> dict[str, str]:
    environment = {
        "HOME": os.environ.get("HOME", ""),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "AGENT_CONTAINER_PROFILE": PROFILE,
        "AGENT_CONTAINER_CONTAINER": CONTAINER,
        "AGENT_CONTAINER_INTERNAL_NETWORK": NETWORK,
        "AGENT_CONTAINER_OAUTH_PORT": str(OAUTH_PORT),
        "AGENT_CONTAINER_IMAGE": IMAGE,
    }
    # The macOS launchers pin the podman machine connection; keep the watcher
    # on the same machine as the dev.sh that started it.
    if os.environ.get("CONTAINER_CONNECTION"):
        environment["CONTAINER_CONNECTION"] = os.environ["CONTAINER_CONNECTION"]
    if os.environ.get("XDG_RUNTIME_DIR"):
        environment["XDG_RUNTIME_DIR"] = os.environ["XDG_RUNTIME_DIR"]
        environment["DBUS_SESSION_BUS_ADDRESS"] = (
            f"unix:path={os.environ['XDG_RUNTIME_DIR']}/bus"
        )
    return environment


def _stop_watcher(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)
    # A stopping watcher removes its relays first, which takes a few engine calls.
    for _ in range(100):
        if not _pid_alive(pid):
            return
        time.sleep(0.1)
    if _owned_watcher(pid):
        os.kill(pid, signal.SIGKILL)


def _running_current_watcher(path: pathlib.Path) -> bool:
    if not _lock_held(path):
        return False
    identity = _read_identity(path)
    return _current(identity) and _owned_watcher(int(identity.get("pid", 0)))


def _spawn_watcher(engine_path: str, interval: float) -> None:
    subprocess.Popen(
        [sys.executable, str(pathlib.Path(__file__).resolve()), "watch", "--engine", engine_path, "--interval", str(interval)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env=_watch_environment(),
    )


def start(engine_path: str, interval: float) -> int:
    runtime = _runtime_dir()
    lock_path = runtime / "lock"
    if _lock_held(lock_path):
        identity = None
        # A watcher that has just taken the lock writes its identity at once.
        for _ in range(10):
            identity = _read_identity(lock_path)
            if identity is not None:
                break
            time.sleep(0.1)
        pid = int(identity.get("pid", 0)) if identity else 0
        if _current(identity) and _owned_watcher(pid):
            # Only a current watcher handles SIGUSR1; it would end an older one.
            os.kill(pid, signal.SIGUSR1)
            return 0
        # Outdated watcher, or one that predates identity in the lock.
        candidates = [pid] if _owned_watcher(pid) else _lock_holders(lock_path)
        holders = [candidate for candidate in candidates if _owned_watcher(candidate)]
        if not holders:
            raise SnapshotError(f"{lock_path} is locked by a process that is not a port reconciler")
        for holder in holders:
            _stop_watcher(holder)
    _spawn_watcher(engine_path, interval)
    for _ in range(100):
        if _running_current_watcher(lock_path):
            return 0
        time.sleep(0.1)
    raise SnapshotError("port reconciler did not become ready")


def stop(engine_path: str) -> int:
    lock_path = _runtime_dir() / "lock"
    if _lock_held(lock_path):
        identity = _read_identity(lock_path)
        pid = int(identity.get("pid", 0)) if identity else 0
        if not _owned_watcher(pid):
            raise SnapshotError("watcher ownership cannot be verified")
        _stop_watcher(pid)
    engine = Engine(engine_path)
    for item in _managed(engine):
        for name in _names(item):
            _remove(engine, name)
    return 0


def show_status() -> int:
    path = _runtime_dir() / "status.json"
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("Nested application port reconciler is not running.")
        return 1
    print("Nested application ports:")
    ports = status.get("ports", [])
    if not ports:
        print("  none")
    for item in ports:
        print(
            f"  127.0.0.1:{item['host_port']} -> "
            f"{item['container']}:{item['container_port']}/{item['protocol']} [{item['state']}]"
        )
    for item in status.get("rejected", []):
        print(f"  rejected {item.get('port')}: {item.get('reason')} ({item.get('container')})")
    if status.get("error"):
        print(f"  status: {status['error']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "watch", "stop", "once"):
        command = subparsers.add_parser(name)
        command.add_argument("--engine", required=True)
        # Safety-net reconcile period; events drive every other reconcile.
        command.add_argument("--interval", type=float, default=60.0)
    subparsers.add_parser("status")
    arguments = parser.parse_args()
    try:
        if arguments.command == "start":
            return start(arguments.engine, arguments.interval)
        if arguments.command == "watch":
            return watch(arguments.engine, arguments.interval)
        if arguments.command == "stop":
            return stop(arguments.engine)
        if arguments.command == "once":
            print(json.dumps(reconcile(Engine(arguments.engine))))
            return 0
        return show_status()
    except SnapshotError as error:
        print(f"port-reconciler: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
