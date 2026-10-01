"""Independent CoAP minimum-profile evaluator. Never exposed to generation."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import time
from pathlib import Path

TEST_IDS = ("coap_con_get", "coap_non_get", "coap_binary_put_get", "coap_endpoint_tokens",
            "coap_option_boundaries", "coap_duplicate_con", "coap_empty_con_reset",
            "coap_error_responses", "coap_malformed_survival", "coap_libcoap_interop")


class EnvironmentBlocked(RuntimeError):
    pass


def extension(value: int) -> tuple[int, bytes]:
    if value < 13:
        return value, b""
    if value < 269:
        return 13, bytes([value - 13])
    return 14, struct.pack("!H", value - 269)


def packet(kind: int, code: int, mid: int, token: bytes = b"", options=(), payload: bytes = b"") -> bytes:
    data = bytes([0x40 | kind << 4 | len(token), code]) + struct.pack("!H", mid) + token
    previous = 0
    for number, value in sorted(options, key=lambda p: p[0]):
        delta, extra_delta = extension(number - previous)
        length, extra_length = extension(len(value))
        data += bytes([delta << 4 | length]) + extra_delta + extra_length + value
        previous = number
    return data + (b"\xff" + payload if payload else b"")


def parse(data: bytes) -> dict:
    assert len(data) >= 4 and data[0] >> 6 == 1, f"Bad CoAP header: {data.hex()}"
    token_length = data[0] & 15
    assert token_length <= 8 and len(data) >= 4 + token_length, "Bad Token length"
    result = {"kind": data[0] >> 4 & 3, "code": data[1], "mid": int.from_bytes(data[2:4], "big"),
              "token": data[4:4 + token_length], "options": [], "payload": b""}
    position, number = 4 + token_length, 0

    def extended(nibble: int) -> int:
        nonlocal position
        assert nibble != 15, "Reserved option nibble"
        size = {13: 1, 14: 2}.get(nibble, 0)
        if not size:
            return nibble
        assert position + size <= len(data), "Truncated option extension"
        value = int.from_bytes(data[position:position + size], "big") + (13 if size == 1 else 269)
        position += size
        return value

    while position < len(data):
        initial = data[position]
        position += 1
        if initial == 255:
            assert position < len(data), "Empty payload after marker"
            result["payload"] = data[position:]
            break
        number += extended(initial >> 4)
        length = extended(initial & 15)
        assert position + length <= len(data), "Truncated option value"
        result["options"].append((number, data[position:position + length]))
        position += length
    if result["code"] == 0:
        assert token_length == 0 and len(data) == 4, "Malformed empty message"
    return result


def request(path: str, mid: int, token: bytes = b"sf", *, kind=0, code=1, payload=b"", extra=()) -> bytes:
    options = [(11, level.encode()) for level in path.strip("/").split("/") if level]
    return packet(kind, code, mid, token, [*options, *extra], payload)


class Case:
    def __init__(self, binary: Path, directory: Path):
        self.binary, self.directory = binary, directory
        self.clients, self.events = [], []
        self.process = None
        self.log = None
        self.mid = 100

    def client(self) -> socket.socket:
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.bind(("127.0.0.1", 0))
        client.settimeout(1)
        self.clients.append(client)
        return client

    def exchange(self, client: socket.socket, data: bytes) -> tuple[dict, bytes]:
        client.sendto(data, ("127.0.0.1", self.port))
        response, peer = client.recvfrom(65535)
        assert peer == ("127.0.0.1", self.port), f"Wrong response endpoint: {peer}"
        self.events.append({"endpoint": client.getsockname(), "input": data.hex(), "response": response.hex()})
        return parse(response), response

    def call(self, client=None, path="hello", *, kind=0, code=1, token=b"sf", payload=b"", extra=(), expected=69, expected_payload=None):
        client = client or self.client()
        self.mid += 1
        reply, raw = self.exchange(client, request(path, self.mid, token, kind=kind, code=code, payload=payload, extra=extra))
        assert reply["kind"] == (2 if kind == 0 else 1), reply
        assert reply["token"] == token and reply["code"] == expected, reply
        if kind == 0:
            assert reply["mid"] == self.mid, reply
        if expected_payload is not None:
            assert reply["payload"] == expected_payload, (reply, expected_payload.hex())
        self.events.append({"expected": {"kind": 2 if kind == 0 else 1, "code": expected,
                            "token": token.hex(), "payload": None if expected_payload is None else expected_payload.hex()}})
        return reply, raw

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.log = (self.directory / "server.log").open("w")
        environment = {**os.environ, "ASAN_OPTIONS": "detect_leaks=1:halt_on_error=1", "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1"}
        self.process = subprocess.Popen([str(self.binary), str(self.port)], stdout=self.log, stderr=subprocess.STDOUT, env=environment)
        client = self.client()
        client.settimeout(0.1)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                break
            try:
                self.call(client, expected_payload=b"hello")
                client.settimeout(1)
                return self
            except socket.timeout:
                time.sleep(0.02)
            except Exception:
                self.__exit__(None, None, None)
                raise
        self.__exit__(None, None, None)
        raise AssertionError("Server did not provide a valid startup response")

    def __exit__(self, exc_type, exc, traceback):
        for client in self.clients:
            client.close()
        error = None
        if self.process:
            if self.process.poll() is None:
                self.process.send_signal(signal.SIGTERM)
            try:
                code = self.process.wait(timeout=4)
                if code != 0:
                    error = f"Server exit status {code}"
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
                error = "Server did not terminate normally"
        if self.log:
            self.log.close()
        log = (self.directory / "server.log").read_text(errors="replace")
        if re.search(r"AddressSanitizer|LeakSanitizer|runtime error:|UndefinedBehaviorSanitizer", log):
            error = "Sanitizer diagnostics"
        (self.directory / "events.json").write_text(json.dumps(self.events, indent=2) + "\n")
        if error:
            raise AssertionError(error)


def content_format(reply: dict, expected: int):
    values = [int.from_bytes(v, "big") for n, v in reply["options"] if n == 12]
    assert values == [expected], reply


def con_get(c: Case):
    reply, _ = c.call(expected_payload=b"hello")
    content_format(reply, 0)


def non_get(c: Case):
    c.call(kind=1, expected_payload=b"hello")


def binary_put_get(c: Case):
    client = c.client()
    c.call(client, "value", expected_payload=b"")
    for value in (b"abc\x00\xffxyz", b"", bytes(range(256)) * 4):
        c.call(client, "value", code=3, payload=value, extra=[(12, b"\x2a")], expected=68)
        reply, _ = c.call(client, "value", expected_payload=value)
        content_format(reply, 42)


def endpoint_tokens(c: Case):
    for token in (b"", b"x", bytes(range(8))):
        a, b = c.client(), c.client()
        data = request("hello", 0x2345, token)
        for client in (a, b):
            reply, _ = c.exchange(client, data)
            assert reply["token"] == token and reply["mid"] == 0x2345 and reply["code"] == 69
            assert reply["payload"] == b"hello"
    a, b = c.client(), c.client()
    for client, value in ((a, b"first"), (b, b"second")):
        reply, _ = c.exchange(client, request("value", 0x4567, b"same", code=3, payload=value, extra=[(12, b"\x2a")]))
        assert reply["code"] == 68 and reply["mid"] == 0x4567 and reply["token"] == b"same"
    c.call(b, "value", expected_payload=b"second")


def option_boundaries(c: Case):
    for extra in ([(2048, b"x" * 13)], [(2048, b"x" * 269)]):
        c.call(extra=extra, expected_payload=b"hello")
    c.call(path="unknown/child", expected=132)
    c.call(path="Hello", expected=132)
    c.call(path="x" * 17, expected=132)


def duplicate_con(c: Case):
    client = c.client()
    a = request("value", 0x1234, b"a", code=3, payload=b"A", extra=[(12, b"\x2a")])
    original, raw = c.exchange(client, a)
    assert original["code"] == 68 and original["token"] == b"a" and original["mid"] == 0x1234
    c.call(client, "value", code=3, payload=b"B", extra=[(12, b"\x2a")], expected=68)
    _, replay = c.exchange(client, a)
    assert replay == raw, "Duplicate response changed"
    c.call(client, "value", expected_payload=b"B")


def empty_con(c: Case):
    client = c.client()
    reply, raw = c.exchange(client, packet(0, 0, 0x8765))
    assert raw == bytes.fromhex("70008765"), reply
    for kind in (2, 3):
        client.sendto(packet(kind, 0, 0x8765), ("127.0.0.1", c.port))
    c.call(client, expected_payload=b"hello")


def error_responses(c: Case):
    c.call(path="absent", expected=132)
    c.call(code=2, expected=133)
    c.call(extra=[(2047, b"x")], expected=130)
    c.call(expected_payload=b"hello")


def malformed_survival(c: Case):
    malformed = (b"", b"\x40", bytes.fromhex("49010001"), bytes.fromhex("81010001"),
                 bytes.fromhex("40010002ff"), bytes.fromhex("40010003f0"), bytes.fromhex("40010004de"),
                 bytes.fromhex("40010005b568"), b"\x40\x01\x00\x06" + b"\xee\x00\x00\x00\x00" + b"x" * 60000)
    client = c.client()
    client.settimeout(0.08)
    for data in malformed:
        client.sendto(data, ("127.0.0.1", c.port))
        try:
            response, _ = client.recvfrom(65535)
        except socket.timeout:
            response = b""
        c.events.append({"malformed_input": data[:80].hex(), "input_length": len(data), "response": response.hex()})
        c.call(expected_payload=b"hello")


def libcoap_interop(c: Case):
    binary = shutil.which("coap-client-notls")
    if not binary:
        raise EnvironmentBlocked("coap-client-notls unavailable")
    payload = c.directory / "payload.bin"
    payload.write_bytes(b"from-libcoap\x00\xff")
    commands = [(["-m", "get"], "hello", b"hello"),
                (["-m", "put", "-t", "42", "-f", str(payload)], "value", None),
                (["-m", "get"], "value", payload.read_bytes())]
    for number, (arguments, path, expected) in enumerate(commands):
        output = c.directory / f"client_{number}.bin"
        argv = [binary, "-B", "3", "-v", "3", "-o", str(output), *arguments, f"coap://127.0.0.1:{c.port}/{path}"]
        result = subprocess.run(argv, capture_output=True, timeout=6)
        c.events.append({"command": argv, "exit_code": result.returncode,
                         "stdout": result.stdout.decode(errors="replace"), "stderr": result.stderr.decode(errors="replace")})
        assert result.returncode == 0, result.stderr
        if expected is not None:
            assert output.is_file() and output.read_bytes() == expected


SCENARIOS = (con_get, non_get, binary_put_get, endpoint_tokens, option_boundaries,
             duplicate_con, empty_con, error_responses, malformed_survival, libcoap_interop)


def run_suite(binary: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for name, scenario in zip(TEST_IDS, SCENARIOS):
        started = time.monotonic()
        record = {"id": name, "status": "not_executed"}
        try:
            with Case(binary.resolve(), out / name) as case:
                scenario(case)
            record["status"] = "passed"
        except EnvironmentBlocked as exc:
            record.update(status="environment_blocked", error=str(exc))
        except Exception as exc:
            record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        results.append(record)
        print(name + ": " + record["status"], flush=True)
    report = {"passed": all(r["status"] == "passed" for r in results), "required_count": len(TEST_IDS),
              "scenarios": results, "elapsed_seconds": sum(r["elapsed_seconds"] for r in results)}
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return 0 if run_suite(args.binary, args.out)["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
