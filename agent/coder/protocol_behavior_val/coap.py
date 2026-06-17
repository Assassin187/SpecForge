from __future__ import annotations

import shutil
import socket
import subprocess
import time
from pathlib import Path

from .common import free_port, start_server, stop_process

EXPECTED_SCENARIOS = (
    "coap_get_hello",
    "coap_unknown_not_found",
    "coap_malformed_survival",
    "coap_extended_option",
    "coap_smoke_test",
    "coap_client_interop",
)


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


def _coap_exchange(sock: socket.socket, port: int, request: bytes) -> dict[str, object]:
    sock.sendto(request, ("127.0.0.1", port))
    data, _ = sock.recvfrom(2048)
    return _decode_coap(data)


# ---------------------------------------------------------------------------
# run entry-point
# ---------------------------------------------------------------------------


def run(project_dir: Path, binary_name: str, scenarios: list[dict[str, str]]) -> None:
    port = free_port(socket.SOCK_DGRAM)
    process = start_server(project_dir, binary_name, port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2)
    try:
        time.sleep(0.15)

        # --------------------------------------------------- 1. GET /hello
        try:
            # Verify codec/parser, codec/serializer, handler, and UDP transport
            # all work together: request dispatched to /hello resource, 2.05 response
            # with correct token, message ID, payload and Content-Format option.
            hello = _coap_exchange(sock, port, _coap_request("/hello", 0x1234))
            if hello["type"] != 2 or hello["code"] != 69 or hello["message_id"] != 0x1234 or hello["token"] != b"\xaa":
                raise RuntimeError(
                    "CoAP GET /hello: invalid response envelope — "
                    f"type={hello['type']} code={hello['code']} "
                    f"mid={hello['message_id']} token={hello['token']!r}"
                )
            if hello["payload"] != b"hello from CoAP server" or (12, b"") not in hello["options"]:
                raise RuntimeError(
                    f"CoAP GET /hello: payload or Content-Format mismatch — "
                    f"payload={hello['payload']!r}"
                )
            scenarios.append({
                "name": "coap_get_hello",
                "status": "passed",
                "detail": "received valid 2.05 Content with correct payload and Content-Format option",
            })
        except Exception as _e:
            scenarios.append({
                "name": "coap_get_hello",
                "status": "failed",
                "detail": str(_e),
            })

        # --------------------------------------------------- 2. unknown path
        try:
            # Verify error handling: unknown resource path returns 4.04 Not Found.
            unknown = _coap_exchange(sock, port, _coap_request("/unknown", 0x1235))
            if unknown["code"] != 132:
                raise RuntimeError(
                    f"CoAP GET /unknown: expected 4.04 (132), got code={unknown['code']}"
                )
            scenarios.append({
                "name": "coap_unknown_not_found",
                "status": "passed",
                "detail": "received 4.04 Not Found for unknown path",
            })
        except Exception as _e:
            scenarios.append({
                "name": "coap_unknown_not_found",
                "status": "failed",
                "detail": str(_e),
            })

        # --------------------------------------------------- 3. malformed survival
        try:
            # Verify error handling: sending garbage UDP datagram does not crash
            # the server, and it continues to serve valid requests afterwards.
            sock.sendto(b"\xff\xfe\xfd\xfc\xfb\xfa", ("127.0.0.1", port))
            time.sleep(0.05)
            # Drain stale RST datagram the server correctly sends per RFC 7252.
            # Without draining, recvfrom() in _coap_exchange() reads the stale RST
            # instead of the response to the subsequent valid GET /hello.
            try:
                while True:
                    sock.settimeout(0.01)
                    sock.recvfrom(2048)
            except socket.timeout:
                pass
            sock.settimeout(2)
            survivor = _coap_exchange(sock, port, _coap_request("/hello", 0x1236))
            if survivor["code"] != 69:
                raise RuntimeError(
                    f"CoAP malformed survival: server responded with code={survivor['code']} "
                    f"instead of 2.05 after malformed datagram"
                )
            if process.poll() is not None:
                raise RuntimeError("CoAP server exited after malformed packet")
            scenarios.append({
                "name": "coap_malformed_survival",
                "status": "passed",
                "detail": "server survived malformed UDP datagram and continued serving /hello",
            })
        except Exception as _e:
            scenarios.append({
                "name": "coap_malformed_survival",
                "status": "failed",
                "detail": str(_e),
            })

        # --------------------------------------------------- 4. extended option
        try:
            # Verify codec/parser handles CoAP option extensions (option delta > 12)
            # without misclassifying the packet as malformed.
            extended = _coap_exchange(sock, port, bytes.fromhex("40010001d10078"))
            if extended["type"] == 3 or extended["code"] != 132:
                raise RuntimeError(
                    "CoAP extended option: valid request with extended option delta "
                    f"was rejected — type={extended['type']} code={extended['code']}"
                )
            scenarios.append({
                "name": "coap_extended_option",
                "status": "passed",
                "detail": "valid extended option delta decoded, request processed normally",
            })
        except Exception as _e:
            scenarios.append({
                "name": "coap_extended_option",
                "status": "failed",
                "detail": str(_e),
            })

        # --------------------------------------------------- 5. smoke test
        try:
            # Verify the server main loop handles multiple sequential requests
            # without crashing or returning incorrect results.
            for i in range(5):
                smoke = _coap_exchange(sock, port, _coap_request("/hello", 0x2000 + i))
                if smoke["code"] != 69 or smoke["payload"] != b"hello from CoAP server":
                    raise RuntimeError(
                        f"CoAP smoke test iteration {i}: "
                        f"expected 2.05 'hello from CoAP server', "
                        f"got code={smoke['code']} payload={smoke['payload']!r}"
                    )
            scenarios.append({
                "name": "coap_smoke_test",
                "status": "passed",
                "detail": "main loop handled 5 sequential GET /hello requests without failure",
            })
        except Exception as _e:
            scenarios.append({
                "name": "coap_smoke_test",
                "status": "failed",
                "detail": str(_e),
            })

        # --------------------------------------------------- 6. client interop
        try:
            # Verify interoperability with a third-party CoAP client (coap-client-notls
            # from libcoap). Skipped if the tool is not installed.
            if shutil.which("coap-client-notls"):
                result = subprocess.run(
                    ["coap-client-notls", "-m", "get", f"coap://127.0.0.1:{port}/hello"],
                    text=True,
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                if result.returncode or result.stdout.strip() != "hello from CoAP server":
                    raise RuntimeError(
                        "coap-client-notls interoperability failed: "
                        f"{result.stderr or result.stdout}".strip()
                    )
                scenarios.append({
                    "name": "coap_client_interop",
                    "status": "passed",
                    "detail": "coap-client-notls received /hello payload",
                })
        except Exception as _e:
            scenarios.append({
                "name": "coap_client_interop",
                "status": "failed",
                "detail": str(_e),
            })
        else:
            scenarios.append({
                "name": "coap_client_interop",
                "status": "skipped",
                "detail": "coap-client-notls unavailable",
            })

    finally:
        sock.close()
        stop_process(process)
