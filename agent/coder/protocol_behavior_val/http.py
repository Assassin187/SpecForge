from __future__ import annotations

import http.client
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

from .common import free_port, start_server, stop_process, wait_for_tcp

EXPECTED_SCENARIOS = (
    "http_tcp_connect",
    "http_get_file",
    "http_head_no_body",
    "http_404_not_found",
    "http_400_bad_request",
    "http_501_unknown_method",
    "http_response_headers",
    "http_connection_close",
    "http_smoke_test",
)

# ---------------------------------------------------------------------------
# protocol helpers
# ---------------------------------------------------------------------------


def _raw_request(host: str, port: int, data: bytes, timeout: float = 3.0) -> tuple[int, bytes]:
    """Send raw bytes over TCP and read the full HTTP response.

    Because the server sends ``Connection: close`` on every response, we can
    simply read until EOF to get the complete response.

    Returns ``(status_code, full_response_bytes)``.
    """
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    try:
        sock.sendall(data)
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
        response = b"".join(chunks)
        if not response:
            return -1, b""
        # Parse status code from "HTTP/1.1 NNN ..."
        parts = response.split(b" ", 2)
        if len(parts) < 2:
            return -1, response
        try:
            return int(parts[1]), response
        except ValueError:
            return -1, response
    finally:
        sock.close()


def _build_request(method: str, path: str, headers: dict[str, str] | None = None,
                   body: bytes | None = None) -> bytes:
    """Build a minimal but well-formed HTTP/1.1 request."""
    lines = [f"{method} {path} HTTP/1.1\r\n".encode()]
    if headers:
        for name, value in headers.items():
            lines.append(f"{name}: {value}\r\n".encode())
    lines.append(b"\r\n")
    request = b"".join(lines)
    if body:
        request += body
    return request


def _http_get(host: str, port: int, path: str, timeout: float = 3.0) -> tuple[int, bytes, dict[str, str]]:
    """Perform an HTTP GET using Python's http.client.

    Returns ``(status, body_bytes, headers_dict)``.
    """
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, body, headers
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# run entry-point – compatible with verify_protocol_behavior()
# ---------------------------------------------------------------------------


