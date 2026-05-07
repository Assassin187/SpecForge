from __future__ import annotations

import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
import re

from .models import Diagnostic, SpecBundle
from .specs import canonical_signature_for_header, normalize_repo_path


@dataclass
class VerificationResult:
    ok: bool
    diagnostics: list[Diagnostic]
    compile_stdout: str = ""
    compile_stderr: str = ""


def _bundle_slug(bundle: SpecBundle) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", bundle.protocol.name.lower()).strip("_")
    return slug or "protocol"


def _bundle_binary_name(bundle: SpecBundle) -> str:
    roles = [role.lower() for role in bundle.protocol.roles]
    suffix = "broker" if "broker" in roles else "server" if "server" in roles else "app"
    return f"{_bundle_slug(bundle)}_{suffix}"


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _enc_rl(length: int) -> bytes:
    out = bytearray()
    remaining = length
    while True:
        digit = remaining % 128
        remaining //= 128
        if remaining > 0:
            digit |= 0x80
        out.append(digit)
        if remaining == 0:
            return bytes(out)


def _enc_str(value: str) -> bytes:
    raw = value.encode("utf-8")
    return len(raw).to_bytes(2, "big") + raw


def _pkt_connect(client_id: str, keepalive: int = 10, clean_session: bool = True) -> bytes:
    vh = _enc_str("MQTT") + bytes([4])
    flags = 0x02 if clean_session else 0x00
    vh += bytes([flags]) + keepalive.to_bytes(2, "big")
    payload = _enc_str(client_id)
    rem = vh + payload
    return bytes([0x10]) + _enc_rl(len(rem)) + rem


def _pkt_subscribe(packet_id: int, topic_filter: str, qos: int = 0) -> bytes:
    body = packet_id.to_bytes(2, "big") + _enc_str(topic_filter) + bytes([qos])
    return bytes([0x82]) + _enc_rl(len(body)) + body


def _pkt_publish(topic: str, payload: bytes, retain: bool = False) -> bytes:
    header = 0x31 if retain else 0x30
    body = _enc_str(topic) + payload
    return bytes([header]) + _enc_rl(len(body)) + body


def _pkt_pingreq() -> bytes:
    return b"\xc0\x00"


def _pkt_disconnect() -> bytes:
    return b"\xe0\x00"


def _read_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        data = sock.recv(size - len(chunks))
        if not data:
            raise ConnectionError("socket closed while reading packet")
        chunks.extend(data)
    return bytes(chunks)


def _read_packet(sock: socket.socket) -> tuple[int, bytes]:
    first = _read_exact(sock, 1)[0]
    multiplier = 1
    remaining = 0
    while True:
        digit = _read_exact(sock, 1)[0]
        remaining += (digit & 0x7F) * multiplier
        if digit & 0x80 == 0:
            break
        multiplier *= 128
    body = _read_exact(sock, remaining) if remaining else b""
    return first >> 4, body


