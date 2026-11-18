#!/usr/bin/env python3
import importlib.util
import json
import os
import pathlib
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

MODULE_PATH = pathlib.Path(__file__).with_name("port_reconciler.py")
spec = importlib.util.spec_from_file_location("port_reconciler", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


class PublicationParsingTests(unittest.TestCase):
    def item(self, ports, name="web", identifier="abc123"):
        return {"Id": identifier, "Names": [name], "Ports": ports}

    def port(self, host=4353, container=8080, protocol="tcp", host_ip="", range_=1):
        return {
            "host_ip": host_ip,
            "host_port": host,
            "container_port": container,
            "protocol": protocol,
            "range": range_,
        }

    def test_normalizes_multiple_containers_and_ranges(self):
        snapshot = [
            self.item([self.port(3242, 80)], "one", "id-one"),
            self.item([self.port(5434, 3000), self.port(8373, 8000)], "two", "id-two"),
            self.item([self.port(6000, 9000, range_=2)], "range", "id-range"),
        ]
        desired, rejected = module.parse_publications(snapshot)
        self.assertEqual([3242, 5434, 6000, 6001, 8373], sorted(desired))
        self.assertEqual(9001, desired[6001].container_port)
        self.assertEqual([], rejected)

    def test_rejects_unsupported_or_unsafe_publications(self):
        snapshot = [
            self.item(
                [
                    self.port(0),
                    self.port(80),
                    self.port(8899),
                    self.port(10256),
                    self.port(10257),
                    self.port(70000),
                    self.port(4353, protocol="udp"),
                    self.port(4354, host_ip="127.0.0.1"),
                ]
            )
        ]
        desired, rejected = module.parse_publications(snapshot)
        self.assertEqual({}, desired)
        self.assertEqual(
            [
                "invalid-port",
                "privileged-port",
                "reserved-port",
                "reserved-port",
                "reserved-port",
                "invalid-port",
                "unsupported-protocol",
                "inner-loopback-bind",
            ],
            [entry["reason"] for entry in rejected],
        )

    def test_collapses_compatible_duplicates_and_rejects_conflicts(self):
        same = self.port(4353, 8080)
        desired, _ = module.parse_publications([self.item([same, same])])
        self.assertEqual([4353], list(desired))
        with self.assertRaises(module.SnapshotError):
            module.parse_publications(
                [self.item([self.port(4353, 8080)], "one"), self.item([self.port(4353, 9090)], "two")]
            )

    def test_accepts_running_container_without_publications(self):
        desired, rejected = module.parse_publications(
            [{"Id": "internal-id", "Names": ["internal"], "Ports": None}]
        )
        self.assertEqual({}, desired)
        self.assertEqual([], rejected)

    def test_reserves_both_profile_oauth_ports(self):
        snapshot = [
            {
                "Id": "oauth-id",
                "Names": ["oauth"],
                "Ports": [self.port(8899), self.port(8900)],
            }
        ]
        desired, rejected = module.parse_publications(snapshot)
        self.assertEqual({}, desired)
        self.assertEqual(
            [
                {"port": 8899, "reason": "reserved-port", "container": "oauth"},
                {"port": 8900, "reason": "reserved-port", "container": "oauth"},
            ],
            rejected,
        )

    def test_malformed_snapshot_fails_closed(self):
        malformed = [None, {}, {"Id": "x", "Names": ["x"], "Ports": "4353:80"}]
        for snapshot in malformed:
            with self.subTest(snapshot=snapshot), self.assertRaises(module.SnapshotError):
                module.parse_publications(snapshot)


class ReconciliationTests(unittest.TestCase):
    class FakeEngine:
        executable = "/usr/bin/podman"

        def __init__(self, nested, managed=None):
            self.nested = nested
            self.managed = managed or []
            self.calls = []

        def run(self, *arguments, check=True):
            self.calls.append(arguments)
            if arguments[:3] == ("container", "inspect", "-f"):
                return subprocess.CompletedProcess(arguments, 0, "true\n", "")
            return subprocess.CompletedProcess(arguments, 0, "", "")

        def json(self, *arguments):
            self.calls.append(arguments)
            if arguments[:2] == ("exec", "agent-container"):
                return self.nested
            if arguments[:2] == ("container", "inspect"):
                return [
                    {
                        "NetworkSettings": {
                            "Networks": {"agent-container-internal": {"IPAddress": "10.89.1.63"}}
                        }
                    }
                ]
            if arguments[:2] == ("network", "inspect"):
                return [{"internal": True, "subnets": [{"subnet": "10.89.1.0/24"}]}]
            if arguments and arguments[0] == "ps":
                return self.managed
            raise AssertionError(arguments)

    def publication(self, host=4353, container=8080):
        return [
            {
                "Id": "nested-id",
                "Names": ["web"],
                "Ports": [
                    {
                        "host_ip": "",
                        "host_port": host,
                        "container_port": container,
                        "protocol": "tcp",
                        "range": 1,
                    }
                ],
            }
        ]

    def test_managed_relays_require_explicit_instance_ownership(self):
        managed = [
            {"Names": ["legacy"], "Labels": {"agent-container.port-relay.managed": "true"}},
            {"Names": ["selected"], "Labels": {"agent-container.port-relay.profile": module.PROFILE}},
            {"Names": ["personal"], "Labels": {"agent-container.port-relay.profile": "personal"}},
        ]
        selected = module._managed(self.FakeEngine([], managed))
        self.assertEqual(["selected"], [item["Names"][0] for item in selected])

    @mock.patch.object(module, "_port_available", return_value=True)
    @mock.patch.object(module.subprocess, "run")
    def test_creates_desired_and_removes_stale_relays(self, run, _available):
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        stale = [
            {
                "Names": [module.relay_name(9999)],
                "Labels": {
                    "agent-container.port-relay.profile": module.PROFILE,
                    "agent-container.port-relay.managed": "true",
                    "agent-container.port-relay.port": "9999",
                    "agent-container.port-relay.target": "10.89.1.63",
                },
                "State": "running",
            }
        ]
        engine = self.FakeEngine(self.publication(), stale)
        status = module.reconcile(engine)
        self.assertIn(("rm", "-f", module.relay_name(9999)), engine.calls)
        self.assertEqual("run", run.call_args.args[0][1])
        self.assertEqual("active", status["ports"][0]["state"])

    @mock.patch.object(module, "_port_available", return_value=True)
    @mock.patch.object(module.subprocess, "run")
    def test_reuses_matching_running_relay(self, run, _available):
        managed = [
            {
                "Names": [module.relay_name(4353)],
                "Labels": {
                    "agent-container.port-relay.profile": module.PROFILE,
                    "agent-container.port-relay.managed": "true",
                    "agent-container.port-relay.port": "4353",
                    "agent-container.port-relay.target": "10.89.1.63",
                },
                "State": "running",
            }
        ]
        status = module.reconcile(self.FakeEngine(self.publication(), managed))
        run.assert_not_called()
        self.assertEqual("active", status["ports"][0]["state"])

    def test_invalid_complete_snapshot_does_not_remove_existing_relays(self):
        engine = self.FakeEngine("not-json", [{"Names": ["agent-container-port-relay-tcp-4353"]}])
        with self.assertRaises(module.SnapshotError):
            module.reconcile(engine)
        self.assertFalse(any(call and call[0] == "rm" for call in engine.calls))


class WatcherEnvironmentTests(unittest.TestCase):
    def test_preserves_selected_profile_for_detached_watcher(self):
        with mock.patch.multiple(
            module,
            PROFILE="personal",
            CONTAINER="agent-container-personal",
            NETWORK="agent-container-internal-personal",
            OAUTH_PORT=8900,
        ), mock.patch.dict(
            module.os.environ,
            {"HOME": "/home/test", "XDG_RUNTIME_DIR": "/run/user/1000"},
            clear=True,
        ):
            environment = module._watch_environment()
        self.assertEqual("personal", environment["AGENT_CONTAINER_PROFILE"])
        self.assertEqual("agent-container-personal", environment["AGENT_CONTAINER_CONTAINER"])
        self.assertEqual("agent-container-internal-personal", environment["AGENT_CONTAINER_INTERNAL_NETWORK"])
        self.assertEqual("8900", environment["AGENT_CONTAINER_OAUTH_PORT"])
        self.assertEqual("/run/user/1000", environment["XDG_RUNTIME_DIR"])
        self.assertEqual(
            "unix:path=/run/user/1000/bus", environment["DBUS_SESSION_BUS_ADDRESS"]
        )


class RelayPolicyTests(unittest.TestCase):
    def test_validates_numeric_target_against_internal_subnet(self):
        network = {"subnets": [{"subnet": "10.89.1.0/24"}]}
        self.assertEqual("10.89.1.63", module.validate_target_ip("10.89.1.63", network))
        for address in ["", "agent-container", "127.0.0.1", "10.89.2.3", "not-an-ip"]:
            with self.subTest(address=address), self.assertRaises(module.SnapshotError):
                module.validate_target_ip(address, network)

    def test_builds_hardened_loopback_only_relay_command(self):
        mapping = module.Mapping(4353, 8080, "tcp", "web", "abc123")
        command = module.relay_command(
            "/usr/bin/podman", mapping, "10.89.1.63", "agent-container:latest", "agent-container-internal"
        )
        rendered = " ".join(command)
        self.assertIn("--cap-drop ALL", rendered)
        self.assertIn("--security-opt no-new-privileges", rendered)
        self.assertIn("--read-only", command)
        self.assertIn("/tmp:rw,nosuid,nodev,noexec,size=1m,notmpcopyup", command)
        self.assertIn("127.0.0.1:4353:4353/tcp", command)
        self.assertEqual("TCP:10.89.1.63:4353", command[-1])
        for forbidden in ["--privileged", "--network host", "/var/run", "/home/dev/dev"]:
            self.assertNotIn(forbidden, rendered)

    def test_status_rendering_is_sanitized_and_deterministic(self):
        status = module.make_status(
            {4353: module.Mapping(4353, 8080, "tcp", "web", "secret-looking-id")},
            [{"port": 8899, "reason": "reserved-port", "container": "oauth"}],
            {4353: "active"},
        )
        encoded = json.dumps(status, sort_keys=True)
        self.assertNotIn("proxy", encoded.lower())
        self.assertNotIn("environment", encoded.lower())
        self.assertEqual(4353, status["ports"][0]["host_port"])


    def test_recognises_a_live_watcher_process_on_this_host(self):
        # The readiness handshake depends on identifying the detached watcher.
        # macOS has no procfs, so this must work through ps there as well.
        import pathlib as _pathlib
        import sys as _sys
        import tempfile as _tempfile

        with _tempfile.TemporaryDirectory() as directory:
            decoy = _pathlib.Path(directory) / "port_reconciler.py"
            decoy.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
            owned = subprocess.Popen([_sys.executable, str(decoy)])
            other = subprocess.Popen([_sys.executable, "-c", "import time; time.sleep(30)"])
            try:
                self.assertTrue(module._owned_watcher(owned.pid))
                self.assertFalse(module._owned_watcher(other.pid))
            finally:
                for process in (owned, other):
                    process.kill()
                    process.wait()
        self.assertFalse(module._owned_watcher(owned.pid))

    def test_detached_watcher_keeps_the_pinned_machine_connection(self):
        with mock.patch.dict(
            module.os.environ,
            {"HOME": "/home/test", "CONTAINER_CONNECTION": "agent-container"},
            clear=True,
        ):
            environment = module._watch_environment()
        self.assertEqual("agent-container", environment["CONTAINER_CONNECTION"])
        with mock.patch.dict(module.os.environ, {"HOME": "/home/test"}, clear=True):
            self.assertNotIn("CONTAINER_CONNECTION", module._watch_environment())



DECOY_HOLDER = """
import fcntl, json, os, signal, sys, time
signal.signal(signal.SIGUSR1, lambda *_: print("poked", flush=True))
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
if sys.argv[2] != "-":
    os.ftruncate(fd, 0)
    identity = json.loads(sys.argv[2])
    identity["pid"] = os.getpid()
    os.pwrite(fd, json.dumps(identity).encode(), 0)
print("held", flush=True)
time.sleep(60)
"""


class SingletonLockTests(unittest.TestCase):
    """The lock alone proves a watcher is alive; marker files are not trusted."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.directory.name)
        self.lock_path = self.root / "lock"
        self.processes = []
        self.environment = mock.patch.dict(
            module.os.environ, {"XDG_RUNTIME_DIR": str(self.root)}, clear=False
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait()
            if process.stdout:
                process.stdout.close()
        self.directory.cleanup()

    def holder(self, identity, lock_path=None):
        # Named port_reconciler.py so it is recognised as a watcher process.
        decoy = self.root / "decoy" / "port_reconciler.py"
        decoy.parent.mkdir(exist_ok=True)
        decoy.write_text(DECOY_HOLDER, encoding="utf-8")
        process = subprocess.Popen(
            [sys.executable, str(decoy), str(lock_path or self.lock_path),
             "-" if identity is None else json.dumps(identity)],
            stdout=subprocess.PIPE,
            text=True,
        )
        self.processes.append(process)
        self.assertEqual("held", process.stdout.readline().strip())
        return process

    def current_identity(self):
        return {"version": module.VERSION, "source_sha256": module.SOURCE_SHA256}

    def test_lock_carries_identity_and_excludes_a_second_holder(self):
        lock = module.WatcherLock(self.lock_path)
        self.assertTrue(lock.acquire())
        identity = module._read_identity(self.lock_path)
        self.assertTrue(module._current(identity))
        self.assertEqual(os.getpid(), identity["pid"])
        self.assertFalse(module.WatcherLock(self.lock_path).acquire())
        lock.release()
        self.assertFalse(module._lock_held(self.lock_path))

    def test_heal_restores_a_deleted_or_emptied_lock(self):
        lock = module.WatcherLock(self.lock_path)
        self.assertTrue(lock.acquire())
        self.lock_path.unlink()
        self.assertTrue(lock.heal())
        self.assertTrue(module._lock_held(self.lock_path))
        self.assertTrue(module._current(module._read_identity(self.lock_path)))
        os.truncate(self.lock_path, 0)
        self.assertTrue(lock.heal())
        self.assertTrue(module._current(module._read_identity(self.lock_path)))
        lock.release()

    def test_heal_yields_when_another_watcher_took_a_replaced_lock(self):
        lock = module.WatcherLock(self.lock_path)
        self.assertTrue(lock.acquire())
        self.lock_path.unlink()
        self.holder(self.current_identity())
        self.assertFalse(lock.heal())

    def test_finds_holders_of_a_lock_without_identity(self):
        process = self.holder(None)
        self.assertIn(process.pid, module._lock_holders(self.lock_path))

    def test_start_accepts_a_live_current_watcher_without_marker_files(self):
        # Regression: a running watcher whose ready.json had been deleted made
        # every later `dev` fail with "did not become ready".
        runtime = module._runtime_dir()
        holder = self.holder(self.current_identity(), runtime / "lock")
        self.assertFalse((runtime / "ready.json").exists())
        with mock.patch.object(module, "_spawn_watcher") as popen:
            self.assertEqual(0, module.start("/bin/true", 60.0))
        popen.assert_not_called()
        # ...and it is asked to reconcile now that setup has finished.
        ready, _, _ = select.select([holder.stdout], [], [], 5)
        self.assertTrue(ready, "the running watcher was not poked")
        self.assertEqual("poked", holder.stdout.readline().strip())

    def test_start_replaces_a_watcher_that_predates_lock_identity(self):
        runtime = module._runtime_dir()
        legacy = self.holder(None, runtime / "lock")
        replacements = []

        def spawn(*_arguments, **_keywords):
            replacements.append(self.holder(self.current_identity(), runtime / "lock"))
            return replacements[-1]

        # The replacement can only take the lock once the legacy holder is gone.
        original_stop = module._stop_watcher

        def stop(pid):
            original_stop(pid)
            legacy.wait(timeout=5)

        with mock.patch.object(module, "_spawn_watcher", side_effect=spawn), \
                mock.patch.object(module, "_stop_watcher", side_effect=stop):
            self.assertEqual(0, module.start("/bin/true", 60.0))
        self.assertIsNotNone(legacy.poll())
        self.assertEqual(1, len(replacements))

    def test_start_refuses_a_lock_held_by_something_else(self):
        runtime = module._runtime_dir()
        fd = os.open(runtime / "lock", os.O_RDWR | os.O_CREAT, 0o600)
        script = "import fcntl,sys,time; fcntl.flock(int(sys.argv[1]), fcntl.LOCK_EX); print('held', flush=True); time.sleep(60)"
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(fd)], pass_fds=(fd,), stdout=subprocess.PIPE, text=True
        )
        os.close(fd)
        self.processes.append(process)
        self.assertEqual("held", process.stdout.readline().strip())
        with mock.patch.object(module, "_spawn_watcher") as popen, \
                self.assertRaises(module.SnapshotError):
            module.start("/bin/true", 60.0)
        popen.assert_not_called()


class EventFilterTests(unittest.TestCase):
    def event(self, **fields):
        return json.dumps(fields).encode()

    def test_outer_events_are_limited_to_agent_container_and_own_relays(self):
        with mock.patch.multiple(module, CONTAINER="agent-container-personal",
                                 NAME_PREFIX="agent-container-personal-port-relay-tcp-"):
            relevant = module.outer_event_relevant
            self.assertTrue(relevant(self.event(Name="agent-container-personal", Status="died")))
            self.assertTrue(relevant(self.event(Name="agent-container-personal-port-relay-tcp-3000")))
            self.assertFalse(relevant(self.event(Name="agent-container-port-relay-tcp-3000")))
            self.assertFalse(relevant(self.event(Name="agent-container", Status="start")))
            self.assertFalse(relevant(self.event(Name="secret-proxy-personal-relay")))
            self.assertTrue(relevant(b"not json"))
            self.assertTrue(module.outer_event_started_agent_container(
                self.event(Name="agent-container-personal", Status="start")))
            self.assertFalse(module.outer_event_started_agent_container(
                self.event(Name="agent-container-personal", Status="died")))

    def test_nested_events_are_container_lifecycle_only(self):
        relevant = module.nested_event_relevant
        for status in ("start", "died", "remove"):
            self.assertTrue(relevant(self.event(Type="container", Status=status)))
        for status in ("exec", "exec_died", "health_status", "create", "init"):
            self.assertFalse(relevant(self.event(Type="container", Status=status)))
        self.assertFalse(relevant(self.event(Type="image", Status="remove")))
        self.assertTrue(relevant(b"{truncated"))

    def test_outer_stream_excludes_exec_events(self):
        arguments = " ".join(module.OUTER_EVENT_ARGUMENTS)
        self.assertNotIn("exec", arguments)
        for status in ("start", "died", "remove"):
            self.assertIn(f"--filter event={status}", arguments)

    def test_nested_stream_reads_only_the_event_log_under_tmp(self):
        # A long-lived nested `podman events` would hold its database open
        # inside the encrypted workspace and pin gocryptfs after unmount.
        script = module.NESTED_EVENTS_SCRIPT
        self.assertIn("exec tail -n 0 -F /tmp/agent-container-podman/libpod/events/events.log", script)
        self.assertNotIn("podman", script.replace("/tmp/agent-container-podman/", ""))


class EventStreamTests(unittest.TestCase):
    def test_failed_streams_back_off_exponentially_up_to_a_cap(self):
        stream = module.EventStream(["/nonexistent/engine"], lambda _line: True)
        selector = module.selectors.DefaultSelector()
        retries = []
        now = 100.0
        for _ in range(10):
            stream.start(selector, now)
            self.assertFalse(stream.running)
            retries.append(stream.retry_at - now)
        self.assertEqual([1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0], retries[:8])

    def test_a_long_healthy_stream_restarts_promptly(self):
        stream = module.EventStream(["/nonexistent/engine"], lambda _line: True)
        stream.backoff = 60.0
        stream._schedule_retry(1000.0, lived=module.HEALTHY_STREAM_SECONDS)
        self.assertEqual(1001.0, stream.retry_at)

    def test_reads_complete_lines_and_reports_end_of_stream(self):
        stream = module.EventStream(
            [sys.executable, "-c", "import sys; sys.stdout.write('one\\ntw'); sys.stdout.flush()"],
            lambda _line: True,
        )
        selector = module.selectors.DefaultSelector()
        stream.start(selector, 0.0)
        lines, ended = [], False
        deadline = time.monotonic() + 5
        while not ended and time.monotonic() < deadline:
            if selector.select(1):
                chunk, ended = stream.read()
                lines.extend(chunk)
        self.assertEqual([b"one"], lines)
        self.assertTrue(ended)
        stream.stop(selector, 0.0, failed=True)
        self.assertFalse(stream.running)


class StatusPublicationTests(unittest.TestCase):
    def test_writes_status_only_when_it_changes_or_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "status.json"
            status = module.make_status({}, [], {})
            with mock.patch.object(module, "_atomic_json", wraps=module._atomic_json) as write:
                previous = module._publish_status(path, status, None)
                previous = module._publish_status(path, dict(status), previous)
                self.assertEqual(1, write.call_count)
                path.unlink()
                previous = module._publish_status(path, dict(status), previous)
                self.assertEqual(2, write.call_count)
                module._publish_status(path, module.make_status({}, [], {}, "agent-container-not-running"), previous)
                self.assertEqual(3, write.call_count)


FAKE_ENGINE = r"""#!/bin/bash
all="$*"
printf '%s\n' "${all//$'\n'/ }" >>"$FAKE_DIR/calls"
case "$1" in
events) exec tail -n 0 -f "$FAKE_DIR/outer.events" ;;
exec)
	if [ "$3" = sh ]; then
		case "$5" in *"exec tail"*) exec tail -n 0 -f "$FAKE_DIR/nested.events" ;; esac
		exit 0
	fi
	cat "$FAKE_DIR/nested-ps.json" ;;
