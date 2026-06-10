from __future__ import annotations

import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .models import Diagnostic, SpecBundle
from .specs import canonical_signature_for_header, normalize_repo_path


@dataclass
class VerificationResult:
    ok: bool
    diagnostics: list[Diagnostic]
    compile_stdout: str = ""
    compile_stderr: str = ""
    scenarios: list[dict[str, str]] = field(default_factory=list)


def _bundle_slug(bundle: SpecBundle) -> str:
    return re.sub(r"[^a-z0-9]+", "_", bundle.protocol.name.lower()).strip("_") or "protocol"


def _bundle_binary_name(bundle: SpecBundle) -> str:
    roles = [role.lower() for role in bundle.protocol.roles]
    suffix = "broker" if "broker" in roles else "server" if "server" in roles else "app"
    return f"{_bundle_slug(bundle)}_{suffix}"


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _enc_rl(length: int) -> bytes:
    out = bytearray()
    while True:
        digit = length % 128
        length //= 128
        out.append(digit | (0x80 if length else 0))
        if not length:
            return bytes(out)


def _enc_str(value: str) -> bytes:
    raw = value.encode("utf-8")
    return len(raw).to_bytes(2, "big") + raw


def _pkt_connect(client_id: str) -> bytes:
    body = _enc_str("MQTT") + b"\x04\x02\x00\x0a" + _enc_str(client_id)
    return b"\x10" + _enc_rl(len(body)) + body


def _pkt_subscribe(packet_id: int, topic_filter: str) -> bytes:
    body = packet_id.to_bytes(2, "big") + _enc_str(topic_filter) + b"\x00"
    return b"\x82" + _enc_rl(len(body)) + body


def _pkt_publish(topic: str, payload: bytes) -> bytes:
    body = _enc_str(topic) + payload
    return b"\x30" + _enc_rl(len(body)) + body


def _read_exact(sock: socket.socket, size: int) -> bytes:
    out = bytearray()
    while len(out) < size:
        data = sock.recv(size - len(out))
        if not data:
            raise ConnectionError("socket closed while reading packet")
        out.extend(data)
    return bytes(out)


def _read_mqtt_packet(sock: socket.socket) -> tuple[int, bytes]:
    packet_type = _read_exact(sock, 1)[0] >> 4
    multiplier = 1
    remaining = 0
    while True:
        digit = _read_exact(sock, 1)[0]
        remaining += (digit & 0x7F) * multiplier
        if digit & 0x80 == 0:
            break
        multiplier *= 128
    return packet_type, _read_exact(sock, remaining) if remaining else b""