def run(project_dir: Path, binary_name: str, scenarios: list[dict[str, str]]) -> None:
    # Create a temporary document root with test files -----------------------
    tmpdir = tempfile.mkdtemp(prefix="http_val_")
    try:
        hello_path = os.path.join(tmpdir, "hello")
        with open(hello_path, "w") as f:
            f.write("Hello, HTTP/1.1!")

        port = free_port(socket.SOCK_STREAM)
        process = start_server(project_dir, binary_name, port, extra_args=[tmpdir])
        try:
            wait_for_tcp(port)

            # --------------------------------------------------- 1. transport
            try:
                # Verify TCP socket bind / listen / accept / read / write / close.
                # The client can connect to the port and receive a response.
                status, response = _raw_request(
                    "127.0.0.1", port,
                    _build_request("GET", "/hello", {"Host": "127.0.0.1"}),
                )
                if status != 200:
                    raise RuntimeError(
                        f"TCP connect test: expected 200, got {status}"
                    )
                if b"Hello, HTTP/1.1!" not in response:
                    raise RuntimeError(
                        "TCP connect test: response body does not contain expected content"
                    )
                scenarios.append({
                    "name": "http_tcp_connect",
                    "status": "passed",
                    "detail": "TCP socket bind/listen/accept/read/write/close works, received 200",
                })
            except Exception as _e:
                scenarios.append({
                    "name": "http_tcp_connect",
                    "status": "failed",
                    "detail": str(_e),
                })

            # ------------------------------------------------- 2. parser + router
            try:
                # Verify request-line parsing (method, path, version) and routing.
                # GET /hello returns the file content; GET /nonexistent returns 404.
                status, body, headers = _http_get("127.0.0.1", port, "/hello")
                if status != 200:
                    raise RuntimeError(
                        f"GET /hello: expected 200, got {status}"
                    )
                if body != b"Hello, HTTP/1.1!":
                    raise RuntimeError(
                        f"GET /hello body mismatch: {body!r}"
                    )
                scenarios.append({
                    "name": "http_get_file",
                    "status": "passed",
                    "detail": "GET /hello parsed correctly, returned 200 with file content",
                })
            except Exception as _e:
                scenarios.append({
                    "name": "http_get_file",
                    "status": "failed",
                    "detail": str(_e),
                })

            # ------------------------------------------------- 3. method handler (HEAD)
            try:
                # Verify HEAD returns headers but no body.
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                try:
                    conn.request("HEAD", "/hello")
                    resp = conn.getresponse()
                    head_body = resp.read()
                    if resp.status != 200:
                        raise RuntimeError(
                            f"HEAD /hello: expected 200, got {resp.status}"
                        )
                    if head_body:
                        raise RuntimeError(
                            f"HEAD /hello: expected empty body, got {len(head_body)} bytes"
                        )
                    content_length = resp.getheader("Content-Length")
                    if not content_length or content_length == "0":
                        raise RuntimeError(
                            "HEAD /hello: Content-Length should be non-zero"
                        )
                finally:
                    conn.close()
                scenarios.append({
                    "name": "http_head_no_body",
                    "status": "passed",
                    "detail": "HEAD /hello returned 200 with Content-Length but zero-length body",
                })
            except Exception as _e:
                scenarios.append({
                    "name": "http_head_no_body",
                    "status": "failed",
                    "detail": str(_e),
                })

            # ------------------------------------------------- 4. 404 routing
            try:
                status, _, _ = _http_get("127.0.0.1", port, "/nonexistent")
                if status != 404:
                    raise RuntimeError(
                        f"GET /nonexistent: expected 404, got {status}"
                    )
                scenarios.append({
                    "name": "http_404_not_found",
                    "status": "passed",
                    "detail": "GET /nonexistent returned 404 Not Found",
                })
            except Exception as _e:
                scenarios.append({
                    "name": "http_404_not_found",
                    "status": "failed",
                    "detail": str(_e),
                })

            # ---------------------------------------------- 5. 400 malformed
            try:
                status, response = _raw_request("127.0.0.1", port, b"GARBAGE\r\n\r\n")
                if status != 400:
                    raise RuntimeError(
                        f"Malformed request: expected 400, got {status}"
                    )
                scenarios.append({
                    "name": "http_400_bad_request",
                    "status": "passed",
                    "detail": "Malformed request returned 400 Bad Request",
                })
            except Exception as _e:
                scenarios.append({
                    "name": "http_400_bad_request",
                    "status": "failed",
                    "detail": str(_e),
                })

            # --------------------------------------- 6. 501 unsupported method
            try:
                status, _ = _raw_request(
                    "127.0.0.1", port,
                    _build_request("DELETE", "/hello", {"Host": "127.0.0.1"}),
                )
                if status != 501:
                    raise RuntimeError(
                        f"DELETE /hello: expected 501, got {status}"
                    )
                scenarios.append({
                    "name": "http_501_unknown_method",
                    "status": "passed",
                    "detail": "Unsupported method returned 501 Not Implemented",
                })
            except Exception as _e:
                scenarios.append({
                    "name": "http_501_unknown_method",
                    "status": "failed",
                    "detail": str(_e),
                })

            # --------------------------------------- 7. response serializer
            try:
                # Verify status line, Content-Length, Content-Type,
                # Connection: close headers are present.
                _, response = _raw_request(
                    "127.0.0.1", port,
                    _build_request("GET", "/hello", {"Host": "127.0.0.1"}),
                )
                resp_text = response.decode(errors="replace")
                checks = ["Content-Length:", "Content-Type:", "Connection: close"]
                missing = [c for c in checks if c not in resp_text]
                if missing:
                    raise RuntimeError(
                        f"Response missing expected headers: {', '.join(missing)}"
                    )
                scenarios.append({
                    "name": "http_response_headers",
                    "status": "passed",
                    "detail": "Response includes Content-Length, Content-Type, and Connection: close headers",
                })
            except Exception as _e:
                scenarios.append({
                    "name": "http_response_headers",
                    "status": "failed",
                    "detail": str(_e),
                })

            # ------------------------------------------- 8. lifecycle / close
            try:
                # Verify the server closes the connection after sending the response.
                # _raw_request reads until EOF so reaching here without error
                # confirms the server closed cleanly.
                req = _build_request("GET", "/hello", {"Host": "127.0.0.1"})
                status, _ = _raw_request("127.0.0.1", port, req)
                if status != 200:
                    raise RuntimeError(
                        f"Connection close test: expected 200, got {status}"
                    )
                scenarios.append({
                    "name": "http_connection_close",
                    "status": "passed",
                    "detail": "Server closed TCP connection after each response",
                })
            except Exception as _e:
                scenarios.append({
                    "name": "http_connection_close",
                    "status": "failed",
                    "detail": str(_e),
                })

            # ----------------------------------------- 9. integration / smoke
            try:
                curl = shutil.which("curl")
                if not curl:
                    raise RuntimeError("curl executable not found")

                # Verify interoperability with an external HTTP/1.1 client.
                for i in range(5):
                    result = subprocess.run(
                        [
                            curl,
                            "--http1.1",
                            "--fail",
                            "--silent",
                            "--show-error",
                            "--max-time",
                            "3",
                            f"http://127.0.0.1:{port}/hello",
                        ],
                        text=True,
                        capture_output=True,
                        check=False,
                        timeout=5,
                    )
                    if result.returncode != 0:
                        raise RuntimeError(
                            f"curl smoke test iteration {i}: "
                            f"exit {result.returncode}, stderr={result.stderr.strip()!r}"
                        )
                    if result.stdout != "Hello, HTTP/1.1!":
                        raise RuntimeError(
                            f"curl smoke test iteration {i}: "
                            f"expected body 'Hello, HTTP/1.1!', got {result.stdout!r}"
                        )
                scenarios.append({
                    "name": "http_smoke_test",
                    "status": "passed",
                    "detail": "curl completed 5 sequential HTTP/1.1 GET requests successfully",
                })
            except Exception as _e:
                scenarios.append({
                    "name": "http_smoke_test",
                    "status": "failed",
                    "detail": str(_e),
                })

        finally:
            stop_process(process)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