container)
	if [ "$3" = -f ]; then echo true; else
		echo '[{"NetworkSettings":{"Networks":{"agent-container-internal":{"IPAddress":"10.89.1.63"}}}}]'
	fi ;;
network) echo '[{"internal": true, "subnets": [{"subnet": "10.89.1.0/24"}]}]' ;;
ps) echo '[]' ;;
esac
exit 0
"""


class EventDrivenWatcherTests(unittest.TestCase):
    """Run the real watcher against a fake engine and count engine calls."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.directory.name)
        self.engine = self.root / "podman"
        self.engine.write_text(FAKE_ENGINE, encoding="utf-8")
        self.engine.chmod(0o755)
        for name in ("calls", "outer.events", "nested.events"):
            (self.root / name).touch()
        (self.root / "nested-ps.json").write_text("[]", encoding="utf-8")
        self.runtime = self.root / "agent-container-port-reconciler-work"
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "FAKE_DIR": str(self.root),
            "XDG_RUNTIME_DIR": str(self.root),
            "AGENT_CONTAINER_PROFILE": "work",
            "AGENT_CONTAINER_CONTAINER": "agent-container",
            "AGENT_CONTAINER_INTERNAL_NETWORK": "agent-container-internal",
        }
        self.watcher = subprocess.Popen(
            [sys.executable, str(MODULE_PATH), "watch", "--engine", str(self.engine),
             "--interval", "3600"],
            env=environment,
            stdin=subprocess.DEVNULL,
        )
        # Registered as cleanups so they also run when setUp itself fails.
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.stop_watcher)
        # Startup: reconcile, then once more after each stream starts.
        self.wait_for(lambda: self.snapshots() >= 1 and self.streams() == 2)
        self.settle()

    def stop_watcher(self):
        if self.watcher.poll() is None:
            self.watcher.kill()
        self.watcher.wait()
        subprocess.run(["pkill", "-f", f"tail -n 0 -f {self.root}/"], check=False)

    def calls(self):
        return (self.root / "calls").read_text(encoding="utf-8").splitlines()

    def snapshots(self):
        return sum(1 for call in self.calls() if call.startswith("ps -a"))

    def streams(self):
        calls = self.calls()
        return sum(1 for call in calls if call.startswith("events")) + sum(
            1 for call in calls if call.startswith("exec agent-container sh -c") and "exec tail" in call
        )

    def wait_for(self, condition, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.05)
        self.fail(f"condition not met; engine calls: {self.calls()}")

    def settle(self, seconds=1.5):
        # Longer than the debounce, so any pending reconcile has run.
        time.sleep(seconds)

    def append(self, name, **event):
        with (self.root / name).open("a", encoding="utf-8") as destination:
            destination.write(json.dumps(event) + "\n")

    def test_idle_watcher_makes_no_engine_calls_and_no_writes(self):
        calls = len(self.calls())
        status = self.runtime / "status.json"
        written = status.stat().st_mtime_ns
        self.settle(3)
        self.assertEqual(calls, len(self.calls()))
        self.assertEqual(written, status.stat().st_mtime_ns)

    def test_relevant_events_reconcile_once_per_burst(self):
        before = self.snapshots()
        for _ in range(5):
            self.append("nested.events", Type="container", Status="start", Name="web")
        self.wait_for(lambda: self.snapshots() == before + 1)
        self.settle()
        self.assertEqual(before + 1, self.snapshots())
        self.append("outer.events", Name="agent-container-work-port-relay-tcp-4000", Status="died")
        self.wait_for(lambda: self.snapshots() == before + 2)

    def test_irrelevant_events_are_ignored(self):
        before = self.snapshots()
        self.append("nested.events", Type="container", Status="exec_died", Name="web")
        self.append("nested.events", Type="image", Status="pull", Name="node")
        self.append("outer.events", Name="secret-proxy-relay", Status="start")
        self.append("outer.events", Name="agent-container-personal-port-relay-tcp-3000", Status="died")
        self.settle()
        self.assertEqual(before, self.snapshots())

    def test_new_publications_create_relays(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        (self.root / "nested-ps.json").write_text(json.dumps([{
            "Id": "nested-id", "Names": ["web"],
            "Ports": [{"host_ip": "", "host_port": port, "container_port": 8080,
                       "protocol": "tcp", "range": 1}],
        }]), encoding="utf-8")
        self.append("nested.events", Type="container", Status="start", Name="web")
        self.wait_for(lambda: any(
            call.startswith("run -d") and f"127.0.0.1:{port}:{port}/tcp" in call
            for call in self.calls()))
        # status.json is published after the relay command returns, so wait for it.
        self.wait_for(lambda: [port] == [
            item["host_port"] for item in
            json.loads((self.runtime / "status.json").read_text(encoding="utf-8"))["ports"]])

    def test_a_deleted_lock_is_restored_on_the_next_reconcile(self):
        lock = self.runtime / "lock"
        lock.unlink()
        self.append("nested.events", Type="container", Status="died", Name="web")
        self.wait_for(lambda: lock.exists() and module._read_identity(lock) is not None)
        self.assertEqual(self.watcher.pid, module._read_identity(lock)["pid"])
        self.assertTrue(module._lock_held(lock))

    def test_a_dropped_stream_is_restarted(self):
        streams = self.streams()
        subprocess.run(["pkill", "-f", f"tail -n 0 -f {self.root}/outer.events"], check=False)
        self.wait_for(lambda: self.streams() == streams + 1)

    def test_a_poke_reconciles_immediately(self):
        before = self.snapshots()
        os.kill(self.watcher.pid, signal.SIGUSR1)
        self.wait_for(lambda: self.snapshots() == before + 1)

    def test_agent_container_starting_restarts_the_nested_stream(self):
        # A restarted agent-container has a new nested event log, so follow it at once
        # rather than waiting for the old stream to fail and back off.
        nested = lambda: sum(1 for call in self.calls()
                             if call.startswith("exec agent-container sh -c") and "exec tail" in call)
        before = nested()
        self.append("outer.events", Name="agent-container", Status="start")
        self.wait_for(lambda: nested() == before + 1)

    def test_terminate_stops_streams_and_releases_the_lock(self):
        self.watcher.terminate()
        self.assertEqual(0, self.watcher.wait(timeout=10))
        self.assertTrue(any(call.startswith("exec agent-container sh -c") and "kill" in call
                            and "exec tail" not in call for call in self.calls()))
        self.assertFalse(module._lock_held(self.runtime / "lock"))
        self.settle(0.5)
        leftover = subprocess.run(["pgrep", "-f", f"tail -n 0 -f {self.root}/"],
                                  capture_output=True, text=True)
        self.assertEqual("", leftover.stdout.strip())


if __name__ == "__main__":
    unittest.main()
