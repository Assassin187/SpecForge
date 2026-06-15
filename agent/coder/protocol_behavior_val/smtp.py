from __future__ import annotations

import os
import shutil
import socket
import tempfile
from pathlib import Path

from .common import free_port, start_server, stop_process, wait_for_tcp

EXPECTED_SCENARIOS = (
    "smtp_tcp_connect",
    "smtp_helo_ehlo",
    "smtp_mail_from",
    "smtp_rcpt_to",
    "smtp_data_delivery",
    "smtp_bad_sequence",
    "smtp_unknown_command",
    "smtp_smoke_test",
)

# ---------------------------------------------------------------------------
# protocol helpers
# ---------------------------------------------------------------------------


def _smtp_cmd(sock: socket.socket, cmd: str) -> None:
    """Send an SMTP command terminated with CRLF."""
    sock.sendall((cmd + "\r\n").encode())


def _read_smtp_response(sock: socket.socket, timeout: float = 3.0) -> tuple[int, bytes]:
    """Read a complete SMTP response (possibly multi-line).

    SMTP responses end with a line whose 4th character is a space
    (e.g. ``220 ...``).  Continuation lines use a hyphen instead
    (e.g. ``250-...``).  Returns ``(status_code, full_bytes)``.
    """
    sock.settimeout(timeout)
    buf = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
        if buf.endswith(b"\r\n"):
            lines = buf.decode(errors="replace").rstrip("\r\n").split("\r\n")
            last_line = lines[-1]
            if len(last_line) >= 4 and last_line[3] == " " and last_line[:3].isdigit():
                return int(last_line[:3]), buf
    return -1, buf


def _smtp_exchange(sock: socket.socket, cmd: str) -> tuple[int, bytes]:
    """Send an SMTP command and read the response.  Convenience wrapper."""
    _smtp_cmd(sock, cmd)
    return _read_smtp_response(sock)


def _build_email_body(sender: str, recipient: str, subject: str, body_text: str) -> str:
    """Build a minimal but well-formed email message for the DATA phase."""
    return (
        f"From: {sender}\r\n"
        f"To: {recipient}\r\n"
        f"Subject: {subject}\r\n"
        "\r\n"
        f"{body_text}\r\n"
        ".\r\n"
    )


# ---------------------------------------------------------------------------
# run entry-point – compatible with verify_protocol_behavior()
# ---------------------------------------------------------------------------


