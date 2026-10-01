"""Independent, exact-wire HTTP/1.1 subset acceptance (standard library only).

Covers cases/http11_min/REQUIREMENTS.md R02-R12: request syntax, Host rules,
resource semantics for /hello /value /echo, Content-Length and chunked
framing, fragmentation and pipelining, persistent connections, method and
path error taxonomy, conservative rejection policy and size limits. Each
scenario starts and stops its own http_server process. Never exposed to
generation.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import signal
import socket
import subprocess
import time
from pathlib import Path

TEST_IDS = (
    "http_get_hello_contract", "http_query_case_and_decoding",
    "http_value_put_get_contract", "http_post_echo_both_framings",
    "http_head_semantics", "http_content_length_edges",
    "http_chunked_decoding_details", "http_fragmentation_and_pipeline",
    "http_persistent_and_connection_close", "http_method_and_path_errors",
    "http_malformed_rejection_policy", "http_size_limits",
    "http_http_client_interop",
)

DATE_RE = re.compile(rb"^[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT$")


class EnvironmentBlocked(RuntimeError):
    pass


class Response:
    def __init__(self, status: int, line: bytes, headers: list, body: bytes):
        self.status, self.line, self.headers, self.body = status, line, headers, body

    def header(self, name: str):
        want = name.lower()
        values = [v for n, v in self.headers if n.lower() == want]
        return values[0] if values else None

    def __repr__(self):
        return f"Response({self.status}, {self.headers}, body={self.body[:40]!r})"


class Client:
    """One raw HTTP/1.1 connection with buffered reads."""

    def __init__(self, case: "Case", name: str):
        self.case, self.name = case, name
        self.sock = socket.create_connection(("127.0.0.1", case.port), timeout=2)
        self.sock.settimeout(2)
        self.buffer = b""
        case.clients.append(self)

    def send(self, data: bytes):
        self.case.events.append({"client": self.name, "send": data.decode("utf-8", "backslashreplace")})
        self.sock.sendall(data)

    def request(self, method: str, target: str, body: bytes = b"", headers=(), close=False):
        lines = [f"{method} {target} HTTP/1.1", f"Host: 127.0.0.1:{self.case.port}"]
        for name, value in headers:
            lines.append(f"{name}: {value}")
        if body or method in ("POST", "PUT"):
            lines.append(f"Content-Length: {len(body)}")
        if close:
            lines.append("Connection: close")
        self.send(("\r\n".join(lines) + "\r\n\r\n").encode() + body)
        return self.response(method)

    def _readn(self, count: int) -> bytes:
        while len(self.buffer) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise AssertionError(f"EOF waiting for {count} bytes (have {len(self.buffer)})")
            self.buffer += chunk
        data, self.buffer = self.buffer[:count], self.buffer[count:]
        return data

    def _line(self) -> bytes:
        while True:
            mark = self.buffer.find(b"\r\n")
            if mark >= 0:
                line, self.buffer = self.buffer[:mark], self.buffer[mark + 2:]
                return line
            chunk = self.sock.recv(65536)
            if not chunk:
                raise AssertionError(f"EOF waiting for line (buffered {self.buffer[:60]!r})")
            self.buffer += chunk

    def response(self, method: str = "GET") -> Response:
        line = self._line()
        match = re.match(rb"^HTTP/1\.1 (\d{3})( .*)?$", line)
        assert match, f"Malformed status line: {line!r}"
        status = int(match.group(1))
        headers = []
        while True:
            field = self._line()
            if field == b"":
                break
            assert field[:1] not in (b" ", b"\t"), f"Unexpected folded field: {field!r}"
            colon = field.find(b":")
            assert colon > 0 and field[colon - 1:colon] not in (b" ", b"\t"), f"Malformed field: {field!r}"
            headers.append((field[:colon].decode("latin-1"),
                            field[colon + 1:].decode("latin-1").strip(" \t")))
        self.case.events.append({"client": self.name, "status": status, "headers": headers})
        if method == "HEAD" or status in (204, 304):
            return Response(status, line, headers, b"")
        length = None
        for name, value in headers:
            if name.lower() == "content-length":
                assert length is None, "Duplicate Content-Length in response"
                assert value.isdigit(), f"Bad response Content-Length {value!r}"
                length = int(value)
        assert not any(n.lower() == "transfer-encoding" for n, _ in headers), \
            "Unexpected chunked response"
        if length is None:
            return Response(status, line, headers, b"")  # R06: content responses carry Content-Length
        body = self._readn(length)
        self.case.events.append({"client": self.name, "body_hex": body[:64].hex(), "body_len": len(body)})
        return Response(status, line, headers, body)

    def quiet(self, timeout: float = 0.15):
        assert not self.buffer, f"Unexpected buffered bytes: {self.buffer[:60]!r}"
        self.sock.settimeout(timeout)
        try:
            data = self.sock.recv(1)
        except socket.timeout:
            return
        finally:
            self.sock.settimeout(2)
        raise AssertionError(f"Expected silence, got {data!r}")

    def closed(self):
        try:
            assert self.sock.recv(1) == b"", "Connection stayed open or sent extra data"
        except ConnectionResetError:
            pass


class Case:
    def __init__(self, binary: Path, directory: Path):
        self.binary, self.directory = binary, directory
        self.clients, self.events = [], []
        self.process = None
        self.log = None
        self.directory.mkdir(parents=True, exist_ok=True)

    def __enter__(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.log = (self.directory / "server.log").open("w")
        environment = {**os.environ, "ASAN_OPTIONS": "detect_leaks=1:halt_on_error=1",
                       "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1"}
        self.process = subprocess.Popen([str(self.binary), str(self.port)],
                                        stdout=self.log, stderr=subprocess.STDOUT, env=environment)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.__exit__(None, None, None)
                raise AssertionError("Server exited during startup; see server.log")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return self
            except OSError:
                time.sleep(0.02)
        self.__exit__(None, None, None)
        raise AssertionError("Server did not listen within startup deadline")

    def __exit__(self, exc_type, exc, tb):
        for client in self.clients:
            client.sock.close()
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
            error = "Sanitizer diagnostics in server.log"
        (self.directory / "events.json").write_text(json.dumps(self.events, indent=2) + "\n")
        if error:
            raise AssertionError(error)

    def client(self, name: str) -> Client:
        return Client(self, name)


def get_hello_contract(c: Case):
    cli = c.client("get")
    r = cli.request("GET", "/hello")
    assert r.status == 200, r
    assert r.header("content-type") == "text/plain", r.headers
    assert r.header("content-length") == "5", r.headers
    assert r.body == b"hello", r.body
    date = r.header("date")
    assert date and DATE_RE.match(date.encode("latin-1")), f"Bad Date field: {date!r}"
    # case-insensitive field names, optional whitespace, unknown field ignored, port in Host
    cli.send(b"GET /hello HTTP/1.1\r\nHOST:  127.0.0.1:" + str(c.port).encode() +
             b" \r\nX-Unknown-Field: whatever\r\n\r\n")
    r = cli.response()
    assert r.status == 200 and r.body == b"hello", r


def query_case_and_decoding(c: Case):
    cli = c.client("q")
    cli.send(b"GET /hello?x=1 HTTP/1.1\r\nHost: h\r\n\r\n")   # query routed to path
    r = cli.response()
    assert r.status == 200 and r.body == b"hello", r
    for target in ("/Hello", "/hello/", "/hello%2F", "/HELLO"):  # exact, case-sensitive, no decoding
        cli.send(f"GET {target} HTTP/1.1\r\nHost: h\r\n\r\n".encode())
        r = cli.response()
        assert r.status == 404, (target, r.status)


def value_put_get_contract(c: Case):
    cli = c.client("v")
    r = cli.request("GET", "/value")           # pre-existing, initially empty
    assert r.status == 200 and r.header("content-type") == "application/octet-stream", r
    assert r.header("content-length") == "0" and r.body == b"", r
    blob = b"\x00bin\xff\x01tail" * 100        # 1000 bytes with NULs
    r = cli.request("PUT", "/value", body=blob)
    assert r.status == 204, r
    assert r.body == b"" and r.header("content-length") is None, r  # 204: no body, no framing
    assert r.header("transfer-encoding") is None, r
    r = cli.request("GET", "/value")
    assert r.status == 200 and r.body == blob and r.header("content-length") == str(len(blob)), r
    r = cli.request("PUT", "/value", body=bytes(range(256)) * 4)   # 1024-byte boundary
    assert r.status == 204, r
    r = cli.request("GET", "/value")
    assert r.body == bytes(range(256)) * 4, r.body[:20]
    r = cli.request("PUT", "/value", body=b"")  # empty PUT clears
    assert r.status == 204, r
    r = cli.request("GET", "/value")
    assert r.header("content-length") == "0" and r.body == b"", r


def post_echo_both_framings(c: Case):
    cli = c.client("e")
    blob = b"hello\x00world"
    r = cli.request("POST", "/echo", body=blob)
    assert r.status == 200 and r.header("content-type") == "application/octet-stream", r
    assert r.body == blob and r.header("content-length") == str(len(blob)), r
    # chunked framing decodes to identical bytes
    cli.send(b"POST /echo HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n"
             b"5\r\nhello\r\n6\r\n\x00world\r\n0\r\n\r\n")
    r = cli.response()
    assert r.status == 200 and r.body == blob, r
    # empty body both ways
    r = cli.request("POST", "/echo", body=b"")
    assert r.status == 200 and r.body == b"", r
    cli.send(b"POST /echo HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n")
    r = cli.response()
    assert r.status == 200 and r.body == b"", r
    # request without Content-Length/TE has no body
    cli.send(b"POST /echo HTTP/1.1\r\nHost: h\r\n\r\n")
    r = cli.response()
    assert r.status == 200 and r.body == b"", r
    # POST never touches /value
    r = cli.request("GET", "/value")
    assert r.body == b"", r


def head_semantics(c: Case):
    cli = c.client("h")
    assert cli.request("PUT", "/value", body=b"12345").status == 204
    r = cli.request("HEAD", "/hello")
    assert r.status == 200 and r.header("content-length") == "5", r
    assert r.header("content-type") == "text/plain", r
    assert r.body == b"", r
    r = cli.request("HEAD", "/value")           # metadata of corresponding GET
    assert r.status == 200 and r.header("content-length") == "5", r
    assert r.header("content-type") == "application/octet-stream" and r.body == b"", r
    r = cli.request("HEAD", "/missing")         # error responses omit body bytes too
    assert r.status == 404 and r.body == b"", r
    r = cli.request("HEAD", "/echo")
    assert r.status == 405 and r.body == b"", r
    allow = {m.strip() for m in (r.header("allow") or "").split(",")}
    assert allow == {"POST"}, r.headers
    r = cli.request("GET", "/hello")            # framing intact after HEAD responses
    assert r.status == 200 and r.body == b"hello", r


def content_length_edges(c: Case):
    cli = c.client("cl")
    cli.send(b"POST /echo HTTP/1.1\r\nHost: h\r\nContent-Length: 10\r\n\r\n12345")
    cli.quiet()                                  # incomplete body: no response yet
    cli.send(b"67890")
    r = cli.response()
    assert r.status == 200 and r.body == b"1234567890", r
    # exactly Content-Length bytes consumed; coalesced next request parsed
    cli.send(b"PUT /value HTTP/1.1\r\nHost: h\r\nContent-Length: 3\r\n\r\nabc"
             b"GET /value HTTP/1.1\r\nHost: h\r\n\r\n")
    r1 = cli.response()
    r2 = cli.response()
    assert r1.status == 204, r1
    assert r2.status == 200 and r2.body == b"abc", r2
    # premature EOF during a declared body: request discarded, /value untouched
    doomed = c.client("doomed")
    doomed.send(b"PUT /value HTTP/1.1\r\nHost: h\r\nContent-Length: 10\r\n\r\nxy")
    doomed.sock.close()
    time.sleep(0.3)
    r = cli.request("GET", "/value")
    assert r.body == b"abc", r


def chunked_decoding_details(c: Case):
    cli = c.client("chunk")

    def post_chunked(body: bytes) -> Response:
        cli.send(b"POST /echo HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n" + body)
        return cli.response()

    # uppercase hex size, chunk extension ignored, binary chunk data (13 == 0xD bytes)
    r = post_chunked(b"A;ext=1\r\n0123456789\r\nD\r\n\x00\xffbinary\x00tail\r\n0\r\n\r\n")
    assert r.status == 200 and r.body == b"0123456789\x00\xffbinary\x00tail", r
    # trailer section ignored, does not change body
    r = post_chunked(b"3\r\nabc\r\n0\r\nX-Trailer: ignored\r\nAnother: yes\r\n\r\n")
    assert r.status == 200 and r.body == b"abc", r
    # bare zero chunk
    r = post_chunked(b"0\r\n\r\n")
    assert r.status == 200 and r.body == b"", r
    # chunk boundary split across TCP reads
    cli.send(b"POST /echo HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhe")
    cli.quiet()
    cli.send(b"llo\r\n0\r\n\r\n")
    r = cli.response()
    assert r.status == 200 and r.body == b"hello", r
    # 1024 decoded bytes through many small chunks: limit applies to content, not framing
    data = b"".join(b"10\r\n" + bytes([65 + i % 26]) * 16 + b"\r\n" for i in range(64))
    r = post_chunked(data + b"0\r\n\r\n")
    assert r.status == 200 and len(r.body) == 1024, r


def fragmentation_and_pipeline(c: Case):
    cli = c.client("frag")
    for byte in b"GET /hello HTTP/1.1\r\nHost: h\r\n\r\n":
        cli.send(bytes([byte]))
    r = cli.response()
    assert r.status == 200 and r.body == b"hello", r
    # three pipelined requests in one write, responses in request order
    cli.send(b"GET /hello HTTP/1.1\r\nHost: h\r\n\r\n"
             b"GET /value HTTP/1.1\r\nHost: h\r\n\r\n"
             b"POST /echo HTTP/1.1\r\nHost: h\r\nContent-Length: 3\r\n\r\nxyz")
    r1, r2, r3 = cli.response(), cli.response(), cli.response()
    assert (r1.status, r1.body) == (200, b"hello"), r1
    assert r2.status == 200 and r2.body == b"", r2
    assert r3.status == 200 and r3.body == b"xyz", r3
    # complete PUT immediately followed by write-side shutdown still applies
    cli.send(b"PUT /value HTTP/1.1\r\nHost: h\r\nContent-Length: 5\r\n\r\nVALUE")
    cli.sock.shutdown(socket.SHUT_WR)
    r = cli.response()
    assert r.status == 204, r
    fresh = c.client("fresh")
    r = fresh.request("GET", "/value")
    assert r.status == 200 and r.body == b"VALUE", r


def persistent_and_connection_close(c: Case):
    cli = c.client("keep")
    for _ in range(3):                            # persistent by default
        r = cli.request("GET", "/hello")
        assert r.status == 200 and r.body == b"hello", r
    cli.send(b"GET /hello HTTP/1.1\r\nHost: h\r\nConnection: keep-alive, Close\r\n\r\n")
    r = cli.response()                            # case-insensitive comma-separated option
    assert r.status == 200 and r.body == b"hello", r
    connection = r.header("connection")
    assert connection and "close" in connection.lower(), r.headers
    cli.closed()
    # requests pipelined after Connection: close are not processed
    cli2 = c.client("c2")
    cli2.send(b"PUT /value HTTP/1.1\r\nHost: h\r\nContent-Length: 3\r\nConnection: close\r\n\r\nbar"
              b"PUT /value HTTP/1.1\r\nHost: h\r\nContent-Length: 3\r\n\r\nEVIL")
    r = cli2.response()
    assert r.status == 204, r
    cli2.closed()
    check = c.client("check")
    r = check.request("GET", "/value")
    assert r.body == b"bar", r                    # second pipelined PUT never applied
    # application errors keep the connection usable
    r = check.request("GET", "/missing")
    assert r.status == 404, r
    r = check.request("GET", "/hello")
    assert r.status == 200 and r.body == b"hello", r


def method_and_path_errors(c: Case):
    cli = c.client("m")
    assert cli.request("PUT", "/value", body=b"keep").status == 204
    r = cli.request("POST", "/hello", body=b"ignored")   # framed body consumed, conn alive
    assert r.status == 405, r
    assert {m.strip() for m in (r.header("allow") or "").split(",")} == {"GET", "HEAD"}, r.headers
    r = cli.request("GET", "/echo")
    assert r.status == 405, r
    assert {m.strip() for m in (r.header("allow") or "").split(",")} == {"POST"}, r.headers
    r = cli.request("POST", "/value", body=b"nope")
    assert r.status == 405, r
    assert {m.strip() for m in (r.header("allow") or "").split(",")} == {"GET", "HEAD", "PUT"}, r.headers
    r = cli.request("PUT", "/echo", body=b"nope")
    assert r.status == 405, r
    r = cli.request("DELETE", "/value")                  # unimplemented method
    assert r.status == 501, r
    r = cli.request("OPTIONS", "/hello")
    assert r.status == 501, r
    r = cli.request("GET", "/unknown")
    assert r.status == 404, r
    r = cli.request("POST", "/unknown", body=b"x")
    assert r.status == 404, r
    r = cli.request("GET", "/value")                     # errors never mutated /value
    assert r.status == 200 and r.body == b"keep", r


def malformed_rejection_policy(c: Case):
    def rejected(raw: bytes, code: int, name: str):
        bad = c.client(name)
        bad.send(raw)
        r = bad.response()
        assert r.status == code, (name, r.status)
        bad.closed()                                     # reject-and-close contract

    rejected(b"GET /hello HTTP/1.1\r\n\r\n", 400, "no-host")
    rejected(b"GET /hello HTTP/1.1\r\nHost: a\r\nHost: b\r\n\r\n", 400, "dup-host")
    rejected(b"GET /hello HTTP/1.1\r\nHost:\r\n\r\n", 400, "empty-host")
    rejected(b"GET /hello HTTP/1.1\r\nHost: a\r\nFolded: x\r\n y\r\n\r\n", 400, "obs-fold")
    rejected(b"GET /hello HTTP/1.1\r\nHost : a\r\n\r\n", 400, "ws-before-colon")
    rejected(b"POST /echo HTTP/1.1\r\nHost: a\r\nContent-Length: abc\r\n\r\n", 400, "bad-cl")
    rejected(b"POST /echo HTTP/1.1\r\nHost: a\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\nx",
             400, "dup-cl")                               # conservative: repeated CL rejected
    rejected(b"POST /echo HTTP/1.1\r\nHost: a\r\nContent-Length: 1, 1\r\n\r\nx", 400, "comma-cl")
    rejected(b"POST /echo HTTP/1.1\r\nHost: a\r\nContent-Length: 1\r\n"
             b"Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n", 400, "cl-plus-te")
    rejected(b"POST /echo HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: gzip\r\n\r\n",
             400, "te-final-not-chunked")
    rejected(b"POST /echo HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: gzip, chunked\r\n\r\n0\r\n\r\n",
             501, "te-unsupported-coding")
    rejected(b"POST /echo HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\n\r\nZZ\r\nx\r\n",
             400, "bad-chunk-size")
    rejected(b"GET /hello HTTP/1.0\r\n\r\n", 505, "http-1-0")
    rejected(b"GET /hello HTTP/2.0\r\nHost: a\r\n\r\n", 505, "http-2-0")
    good = c.client("good")                               # server unaffected
    r = good.request("GET", "/hello")
    assert r.status == 200 and r.body == b"hello", r


def size_limits(c: Case):
    big = c.client("big-cl")
    big.send(b"POST /echo HTTP/1.1\r\nHost: h\r\nContent-Length: 1025\r\n\r\n" + b"x" * 1025)
    r = big.response()
    assert r.status == 413, r
    big.closed()
    over = c.client("big-chunked")                        # limit on decoded content
    over.send(b"POST /echo HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n"
              + b"401\r\n" + b"y" * 1025 + b"\r\n0\r\n\r\n")
    r = over.response()
    assert r.status == 413, r
    over.closed()
    long_target = "/" + "a" * 8192                        # 8193 octets > 8192
    wide = c.client("long-target")
    wide.send(f"GET {long_target} HTTP/1.1\r\nHost: h\r\n\r\n".encode())
    r = wide.response()
    assert r.status == 414, r
    wide.closed()
    fat = c.client("fat-headers")
    fat.send(b"GET /hello HTTP/1.1\r\nHost: h\r\nX-Pad: " + b"z" * 20000 + b"\r\n\r\n")
    r = fat.response()
    assert r.status == 431, r
    fat.closed()
    alive = c.client("alive")
    r = alive.request("GET", "/value")                    # unchanged and alive
    assert r.status == 200 and r.body == b"", r


def http_client_interop(c: Case):
    conn = http.client.HTTPConnection("127.0.0.1", c.port, timeout=4)
    conn.request("GET", "/hello")
    r = conn.getresponse()
    assert r.status == 200 and r.read() == b"hello", r.status
    conn.request("PUT", "/value", body=b"interop\x00data")
    r = conn.getresponse()
    assert r.status == 204 and r.read() == b"", r.status
    conn.request("GET", "/value")
    r = conn.getresponse()
    assert r.status == 200 and r.read() == b"interop\x00data", r.status
    conn.request("POST", "/echo", body=b"echo\x00me")
    r = conn.getresponse()
    assert r.status == 200 and r.read() == b"echo\x00me", r.status
    conn.close()
    c.events.append({"interop": "http.client GET/PUT/GET/POST ok"})


SCENARIOS = (get_hello_contract, query_case_and_decoding, value_put_get_contract,
             post_echo_both_framings, head_semantics, content_length_edges,
             chunked_decoding_details, fragmentation_and_pipeline,
             persistent_and_connection_close, method_and_path_errors,
             malformed_rejection_policy, size_limits, http_client_interop)


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
        print(f"{name}: {record['status']}" + (f" ({record['error']})" if "error" in record else ""), flush=True)
    report = {"passed": all(r["status"] == "passed" for r in results),
              "required_count": len(TEST_IDS), "scenarios": results,
              "elapsed_seconds": round(sum(r["elapsed_seconds"] for r in results), 3)}
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