def _wait_for_port(port: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.3)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise TimeoutError(f"broker did not start listening on port {port}")


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


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
            for file_path in module.files:
                expected_paths.add(normalize_repo_path(file_path))
        for relative_path in sorted(expected_paths):
            if not (self.project_dir / relative_path).exists():
                diagnostics.append(Diagnostic("error", "missing_generated_file", f"Missing generated file '{relative_path}'", relative_path))

        for file_spec in self.bundle.file_specs_by_trace.values():
            header_path = self.project_dir / file_spec.header_path
            if not header_path.exists():
                continue
            content = _normalize_text(header_path.read_text(encoding="utf-8"))
            for interface in file_spec.header_interfaces:
                canonical = _normalize_text(canonical_signature_for_header(self.bundle, file_spec, interface))
                if canonical not in content:
                    diagnostics.append(Diagnostic("error", "missing_signature", f"Header '{file_spec.header_path}' does not contain '{interface.name}' with canonical signature", file_spec.header_path))
        return diagnostics

    def compile_broker(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["make", _bundle_binary_name(self.bundle)],
            cwd=self.project_dir,
            text=True,
            capture_output=True,
            check=False,
        )

    def verify(self) -> VerificationResult:
        diagnostics = self.verify_structure()
        compile_result = self.compile_broker()
        if compile_result.returncode != 0:
            diagnostics.append(Diagnostic("error", "compile_failed", "Generated broker failed to compile", "Makefile"))
            return VerificationResult(False, diagnostics, compile_result.stdout, compile_result.stderr)
        try:
            self.run_smoke_test()
        except Exception as exc:  # noqa: BLE001
            diagnostics.append(Diagnostic("error", "smoke_test_failed", str(exc), "mqtt_broker"))
            return VerificationResult(False, diagnostics, compile_result.stdout, compile_result.stderr)
        return VerificationResult(not any(d.level == "error" for d in diagnostics), diagnostics, compile_result.stdout, compile_result.stderr)

    def run_smoke_test(self) -> None:
        if _bundle_slug(self.bundle) != "mqtt":
            return
        port = _free_tcp_port()
        binary_name = _bundle_binary_name(self.bundle)
        broker = subprocess.Popen(
            [f"./{binary_name}", str(port)],
            cwd=self.project_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            _wait_for_port(port)

            sub_hash = socket.create_connection(("127.0.0.1", port), timeout=2.0)
            sub_hash.settimeout(2.0)
            sub_hash.sendall(_pkt_connect("sub-hash"))
            pkt_type, _ = _read_packet(sub_hash)
            if pkt_type != 2:
                raise RuntimeError("expected CONNACK for # subscriber")
            sub_hash.sendall(_pkt_subscribe(1, "a/#"))
            pkt_type, _ = _read_packet(sub_hash)
            if pkt_type != 9:
                raise RuntimeError("expected SUBACK for # subscriber")

            sub_plus = socket.create_connection(("127.0.0.1", port), timeout=2.0)
            sub_plus.settimeout(2.0)
            sub_plus.sendall(_pkt_connect("sub-plus"))
            pkt_type, _ = _read_packet(sub_plus)
            if pkt_type != 2:
                raise RuntimeError("expected CONNACK for + subscriber")
            sub_plus.sendall(_pkt_subscribe(2, "a/+"))
            pkt_type, _ = _read_packet(sub_plus)
            if pkt_type != 9:
                raise RuntimeError("expected SUBACK for + subscriber")

            pub = socket.create_connection(("127.0.0.1", port), timeout=2.0)
            pub.settimeout(2.0)
            pub.sendall(_pkt_connect("publisher"))
            pkt_type, _ = _read_packet(pub)
            if pkt_type != 2:
                raise RuntimeError("expected CONNACK for publisher")

            pub.sendall(_pkt_publish("a/b", b"hello"))
            pkt_type, body = _read_packet(sub_hash)
            if pkt_type != 3 or b"hello" not in body:
                raise RuntimeError("expected wildcard # subscriber to receive a/b publish")
            pkt_type, body = _read_packet(sub_plus)
            if pkt_type != 3 or b"hello" not in body:
                raise RuntimeError("expected wildcard + subscriber to receive a/b publish")

            pub.sendall(_pkt_publish("a/b/c", b"deep"))
            pkt_type, body = _read_packet(sub_hash)
            if pkt_type != 3 or b"deep" not in body:
                raise RuntimeError("expected wildcard # subscriber to receive a/b/c publish")
            sub_plus.settimeout(0.7)
            try:
                _read_packet(sub_plus)
                raise RuntimeError("subscriber with a/+ unexpectedly received a/b/c publish")
            except socket.timeout:
                pass
            finally:
                sub_plus.settimeout(2.0)

            pub.sendall(_pkt_pingreq())
            pkt_type, _ = _read_packet(pub)
            if pkt_type != 13:
                raise RuntimeError("expected PINGRESP")

            pub.sendall(_pkt_disconnect())
            pub.settimeout(1.0)
            eof = pub.recv(1)
            if eof != b"":
                raise RuntimeError("expected broker to close publisher socket after DISCONNECT")

            for sock in (sub_hash, sub_plus, pub):
                try:
                    sock.close()
                except OSError:
                    pass
        finally:
            broker.terminate()
            try:
                broker.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                broker.kill()
                broker.wait(timeout=2.0)
