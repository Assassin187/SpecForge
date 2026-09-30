"""Independent, exact-wire MQTT subset acceptance (standard library only).

This file also runs directly inside a bubblewrap stage, without importing
the model runtime. Each scenario starts and stops its own broker process.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import queue
import re
import shutil
import signal
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path

TEST_IDS = (
    "mqtt_cross_client_pubsub", "mqtt_buffer_before_eof", "mqtt_malformed_survival",
    "mqtt_disconnect_handler", "mqtt_smoke_test", "mqtt_mosquitto_interop",
    "mqtt_connect_semantics", "mqtt_suback_contract", "mqtt_exact_binary_payload",
    "mqtt_plus_filter_boundaries", "mqtt_hash_filter_boundaries", "mqtt_fragmented_input",
    "mqtt_coalesced_input", "mqtt_ping_exchange", "mqtt_disconnect_subscription_cleanup",
    "mqtt_invalid_packet_connection_isolation",
)


class EnvironmentBlocked(RuntimeError):
    pass


def remaining(length: int) -> bytes:
    data = bytearray()
    while True:
        value = length % 128
        length //= 128
        data.append(value | (128 if length else 0))
        if not length:
            return bytes(data)


def frame(header: int, body: bytes = b"") -> bytes:
    return bytes([header]) + remaining(len(body)) + body


def string(value: str) -> bytes:
    data = value.encode("utf-8")
    return struct.pack("!H", len(data)) + data


def connect_packet(client_id: str) -> bytes:
    return frame(0x10, string("MQTT") + b"\x04\x02\x00\x0a" + string(client_id))


def subscribe_packet(filters: list[str], identifier: int = 7) -> bytes:
    return frame(0x82, struct.pack("!H", identifier) + b"".join(string(f) + b"\0" for f in filters))


def publish_packet(topic: str, payload: bytes) -> bytes:
    return frame(0x30, string(topic) + payload)


def exact(sock: socket.socket, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise AssertionError(f"Unexpected EOF: wanted {count}, received {len(data)} bytes")
        data.extend(chunk)
    return bytes(data)


class Client:
    def __init__(self, case: "Case", client_id: str, handshake: bool = True):
        self.case, self.client_id = case, client_id
        self.sock = socket.create_connection(("127.0.0.1", case.port), timeout=2)
        self.sock.settimeout(2)
        case.clients.append(self)
        if handshake:
            self.send(connect_packet(client_id))
            self.expect(0x20, b"\0\0")

    def send(self, data: bytes):
        self.case.events.append({"client": self.client_id, "send_hex": data.hex()})
        self.sock.sendall(data)

    def receive(self) -> tuple[int, bytes]:
        head = exact(self.sock, 1)[0]
        length, multiplier = 0, 1
        for i in range(4):
            octet = exact(self.sock, 1)[0]
            length += (octet & 127) * multiplier
            if not octet & 128:
                break
            multiplier *= 128
        else:
            raise AssertionError("Invalid output Remaining Length")
        if length > 1024 * 1024:
            raise AssertionError("Unexpectedly large output frame")
        body = exact(self.sock, length)
        self.case.events.append({"client": self.client_id, "received_header": head, "received_body_hex": body.hex()})
        return head, body

    def expect(self, header: int, body: bytes):
        self.case.events.append({"client": self.client_id, "expected_header": header, "expected_body_hex": body.hex()})
        actual = self.receive()
        assert actual == (header, body), f"Packet mismatch: expected {(header, body.hex())}, got {(actual[0], actual[1].hex())}"

    def subscribe(self, filters: list[str], identifier: int = 7):
        self.send(subscribe_packet(filters, identifier))
        self.expect(0x90, struct.pack("!H", identifier) + b"\0" * len(filters))

    def publish(self, topic: str, payload: bytes):
        self.send(publish_packet(topic, payload))

    def expect_publish(self, topic: str, payload: bytes):
        self.expect(0x30, string(topic) + payload)

    def quiet(self, timeout: float = 0.12):
        self.sock.settimeout(timeout)
        try:
            data = self.sock.recv(1)
        except socket.timeout:
            return
        finally:
            self.sock.settimeout(2)
        raise AssertionError(f"Expected no data, got {data.hex() if data else 'EOF'}")

    def closed(self):
        try:
            assert self.sock.recv(1) == b"", "Invalid connection remained open or returned unexpected data"
        except ConnectionResetError:
            pass

    def disconnect(self):
        self.send(frame(0xE0))
        self.closed()
        self.sock.close()


class Case:
    def __init__(self, binary: Path, directory: Path, reference: bool):
        self.binary, self.directory, self.reference = binary, directory, reference
        self.clients, self.events = [], []
        self.process = None
        self.directory.mkdir(parents=True, exist_ok=True)
        self.output = None

    def __enter__(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.output = (self.directory / "broker.log").open("w")
        argv = [str(self.binary), "-p", str(self.port)] if self.reference else [str(self.binary), str(self.port)]
        environment = {**os.environ, "ASAN_OPTIONS": "detect_leaks=1:halt_on_error=1", "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1"}
        self.process = subprocess.Popen(argv, stdout=self.output, stderr=subprocess.STDOUT, env=environment)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.__exit__(None, None, None)
                raise AssertionError("Broker exited before startup; see broker.log")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                    return self
            except OSError:
                time.sleep(0.02)
        self.__exit__(None, None, None)
        raise AssertionError("Broker did not listen within startup deadline")

    def __exit__(self, exc_type, exc, traceback):
        for client in self.clients:
            client.sock.close()
        termination_error = None
        if self.process:
            if self.process.poll() is None:
                self.process.send_signal(signal.SIGTERM)
            try:
                code = self.process.wait(timeout=4)
                if code != 0:
                    termination_error = f"Broker exited with {code}"
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
                termination_error = "Broker failed to terminate gracefully"
        if self.output:
            self.output.close()
        log = (self.directory / "broker.log").read_text(errors="replace")
        if re.search(r"AddressSanitizer|LeakSanitizer|runtime error:|UndefinedBehaviorSanitizer", log):
            termination_error = "Sanitizer diagnostics in broker.log"
        (self.directory / "events.json").write_text(json.dumps(self.events, indent=2) + "\n")
        if termination_error:
            raise AssertionError(termination_error)

    def client(self, name: str, handshake: bool = True) -> Client:
        return Client(self, name, handshake)

    def pair(self, topic: str = "sf/test") -> tuple[Client, Client]:
        sub, pub = self.client("subscriber"), self.client("publisher")
        sub.subscribe([topic])
        return sub, pub


def cross_client(c: Case):
    sub, pub = c.pair()
    pub.publish("sf/test", b"hello")
    sub.expect_publish("sf/test", b"hello")


def before_eof(c: Case):
    sub, pub = c.pair()
    pub.publish("sf/test", b"before-eof")
    pub.sock.shutdown(socket.SHUT_WR)
    sub.expect_publish("sf/test", b"before-eof")


def malformed_survival(c: Case):
    bad = c.client("bad", False)
    bad.send(frame(0x10, b"\0"))
    bad.closed()
    cross_client(c)


def disconnect_handler(c: Case):
    sub, pub = c.pair()
    doomed = c.client("doomed")
    doomed.subscribe(["sf/test"])
    doomed.disconnect()
    pub.publish("sf/test", b"still-alive")
    sub.expect_publish("sf/test", b"still-alive")


def smoke(c: Case):
    for number in range(4):
        sub, pub = c.client(f"sub{number}"), c.client(f"pub{number}")
        topic = f"smoke/{number}"
        sub.subscribe([topic])
        payload = f"cycle-{number}".encode()
        pub.publish(topic, payload)
        sub.expect_publish(topic, payload)
        sub.disconnect()
        pub.disconnect()


def interop(c: Case):
    if not shutil.which("mosquitto_pub") or not shutil.which("mosquitto_sub"):
        raise EnvironmentBlocked("mosquitto_pub/sub are required")
    argv = ["stdbuf", "-oL", "-eL", "mosquitto_sub", "-h", "127.0.0.1", "-p", str(c.port), "-V", "mqttv311", "-q", "0",
            "-t", "sf/interop", "-C", "1", "-W", "4", "-d"]
    subscriber = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines, updates = [], queue.Queue()

    def collect():
        for line in subscriber.stdout:
            lines.append(line.rstrip("\n"))
            updates.put(line)

    thread = threading.Thread(target=collect, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 4
        while True:
            try:
                line = updates.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                raise AssertionError("Mosquitto subscriber did not acknowledge subscription")
            if "Subscribed (mid:" in line:
                break
            if time.monotonic() >= deadline:
                raise AssertionError("Mosquitto subscriber handshake failed")
        result = subprocess.run(["mosquitto_pub", "-h", "127.0.0.1", "-p", str(c.port), "-V", "mqttv311", "-q", "0",
                                 "-t", "sf/interop", "-m", "specforge-interop"], capture_output=True, text=True, timeout=4)
        assert result.returncode == 0, f"Mosquitto publisher failed: {result.stderr}"
        assert subscriber.wait(timeout=5) == 0, "Mosquitto subscriber failed"
        thread.join(timeout=1)
        assert "specforge-interop" in lines, f"Missing exact payload line: {lines}"
    finally:
        if subscriber.poll() is None:
            subscriber.kill()
            subscriber.wait()
        thread.join(timeout=1)
        subscriber.stdout.close()
        c.events.append({"mosquitto_command": argv, "subscriber_output": lines})


def connect_semantics(c: Case):
    good = c.client("connected")
    first = c.client("first", False)
    first.send(frame(0xC0))
    first.closed()
    good.send(connect_packet("connected"))
    good.closed()
    c.client("new-client")


def suback(c: Case):
    sub = c.client("multi")
    sub.subscribe(["multi/one", "multi/two", "multi/+/three"], 0x1245)
    pub = c.client("multi-pub")
    for topic in ("multi/one", "multi/two", "multi/a/three"):
        pub.publish(topic, b"ok")
        sub.expect_publish(topic, b"ok")


def binary_payload(c: Case):
    sub, pub = c.pair("Case/Topic")
    pub.publish("case/Topic", b"wrong-case")
    sub.quiet()
    for payload in (b"\0\xff\x80binary\0tail", b""):
        pub.publish("Case/Topic", payload)
        sub.expect_publish("Case/Topic", payload)


def plus_filter(c: Case):
    sub, pub = c.pair("lab/+/temp")
    for topic in ("lab/a/temp", "lab//temp", "lab/b/temp"):
        pub.publish(topic, topic.encode())
        sub.expect_publish(topic, topic.encode())
    for topic in ("lab/a/x/temp", "lab/temp"):
        pub.publish(topic, b"no-match")
        sub.quiet()


def hash_filter(c: Case):
    sub, pub = c.pair("lab/device/#")
    for topic in ("lab/device", "lab/device/temp", "lab/device/room/temp"):
        pub.publish(topic, topic.encode())
        sub.expect_publish(topic, topic.encode())
    pub.publish("lab/device2/temp", b"no-match")
    sub.quiet()


def fragmented(c: Case):
    sub = c.client("fragment-sub", False)
    packet = connect_packet("fragment-sub")
    sub.send(packet[:2])
    sub.quiet()
    sub.send(packet[2:-1])
    sub.quiet()
    sub.send(packet[-1:])
    sub.expect(0x20, b"\0\0")
    packet = subscribe_packet(["fragment/topic"])
    sub.send(packet[:5])
    sub.quiet()
    sub.send(packet[5:])
    sub.expect(0x90, b"\0\x07\0")
    pub = c.client("fragment-pub")
    payload = bytes(range(256))
    packet = publish_packet("fragment/topic", payload)
    pub.send(packet[:2])  # Split multi-byte Remaining Length.
    sub.quiet()
    pub.send(packet[2:8])
    sub.quiet()
    pub.send(packet[8:-1])
    sub.quiet()
    pub.send(packet[-1:])
    sub.expect_publish("fragment/topic", payload)


def coalesced(c: Case):
    sub, pub = c.pair("batch")
    pub.send(publish_packet("batch", b"one") + publish_packet("batch", b"two") + frame(0xC0))
    sub.expect_publish("batch", b"one")
    sub.expect_publish("batch", b"two")
    pub.expect(0xD0, b"")


def ping(c: Case):
    sub, pub = c.pair()
    pub.send(frame(0xC0))
    pub.expect(0xD0, b"")
    pub.publish("sf/test", b"after-ping")
    sub.expect_publish("sf/test", b"after-ping")


def subscription_cleanup(c: Case):
    sub, pub = c.pair("cleanup")
    sub.disconnect()
    new = c.client("subscriber")
    pub.publish("cleanup", b"not-subscribed")
    new.quiet()
    new.subscribe(["cleanup"])
    pub.publish("cleanup", b"new-subscription")
    new.expect_publish("cleanup", b"new-subscription")


def isolation(c: Case):
    sub, pub = c.pair("isolation")
    packets = ((b"\x30\x80\x80\x80\x80\x00", True),
               (b"\x80\x06\x00\x07\x00\x01x\x00", True),
               (publish_packet("isolation", b"before-connect"), False))
    for number, (packet, handshake) in enumerate(packets):
        bad = c.client(f"invalid{number}", handshake)
        bad.send(packet)
        bad.closed()
        payload = f"survived-{number}".encode()
        pub.publish("isolation", payload)
        sub.expect_publish("isolation", payload)


SCENARIOS = (cross_client, before_eof, malformed_survival, disconnect_handler, smoke, interop,
             connect_semantics, suback, binary_payload, plus_filter, hash_filter, fragmented,
             coalesced, ping, subscription_cleanup, isolation)


def run_suite(binary: Path, out: Path, *, reference: bool = False, legacy: bool = False) -> dict:
    results = []
    out.mkdir(parents=True, exist_ok=True)
    for name, scenario in list(zip(TEST_IDS, SCENARIOS))[:6 if legacy else 16]:
        started = time.monotonic()
        record = {"id": name, "status": "not_executed"}
        try:
            with Case(binary.resolve(), out / name, reference) as case:
                scenario(case)
            record["status"] = "passed"
        except EnvironmentBlocked as exc:
            record.update(status="environment_blocked", error=str(exc))
        except Exception as exc:
            record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        results.append(record)
        print(f"{name}: {record['status']}" + (f" ({record['error']})" if "error" in record else ""), flush=True)
    report = {"passed": all(r["status"] == "passed" for r in results), "reference_calibration": reference,
              "required_count": 6 if legacy else 16, "scenarios": results,
              "elapsed_seconds": round(sum(r["elapsed_seconds"] for r in results), 3)}
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reference", action="store_true")
    parser.add_argument("--legacy", action="store_true")
    args = parser.parse_args()
    return 0 if run_suite(args.binary, args.out, reference=args.reference, legacy=args.legacy)["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