def _free_port(sock_type: int) -> int:
    with socket.socket(socket.AF_INET, sock_type) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_tcp(port: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise TimeoutError(f"server did not listen on TCP port {port}")


def _coap_request(path: str, message_id: int, token: bytes = b"\xaa") -> bytes:
    out = bytearray([0x40 | len(token), 0x01, message_id >> 8, message_id & 0xFF])
    out.extend(token)
    previous = 0
    for segment in [item for item in path.split("/") if item]:
        raw = segment.encode("utf-8")
        delta = 11 - previous
        out.append((delta << 4) | len(raw))
        out.extend(raw)
        previous = 11
    return bytes(out)


def _coap_ext(data: bytes, offset: int, nibble: int) -> tuple[int, int]:
    if nibble < 13:
        return nibble, offset
    if nibble == 13 and offset < len(data):
        return 13 + data[offset], offset + 1
    if nibble == 14 and offset + 1 < len(data):
        return 269 + int.from_bytes(data[offset : offset + 2], "big"), offset + 2
    raise ValueError("invalid CoAP option extension")


def _decode_coap(data: bytes) -> dict[str, object]:
    if len(data) < 4:
        raise ValueError("truncated CoAP response")
    token_len = data[0] & 0x0F
    offset = 4
    token = data[offset : offset + token_len]
    offset += token_len
    option_number = 0
    options: list[tuple[int, bytes]] = []
    while offset < len(data) and data[offset] != 0xFF:
        byte = data[offset]
        offset += 1
        delta, offset = _coap_ext(data, offset, byte >> 4)
        length, offset = _coap_ext(data, offset, byte & 0x0F)
        option_number += delta
        if offset + length > len(data):
            raise ValueError("truncated CoAP option")
        options.append((option_number, data[offset : offset + length]))
        offset += length
    payload = data[offset + 1 :] if offset < len(data) and data[offset] == 0xFF else b""
    return {
        "type": (data[0] >> 4) & 0x03,
        "code": data[1],
        "message_id": int.from_bytes(data[2:4], "big"),
        "token": token,
        "options": options,
        "payload": payload,
    }


class ProjectVerifier:
    def __init__(self, bundle: SpecBundle, output_dir: str | Path) -> None:
        self.bundle = bundle
        self.output_dir = Path(output_dir)
        nested = self.output_dir / _bundle_slug(bundle)
        self.project_dir = nested if nested.exists() else self.output_dir

    def verify_structure(self) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        expected_paths = {"Makefile"}
        for module in self.bundle.modules_in_order:
            expected_paths.update(normalize_repo_path(path) for path in module.files)
        for relative_path in sorted(expected_paths):
            if not (self.project_dir / relative_path).exists():
                diagnostics.append(Diagnostic("error", "missing_generated_file", f"Missing generated file '{relative_path}'", relative_path))

        for file_spec in self.bundle.file_specs_by_trace.values():
            if not file_spec.header_path:
                continue
            header_path = self.project_dir / file_spec.header_path
            if not header_path.is_file():
                continue
            content = _normalize_text(header_path.read_text(encoding="utf-8"))
            for interface in file_spec.header_interfaces:
                canonical = _normalize_text(canonical_signature_for_header(self.bundle, file_spec, interface))
                if canonical not in content:
                    diagnostics.append(Diagnostic("error", "missing_signature", f"Header '{file_spec.header_path}' does not contain '{interface.name}' with canonical signature", file_spec.header_path))
        return diagnostics

    def compile_project(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["make", _bundle_binary_name(self.bundle)], cwd=self.project_dir, text=True, capture_output=True, check=False)

    def verify(self) -> VerificationResult:
        diagnostics = self.verify_structure()
        compile_result = self.compile_project()
        if compile_result.returncode != 0:
            diagnostics.append(Diagnostic("error", "compile_failed", "Generated project failed to compile", "Makefile"))
            return VerificationResult(False, diagnostics, compile_result.stdout, compile_result.stderr)
        behavior = self.verify_behavior()
        diagnostics.extend(behavior.diagnostics)
        return VerificationResult(not any(item.level == "error" for item in diagnostics), diagnostics, compile_result.stdout, compile_result.stderr, behavior.scenarios)

    def verify_behavior(self) -> VerificationResult:
        slug = _bundle_slug(self.bundle)
        scenarios: list[dict[str, str]] = []
        expected = {
            "mqtt": ["mqtt_cross_client_pubsub", "mqtt_buffer_before_eof", "mqtt_malformed_survival", "mqtt_mosquitto_interop"],
            "coap": ["coap_get_hello", "coap_unknown_not_found", "coap_extended_option", "coap_client_interop"],
        }.get(slug, [])
        try:
            if slug == "mqtt":
                self._mqtt_smoke(scenarios)
            elif slug == "coap":
                self._coap_smoke(scenarios)
            else:
                scenarios.append({"name": "runtime_smoke", "status": "skipped", "detail": f"no verifier for protocol '{slug}'"})
        except Exception as exc:  # noqa: BLE001
            failed_index = len(scenarios)
            failed_name = expected[failed_index] if failed_index < len(expected) else "runtime_smoke"
            scenarios.append({"name": failed_name, "status": "failed", "detail": str(exc)})
            for name in expected[failed_index + 1 :]:
                scenarios.append({"name": name, "status": "skipped", "detail": f"not run after {failed_name} failed"})
            return VerificationResult(False, [Diagnostic("error", "behavior_verification_failed", str(exc), _bundle_binary_name(self.bundle))], scenarios=scenarios)
        return VerificationResult(True, [], scenarios=scenarios)

    def _start(self, port: int) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [f"./{_bundle_binary_name(self.bundle)}", str(port)],
            cwd=self.project_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @staticmethod
    def _stop(process: subprocess.Popen[bytes]) -> None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    @staticmethod
    def _mqtt_client(port: int, client_id: str) -> socket.socket:
        sock = socket.create_connection(("127.0.0.1", port), timeout=2)
        sock.settimeout(2)
        sock.sendall(_pkt_connect(client_id))
        if _read_mqtt_packet(sock)[0] != 2:
            raise RuntimeError("MQTT client did not receive CONNACK")
        return sock

    def _mqtt_smoke(self, scenarios: list[dict[str, str]]) -> None:
        port = _free_port(socket.SOCK_STREAM)
        process = self._start(port)
        sockets: list[socket.socket] = []
        try:
            _wait_for_tcp(port)
            subscriber = self._mqtt_client(port, "smoke-sub")
            sockets.append(subscriber)
            subscriber.sendall(_pkt_subscribe(1, "a/#"))
            if _read_mqtt_packet(subscriber)[0] != 9:
                raise RuntimeError("MQTT subscriber did not receive SUBACK")

            publisher = self._mqtt_client(port, "smoke-pub")
            sockets.append(publisher)
            publisher.sendall(_pkt_publish("a/b", b"cross-client"))
            packet_type, body = _read_mqtt_packet(subscriber)
            if packet_type != 3 or b"cross-client" not in body:
                raise RuntimeError("MQTT broker did not forward PUBLISH to another client")
            scenarios.append({"name": "mqtt_cross_client_pubsub", "status": "passed", "detail": "QoS0 PUBLISH forwarded"})

            quick = self._mqtt_client(port, "quick-pub")
            quick.sendall(_pkt_publish("a/quick", b"before-eof") + b"\xe0\x00")
            quick.close()
            packet_type, body = _read_mqtt_packet(subscriber)
            if packet_type != 3 or b"before-eof" not in body:
                raise RuntimeError("MQTT broker discarded buffered PUBLISH before peer EOF")
            scenarios.append({"name": "mqtt_buffer_before_eof", "status": "passed", "detail": "buffered packet processed before close"})

            malformed = socket.create_connection(("127.0.0.1", port), timeout=2)
            malformed.sendall(b"\xff\xff\xff\xff\xff")
            malformed.close()
            time.sleep(0.1)
            survivor = self._mqtt_client(port, "after-malformed")
            survivor.close()
            if process.poll() is not None:
                raise RuntimeError("MQTT broker exited after malformed packet")
            scenarios.append({"name": "mqtt_malformed_survival", "status": "passed", "detail": "broker accepted a later CONNECT"})

            if shutil.which("mosquitto_sub") and shutil.which("mosquitto_pub"):
                sub = subprocess.Popen(
                    ["mosquitto_sub", "-h", "127.0.0.1", "-p", str(port), "-t", "interop/topic", "-q", "0", "-C", "1", "-W", "3"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    time.sleep(0.4)
                    pub = subprocess.run(
                        ["mosquitto_pub", "-h", "127.0.0.1", "-p", str(port), "-t", "interop/topic", "-q", "0", "-m", "interop-ok"],
                        text=True,
                        capture_output=True,
                        timeout=4,
                        check=False,
                    )
                    output, error = sub.communicate(timeout=5)
                finally:
                    if sub.poll() is None:
                        sub.terminate()
                        sub.wait(timeout=2)
                if pub.returncode or sub.returncode or "interop-ok" not in output:
                    raise RuntimeError(f"mosquitto interoperability failed: {error or pub.stderr}".strip())
                scenarios.append({"name": "mqtt_mosquitto_interop", "status": "passed", "detail": "mosquitto_sub received mosquitto_pub payload"})
            else:
                scenarios.append({"name": "mqtt_mosquitto_interop", "status": "skipped", "detail": "mosquitto clients unavailable"})
        finally:
            for sock in sockets:
                sock.close()
            self._stop(process)

    def _coap_exchange(self, sock: socket.socket, port: int, request: bytes) -> dict[str, object]:
        sock.sendto(request, ("127.0.0.1", port))
        data, _ = sock.recvfrom(2048)
        return _decode_coap(data)

    def _coap_smoke(self, scenarios: list[dict[str, str]]) -> None:
        port = _free_port(socket.SOCK_DGRAM)
        process = self._start(port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2)
        try:
            time.sleep(0.15)
            hello = self._coap_exchange(sock, port, _coap_request("/hello", 0x1234))
            if hello["type"] != 2 or hello["code"] != 69 or hello["message_id"] != 0x1234 or hello["token"] != b"\xaa":
                raise RuntimeError("CoAP GET /hello response envelope or 2.05 code is invalid")
            if hello["payload"] != b"hello from CoAP server" or (12, b"") not in hello["options"]:
                raise RuntimeError("CoAP GET /hello did not return the fixed text/plain payload")
            scenarios.append({"name": "coap_get_hello", "status": "passed", "detail": "received valid 2.05 Content"})

            unknown = self._coap_exchange(sock, port, _coap_request("/unknown", 0x1235))
            if unknown["code"] != 132:
                raise RuntimeError("CoAP GET /unknown did not return 4.04 Not Found")
            scenarios.append({"name": "coap_unknown_not_found", "status": "passed", "detail": "received 4.04 Not Found"})

            extended = self._coap_exchange(sock, port, bytes.fromhex("40010001d10078"))
            if extended["type"] == 3 or extended["code"] != 132:
                raise RuntimeError("CoAP valid extended option was rejected as malformed")
            scenarios.append({"name": "coap_extended_option", "status": "passed", "detail": "valid extended option decoded"})

            if shutil.which("coap-client-notls"):
                result = subprocess.run(
                    ["coap-client-notls", "-m", "get", f"coap://127.0.0.1:{port}/hello"],
                    text=True,
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                if result.returncode or result.stdout.strip() != "hello from CoAP server":
                    raise RuntimeError(f"coap-client-notls interoperability failed: {result.stderr or result.stdout}".strip())
                scenarios.append({"name": "coap_client_interop", "status": "passed", "detail": "coap-client-notls received /hello payload"})
            else:
                scenarios.append({"name": "coap_client_interop", "status": "skipped", "detail": "coap-client-notls unavailable"})
        finally:
            sock.close()
            self._stop(process)