def run(project_dir: Path, binary_name: str, scenarios: list[dict[str, str]]) -> None:
    # Create a temporary mail store directory ---------------------------------
    tmpdir = tempfile.mkdtemp(prefix="smtp_val_")
    try:
        port = free_port(socket.SOCK_STREAM)
        process = start_server(project_dir, binary_name, port, extra_args=[tmpdir])
        try:
            wait_for_tcp(port)

            # ------------------------------------------------- 1. transport
            # Verify TCP socket bind / listen / accept works: the client can
            # connect and receive the SMTP service greeting (220).
            sock = socket.create_connection(("127.0.0.1", port), timeout=3)
            sock.settimeout(3)
            try:
                code, response = _read_smtp_response(sock)
                if code != 220:
                    raise RuntimeError(
                        f"TCP connect test: expected 220 greeting, got {code}"
                    )
            finally:
                sock.close()
            scenarios.append({
                "name": "smtp_tcp_connect",
                "status": "passed",
                "detail": "TCP connect succeeded, received 220 service greeting",
            })

            # ------------------------------------------------- 2. HELO/EHLO
            # Verify codec/parser recognises HELO and the server progresses
            # the session state to 'greeted'.
            sock = socket.create_connection(("127.0.0.1", port), timeout=3)
            sock.settimeout(3)
            try:
                _read_smtp_response(sock)  # consume 220 greeting
                code, response = _smtp_exchange(sock, "HELO test.local")
                if code != 250:
                    raise RuntimeError(
                        f"HELO test: expected 250, got {code}"
                    )
            finally:
                sock.close()
            scenarios.append({
                "name": "smtp_helo_ehlo",
                "status": "passed",
                "detail": "HELO command parsed correctly, received 250",
            })

            # ---------------------------------------------- 3. MAIL FROM
            # Verify the MAIL FROM command is recognised and the server
            # advances the transaction state to 'mail_from'.
            sock = socket.create_connection(("127.0.0.1", port), timeout=3)
            sock.settimeout(3)
            try:
                _read_smtp_response(sock)  # consume 220
                _smtp_exchange(sock, "HELO test.local")  # consume 250
                code, response = _smtp_exchange(
                    sock, "MAIL FROM:<sender@test.local>"
                )
                if code != 250:
                    raise RuntimeError(
                        f"MAIL FROM test: expected 250, got {code}"
                    )
            finally:
                sock.close()
            scenarios.append({
                "name": "smtp_mail_from",
                "status": "passed",
                "detail": "MAIL FROM command parsed correctly, received 250 OK",
            })

            # ------------------------------------------------ 4. RCPT TO
            # Verify the RCPT TO command is recognised and the server
            # advances the transaction state to 'rcpt_to'.
            sock = socket.create_connection(("127.0.0.1", port), timeout=3)
            sock.settimeout(3)
            try:
                _read_smtp_response(sock)  # consume 220
                _smtp_exchange(sock, "HELO test.local")
                _smtp_exchange(sock, "MAIL FROM:<sender@test.local>")
                code, response = _smtp_exchange(
                    sock, "RCPT TO:<rcpt@test.local>"
                )
                if code != 250:
                    raise RuntimeError(
                        f"RCPT TO test: expected 250, got {code}"
                    )
            finally:
                sock.close()
            scenarios.append({
                "name": "smtp_rcpt_to",
                "status": "passed",
                "detail": "RCPT TO command parsed correctly, received 250 OK",
            })

            # ------------------------------------------- 5. DATA delivery
            # Verify the full DATA flow: server returns 354 intermediate
            # reply, collects the email body (terminated by CRLF.CRLF), and
            # responds with 250 OK.  Then QUIT cleanly.
            sock = socket.create_connection(("127.0.0.1", port), timeout=3)
            sock.settimeout(3)
            try:
                _read_smtp_response(sock)  # consume 220
                _smtp_exchange(sock, "HELO test.local")
                _smtp_exchange(sock, "MAIL FROM:<sender@test.local>")
                _smtp_exchange(sock, "RCPT TO:<rcpt@test.local>")

                code, response = _smtp_exchange(sock, "DATA")
                if code != 354:
                    raise RuntimeError(
                        f"DATA test: expected 354 intermediate, got {code}"
                    )

                # Send email body, terminated by CRLF.CRLF.
                body = _build_email_body(
                    "sender@test.local",
                    "rcpt@test.local",
                    "Delivery Test",
                    "This is a test email body.",
                )
                sock.sendall(body.encode())

                code, response = _read_smtp_response(sock)
                if code != 250:
                    raise RuntimeError(
                        f"DATA test: expected 250 after body, got {code}"
                    )

                # Clean shutdown.
                code, response = _smtp_exchange(sock, "QUIT")
                if code != 221:
                    raise RuntimeError(
                        f"DATA test QUIT: expected 221, got {code}"
                    )
            finally:
                sock.close()
            scenarios.append({
                "name": "smtp_data_delivery",
                "status": "passed",
                "detail": "DATA command accepted (354), body collected, 250 OK, QUIT 221",
            })

            # ------------------------------------------ 6. bad sequence
            # Verify session/state enforcement: commands issued before the
            # required prior commands return 503 Bad sequence.
            #
            # 6a. MAIL FROM before HELO.
            sock = socket.create_connection(("127.0.0.1", port), timeout=3)
            sock.settimeout(3)
            try:
                _read_smtp_response(sock)  # consume 220
                code, _ = _smtp_exchange(sock, "MAIL FROM:<s@t.local>")
                if code not in (503, 500):
                    raise RuntimeError(
                        f"Bad sequence (MAIL before HELO): expected 503, got {code}"
                    )
            finally:
                sock.close()

            # 6b. DATA before MAIL FROM / RCPT TO (after HELO only).
            sock = socket.create_connection(("127.0.0.1", port), timeout=3)
            sock.settimeout(3)
            try:
                _read_smtp_response(sock)  # consume 220
                _smtp_exchange(sock, "HELO test.local")
                code, _ = _smtp_exchange(sock, "DATA")
                if code not in (503, 500):
                    raise RuntimeError(
                        f"Bad sequence (DATA before MAIL/RCPT): expected 503, got {code}"
                    )
            finally:
                sock.close()

            # 6c. DATA before RCPT TO (after HELO + MAIL FROM).
            sock = socket.create_connection(("127.0.0.1", port), timeout=3)
            sock.settimeout(3)
            try:
                _read_smtp_response(sock)  # consume 220
                _smtp_exchange(sock, "HELO test.local")
                _smtp_exchange(sock, "MAIL FROM:<s@t.local>")
                code, _ = _smtp_exchange(sock, "DATA")
                if code not in (503, 500):
                    raise RuntimeError(
                        f"Bad sequence (DATA before RCPT): expected 503, got {code}"
                    )
            finally:
                sock.close()

            scenarios.append({
                "name": "smtp_bad_sequence",
                "status": "passed",
                "detail": "out-of-sequence commands returned 503 Bad sequence",
            })

            # --------------------------------------- 7. unknown command
            # Verify error handling: an unrecognised command returns 500
            # (or 502) without crashing the server.
            sock = socket.create_connection(("127.0.0.1", port), timeout=3)
            sock.settimeout(3)
            try:
                _read_smtp_response(sock)  # consume 220
                _smtp_exchange(sock, "HELO test.local")
                code, _ = _smtp_exchange(sock, "GARBAGE")
                if code not in (500, 502):
                    raise RuntimeError(
                        f"Unknown command: expected 500/502, got {code}"
                    )
                # Server must still be alive — verify by sending QUIT.
                code, _ = _smtp_exchange(sock, "QUIT")
                if code != 221:
                    raise RuntimeError(
                        f"Unknown command survival QUIT: expected 221, got {code}"
                    )
            finally:
                sock.close()
            scenarios.append({
                "name": "smtp_unknown_command",
                "status": "passed",
                "detail": "unrecognised command returned 500; server survived and accepted QUIT",
            })

            # ------------------------------------------- 8. smoke test
            # Verify the main loop handles a complete SMTP transaction
            # end-to-end, and the server persists the received email to the
            # mail store directory.
            mail_files_before = set(os.listdir(tmpdir))

            sock = socket.create_connection(("127.0.0.1", port), timeout=3)
            sock.settimeout(3)
            try:
                code, _ = _read_smtp_response(sock)
                if code != 220:
                    raise RuntimeError(
                        f"Smoke test: expected 220 greeting, got {code}"
                    )

                code, _ = _smtp_exchange(sock, "HELO smoke.local")
                if code != 250:
                    raise RuntimeError(
                        f"Smoke test HELO: expected 250, got {code}"
                    )

                code, _ = _smtp_exchange(
                    sock, "MAIL FROM:<smoke-sender@test.local>"
                )
                if code != 250:
                    raise RuntimeError(
                        f"Smoke test MAIL FROM: expected 250, got {code}"
                    )

                code, _ = _smtp_exchange(
                    sock, "RCPT TO:<smoke-rcpt@test.local>"
                )
                if code != 250:
                    raise RuntimeError(
                        f"Smoke test RCPT TO: expected 250, got {code}"
                    )

                code, _ = _smtp_exchange(sock, "DATA")
                if code != 354:
                    raise RuntimeError(
                        f"Smoke test DATA: expected 354, got {code}"
                    )

                body = _build_email_body(
                    "smoke-sender@test.local",
                    "smoke-rcpt@test.local",
                    "Smoke Test Email",
                    "This email was delivered during the SMTP smoke test.",
                )
                sock.sendall(body.encode())
                code, _ = _read_smtp_response(sock)
                if code != 250:
                    raise RuntimeError(
                        f"Smoke test DATA body: expected 250, got {code}"
                    )

                code, _ = _smtp_exchange(sock, "QUIT")
                if code != 221:
                    raise RuntimeError(
                        f"Smoke test QUIT: expected 221, got {code}"
                    )
            finally:
                sock.close()

            # Verify mail was persisted.
            mail_files_after = set(os.listdir(tmpdir))
            new_files = mail_files_after - mail_files_before
            if not new_files:
                raise RuntimeError(
                    "Smoke test: no new file created in mail store directory "
                    f"({tmpdir}) after successful DATA delivery"
                )

            scenarios.append({
                "name": "smtp_smoke_test",
                "status": "passed",
                "detail": f"complete SMTP transaction succeeded, mail persisted ({len(new_files)} file(s))",
            })

        finally:
            stop_process(process)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
