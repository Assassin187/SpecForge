"""Independent, exact-wire SMTP subset acceptance (standard library only).

Covers cases/smtp_min/REQUIREMENTS.md R02-R12: greeting, EHLO/HELO session
state, envelope rules, DATA transparency, capture-file contract, TCP
fragmentation/coalescing, auxiliary commands, reply codes, size limits,
EOF handling and storage failure. Each scenario starts and stops its own
smtp_server process with a private mail directory. Never exposed to
generation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import smtplib
import socket
import subprocess
import time
from pathlib import Path

TEST_IDS = (
    "smtp_greeting_helo_ehlo", "smtp_command_case_and_ehlo_reset",
    "smtp_envelope_recipient_rules", "smtp_data_capture_record",
    "smtp_dot_transparency_and_empty_data", "smtp_fragmented_and_coalesced_input",
    "smtp_rset_noop_vrfy_quit", "smtp_transaction_reuse_and_isolation",
    "smtp_reply_code_matrix", "smtp_size_limits",
    "smtp_eof_and_aborted_data", "smtp_storage_failure", "smtp_smtplib_interop",
)


class EnvironmentBlocked(RuntimeError):
    pass


class Client:
    """One raw SMTP connection with buffered CRLF line reading."""

    def __init__(self, case: "Case", name: str, greeting: bool = True):
        self.case, self.name = case, name
        self.sock = socket.create_connection(("127.0.0.1", case.port), timeout=2)
        self.sock.settimeout(2)
        self.buffer = b""
        case.clients.append(self)
        if greeting:
            code, text = self.reply()
            assert code == 220, f"Greeting reply code {code}"
            assert b"smtp.example.test" in text, f"Greeting lacks server identity: {text!r}"

    def send(self, data: bytes):
        self.case.events.append({"client": self.name, "send": data.decode("utf-8", "backslashreplace")})
        self.sock.sendall(data)

    def _line(self) -> bytes:
        while True:
            mark = self.buffer.find(b"\r\n")
            if mark >= 0:
                line, self.buffer = self.buffer[:mark + 2], self.buffer[mark + 2:]
                return line
            chunk = self.sock.recv(4096)
            if not chunk:
                raise AssertionError(f"EOF while waiting for reply (buffered {self.buffer[:60]!r})")
            self.buffer += chunk

    def reply(self) -> tuple[int, bytes]:
        lines = [self._line()]
        first = lines[0]
        assert len(first) >= 5 and first[:3].isdigit() and first[3:4] in (b" ", b"-"), \
            f"Malformed reply line {first!r}"
        while lines[-1][3:4] == b"-":
            lines.append(self._line())
            assert lines[-1][:3] == first[:3], f"Multiline reply code changed: {lines!r}"
        text = b"".join(lines)
        assert text.endswith(b"\r\n"), f"Reply not CRLF terminated: {text!r}"
        self.case.events.append({"client": self.name, "reply": text.decode("utf-8", "backslashreplace")})
        return int(first[:3]), text

    def expect(self, code: int) -> bytes:
        actual, text = self.reply()
        assert actual == code, f"Expected {code}, got {actual}: {text!r}"
        return text

    def command(self, line: bytes, code: int) -> bytes:
        self.send(line + b"\r\n")
        return self.expect(code)

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
        self.maildir = directory / "mail"
        self.clients, self.events = [], []
        self.process = None
        self.log = None
        self.directory.mkdir(parents=True, exist_ok=True)
        self.maildir.mkdir(exist_ok=True)

    def __enter__(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.log = (self.directory / "server.log").open("w")
        environment = {**os.environ, "ASAN_OPTIONS": "detect_leaks=1:halt_on_error=1",
                       "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1"}
        self.process = subprocess.Popen([str(self.binary), str(self.port), str(self.maildir)],
                                        stdout=self.log, stderr=subprocess.STDOUT, env=environment)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.__exit__(None, None, None)
                raise AssertionError("Server exited during startup; see server.log")
            try:
                probe = socket.create_connection(("127.0.0.1", self.port), timeout=0.2)
                probe.settimeout(0.4)
                greeting = probe.makefile("rb").readline()
                probe.close()
                if greeting.startswith(b"220"):
                    return self
            except OSError:
                time.sleep(0.02)
        self.__exit__(None, None, None)
        raise AssertionError("Server did not greet within startup deadline")

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

    def client(self, name: str, greeting: bool = True) -> Client:
        return Client(self, name, greeting)

    def session(self, name: str) -> Client:
        client = self.client(name)
        client.command(b"EHLO session.example", 250)
        return client

    def transaction(self, client: Client, sender: bytes = b"sender@example.org",
                    rcpt: bytes = b"alice@example.test"):
        client.command(b"MAIL FROM:<" + sender + b">", 250)
        client.command(b"RCPT TO:<" + rcpt + b">", 250)

    def capture_files(self) -> list[Path]:
        return sorted(self.maildir.iterdir(), key=lambda p: (p.stat().st_mtime_ns, p.name))

    def captures(self) -> list[dict]:
        records = []
        for path in self.capture_files():
            records.append(json.loads(path.read_bytes().decode("utf-8")))  # strict UTF-8 JSON
        return records


def greeting_helo_ehlo(c: Case):
    cli = c.client("main")  # constructor already asserted 220 + identity
    cli.command(b"EHLO mail.example.org", 250)
    cli.command(b"HELO [192.0.2.1]", 250)          # IPv4 address literal
    cli.command(b"EHLO", 501)                      # missing argument
    cli.command(b"EHLO second.example.org", 250)   # repeated EHLO still fine


def command_case_and_ehlo_reset(c: Case):
    cli = c.client("case")
    cli.command(b"eHlO Mixed.Example.ORG", 250)    # case-insensitive command
    cli.command(b"mAiL fRoM:<up@example.org>", 250)
    cli.command(b"RcPt tO:<alice@example.test>", 250)
    cli.command(b"HELO restart.example", 250)      # repeated HELO resets transaction
    cli.command(b"DATA", 503)                      # envelope was discarded
    cli.command(b"MAIL FROM:<up@example.org>", 250)  # session stays initialized
    cli.command(b"RCPT TO:<alice@example.test>", 250)
    cli.command(b"DATA", 354)
    cli.send(b"body\r\n.\r\n")
    cli.expect(250)
    caps = c.captures()
    assert len(caps) == 1 and caps[0]["data"] == "body\r\n", caps


def envelope_recipient_rules(c: Case):
    cli = c.client("env")
    cli.command(b"MAIL FROM:<s@example.org>", 503)     # before initialization
    cli.command(b"RCPT TO:<alice@example.test>", 503)
    cli.command(b"DATA", 503)
    cli.command(b"EHLO x.example", 250)
    cli.command(b"RCPT TO:<alice@example.test>", 503)  # before MAIL
    cli.command(b"DATA", 503)
    cli.command(b"MAIL FROM:<s@example.org>", 250)
    cli.command(b"MAIL FROM:<other@example.org>", 503)  # nested MAIL rejected
    cli.command(b"RCPT TO:<alice@example.test>", 250)
    cli.command(b"RCPT TO:<charlie@example.test>", 550)  # unknown local mailbox
    cli.command(b"RCPT TO:<bob@EXAMPLE.TEST>", 250)      # domain case-insensitive
    cli.command(b"RCPT TO:<ALICE@example.test>", 550)    # local-part case-sensitive
    cli.command(b"RCPT TO:<Postmaster>", 250)            # reserved unqualified form
    cli.command(b"RCPT TO:<POSTMASTER@example.test>", 250)  # postmaster case-insensitive
    cli.command(b"RCPT TO:<alice@example.test>", 250)    # duplicate retained
    cli.command(b"DATA", 354)                            # nested MAIL did not replace envelope
    cli.send(b"Envelope test\r\n.\r\n")
    cli.expect(250)
    (cap,) = c.captures()
    assert cap["mail_from"] == "s@example.org", cap
    assert cap["rcpt_to"] == ["alice@example.test", "bob@EXAMPLE.TEST", "Postmaster",
                              "POSTMASTER@example.test", "alice@example.test"], cap["rcpt_to"]
    assert cap["data"] == "Envelope test\r\n", cap


def data_capture_record(c: Case):
    cli = c.session("data")
    c.transaction(cli)
    cli.command(b"DATA", 354)
    body = b"From: s@example.org\r\nTo: alice@example.test\r\n\r\nLine one\r\n\r\nLine two!\r\n"
    cli.send(body + b".\r\n")
    cli.expect(250)  # final 250 implies the capture is already written
    (cap,) = c.captures()
    assert cap["mail_from"] == "sender@example.org", cap
    assert cap["rcpt_to"] == ["alice@example.test"], cap
    assert cap["data"] == body.decode("ascii"), repr(cap["data"])


def dot_transparency_and_empty_data(c: Case):
    cli = c.session("dots")
    c.transaction(cli)
    cli.command(b"DATA", 354)
    # leading-dot unstuffing; command-looking lines stay content
    cli.send(b"..leading\r\n...triple\r\nQUIT\r\nRSET\r\n\r\n.\r\n")
    cli.expect(250)
    # empty DATA block
    c.transaction(cli, rcpt=b"bob@example.test")
    cli.command(b"DATA", 354)
    cli.send(b".\r\n")
    cli.expect(250)
    # null reverse-path maps to empty mail_from
    cli.command(b"MAIL FROM:<>", 250)
    cli.command(b"RCPT TO:<alice@example.test>", 250)
    cli.command(b"DATA", 354)
    cli.send(b"bounced\r\n.\r\n")
    cli.expect(250)
    first, second, third = c.captures()
    assert first["data"] == ".leading\r\n..triple\r\nQUIT\r\nRSET\r\n\r\n", repr(first["data"])
    assert second["data"] == "", repr(second["data"])
    assert second["rcpt_to"] == ["bob@example.test"], second
    assert third["mail_from"] == "", third
    assert third["data"] == "bounced\r\n", third


def fragmented_and_coalesced_input(c: Case):
    cli = c.client("frag")
    for byte in b"EHLO frag.example\r\n":       # byte-by-byte command
        cli.send(bytes([byte]))
    cli.expect(250)
    cli.send(b"MAIL FROM:<frag@example.org>\r")  # split CRLF
    cli.quiet()
    cli.send(b"\n")
    cli.expect(250)
    # coalesced RCPT RCPT DATA: replies in order
    cli.send(b"RCPT TO:<alice@example.test>\r\nRCPT TO:<bob@example.test>\r\nDATA\r\n")
    cli.expect(250)
    cli.expect(250)
    cli.expect(354)
    # terminator split across reads, next command coalesced after terminator
    cli.send(b"split-body\r")
    cli.quiet()
    cli.send(b"\n.")
    cli.quiet()
    cli.send(b"\r\nQUIT\r\n")
    cli.expect(250)  # DATA completed
    cli.expect(221)  # coalesced QUIT processed afterwards
    (cap,) = c.captures()
    assert cap["data"] == "split-body\r\n", cap
    assert cap["rcpt_to"] == ["alice@example.test", "bob@example.test"], cap


def rset_noop_vrfy_quit(c: Case):
    cli = c.session("misc")
    c.transaction(cli)
    cli.command(b"NOOP", 250)                       # keeps transaction
    cli.command(b"VRFY alice@example.test", 252)    # no verification claim
    cli.command(b"VRFY", 501)
    cli.command(b"RSET", 250)                       # discards envelope
    cli.command(b"DATA", 503)
    c.transaction(cli)                              # still initialized after RSET
    cli.command(b"QUIT", 221)                       # unfinished transaction discarded
    cli.closed()
    assert c.captures() == [], c.captures()


def transaction_reuse_and_isolation(c: Case):
    cli = c.session("reuse")
    c.transaction(cli)
    cli.command(b"DATA", 354)
    cli.send(b"first\r\n.\r\n")
    cli.expect(250)
    c.transaction(cli, sender=b"second@example.org", rcpt=b"bob@example.test")
    cli.command(b"DATA", 354)
    cli.send(b"second\r\n.\r\n")
    cli.expect(250)
    fresh = c.client("fresh")                       # new connection inherits nothing
    fresh.command(b"DATA", 503)
    fresh.command(b"MAIL FROM:<x@y.example>", 503)
    fresh.command(b"EHLO fresh.example", 250)
    c.transaction(fresh)
    fresh.command(b"DATA", 354)
    fresh.send(b"third\r\n.\r\n")
    fresh.expect(250)
    caps = c.captures()
    assert [cap["data"] for cap in caps] == ["first\r\n", "second\r\n", "third\r\n"], caps
    names = [p.name for p in c.capture_files()]
    assert len(names) == len(set(names)) == 3, names  # unique filenames


def reply_code_matrix(c: Case):
    cli = c.client("matrix")
    cli.command(b"FOOBAR", 500)                             # unrecognized
    cli.command(b"EXPN members", 502)                       # recognized, unimplemented
    cli.command(b"TURN", 502)
    cli.command(b"EHLO", 501)                               # malformed argument
    cli.command(b"EHLO ok.example", 250)
    cli.command(b"MAIL FROM:<a@b.example> SIZE=99", 555)    # unsupported extension parameter
    cli.command(b"MAIL FROM a@b.example", 501)              # malformed path
    cli.command(b"MAIL FROM:<a@b.example>", 250)
    cli.command(b"RCPT TO:<alice@example.test> NOTIFY=SUCCESS", 555)
    cli.command(b"RCPT TO:", 501)
    cli.command(b"RCPT TO:<alice@example.test>", 250)
    cli.command(b"DATA", 354)
    cli.send(b"matrix\r\n.\r\n")
    cli.expect(250)
    (cap,) = c.captures()
    assert cap["data"] == "matrix\r\n", cap


def size_limits(c: Case):
    cli = c.client("limits")
    cli.send(b"NOOP " + b"x" * 506 + b"\r\n")   # 513 octets incl. CRLF > 512
    cli.expect(500)
    cli.command(b"EHLO limits.example", 250)
    cli.command(b"MAIL FROM:<big@example.org>", 250)
    for _ in range(100):
        cli.command(b"RCPT TO:<alice@example.test>", 250)
    cli.command(b"RCPT TO:<bob@example.test>", 452)      # recipient 101
    cli.command(b"RSET", 250)
    # overlong data line (999 content + CRLF = 1001 > 1000): 552 after terminator
    c.transaction(cli)
    cli.command(b"DATA", 354)
    cli.send(b"y" * 999 + b"\r\n")
    cli.send(b".\r\n")
    cli.expect(552)
    assert c.captures() == []
    # oversized DATA total (> 65536 unstuffed) with legal lines: 552 after terminator
    c.transaction(cli)
    cli.command(b"DATA", 354)
    line = b"z" * 998 + b"\r\n"                          # exactly 1000-octet line, legal
    for _ in range(66):                                  # 66 * 998 = 65868 unstuffed octets
        cli.send(line)
    cli.send(b".\r\n")
    cli.expect(552)
    assert c.captures() == []
    cli.command(b"DATA", 503)                            # transaction was reset
    cli.command(b"NOOP", 250)                            # server still fine


def eof_and_aborted_data(c: Case):
    # complete buffered transaction followed by immediate write-side shutdown
    cli = c.client("eof")
    cli.send(b"EHLO eof.example\r\nMAIL FROM:<eof@example.org>\r\n"
             b"RCPT TO:<alice@example.test>\r\nDATA\r\nfinal\r\n.\r\n")
    cli.sock.shutdown(socket.SHUT_WR)
    for code in (250, 250, 250, 354, 250):
        cli.expect(code)
    # aborted DATA at EOF leaves no capture
    broken = c.client("broken")
    broken.command(b"EHLO broken.example", 250)
    broken.command(b"MAIL FROM:<b@example.org>", 250)
    broken.command(b"RCPT TO:<alice@example.test>", 250)
    broken.command(b"DATA", 354)
    broken.send(b"partial\r\n")
    broken.sock.close()
    time.sleep(0.3)
    caps = c.captures()
    assert len(caps) == 1 and caps[0]["data"] == "final\r\n", caps
    alive = c.session("alive")
    alive.command(b"NOOP", 250)


def storage_failure(c: Case):
    if os.geteuid() == 0:
        raise EnvironmentBlocked("permission-based storage failure is unreliable as root")
    c.maildir.chmod(0o555)
    try:
        cli = c.session("store")
        c.transaction(cli)
        cli.command(b"DATA", 354)
        cli.send(b"nosave\r\n.\r\n")
        cli.expect(451)
        assert c.captures() == []
    finally:
        c.maildir.chmod(0o755)
    cli.command(b"NOOP", 250)  # session survives the failed transaction


def smtplib_interop(c: Case):
    with smtplib.SMTP("127.0.0.1", c.port, timeout=4) as smtp:
        smtp.ehlo("interop.example")
        smtp.sendmail("Interop <interop@example.org>",
                      ["alice@example.test", "bob@example.test"],
                      "From: interop@example.org\r\n\r\ninterop body\r\n.dotted line\r\n")
    caps = c.captures()
    assert len(caps) == 1, caps
    (cap,) = caps
    assert cap["mail_from"] == "interop@example.org", cap
    assert cap["rcpt_to"] == ["alice@example.test", "bob@example.test"], cap
    assert cap["data"].endswith("interop body\r\n.dotted line\r\n"), repr(cap["data"])


SCENARIOS = (greeting_helo_ehlo, command_case_and_ehlo_reset, envelope_recipient_rules,
             data_capture_record, dot_transparency_and_empty_data, fragmented_and_coalesced_input,
             rset_noop_vrfy_quit, transaction_reuse_and_isolation, reply_code_matrix,
             size_limits, eof_and_aborted_data, storage_failure, smtplib_interop)


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
