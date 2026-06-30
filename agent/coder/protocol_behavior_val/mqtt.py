from __future__ import annotations

import shutil
import socket
import subprocess
import time
from pathlib import Path

from .common import free_port, start_server, stop_process, wait_for_tcp

EXPECTED_SCENARIOS = (
    "mqtt_cross_client_pubsub",
    "mqtt_buffer_before_eof",
    "mqtt_malformed_survival",
    "mqtt_disconnect_handler",
    "mqtt_smoke_test",
    "mqtt_mosquitto_interop",
)


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


def _pkt_disconnect() -> bytes:
    return b"\xe0\x00"


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


def _mqtt_client(port: int, client_id: str) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", port), timeout=2)
    sock.settimeout(2)
    sock.sendall(_pkt_connect(client_id))
    if _read_mqtt_packet(sock)[0] != 2:
        raise RuntimeError("MQTT client did not receive CONNACK")
    return sock


# ---------------------------------------------------------------------------
# run entry-point
# ---------------------------------------------------------------------------


def run(project_dir: Path, binary_name: str, scenarios: list[dict[str, str]]) -> None:
    port = free_port(socket.SOCK_STREAM)
    process = start_server(project_dir, binary_name, port)
    sockets: list[socket.socket] = []
    try:
        wait_for_tcp(port)

        # -------------------------------------------- 1. cross-client pub/sub
        try:
            # Verify transport (TCP listen/accept, 2 concurrent clients),
            # codec/parser (CONNECT, SUBSCRIBE, PUBLISH), codec/serializer
            # (CONNACK, SUBACK, forwarded PUBLISH), session (topic→subscriber
            # mapping), and handler (CONNECT, SUBSCRIBE, PUBLISH).
            subscriber = _mqtt_client(port, "smoke-sub")
            sockets.append(subscriber)
            subscriber.sendall(_pkt_subscribe(1, "a/#"))
            if _read_mqtt_packet(subscriber)[0] != 9:
                raise RuntimeError("MQTT subscriber did not receive SUBACK")
    
            publisher = _mqtt_client(port, "smoke-pub")
            sockets.append(publisher)
            publisher.sendall(_pkt_publish("a/b", b"cross-client"))
            packet_type, body = _read_mqtt_packet(subscriber)
            if packet_type != 3 or b"cross-client" not in body:
                raise RuntimeError(
                    "MQTT broker did not forward PUBLISH to another client: "
                    f"type={packet_type} body={body!r}"
                )
            scenarios.append({
                "name": "mqtt_cross_client_pubsub",
                "status": "passed",
                "detail": "QoS0 PUBLISH forwarded from publisher to subscriber",
            })
        except Exception as _e:
            scenarios.append({
                "name": "mqtt_cross_client_pubsub",
                "status": "failed",
                "detail": str(_e),
            })

        # ----------------------------------------------- 2. buffer before EOF
        try:
            # Verify the broker processes buffered data in the socket before
            # handling a peer close — a PUBLISH immediately followed by DISCONNECT
            # should still be delivered to the subscriber.
            quick = _mqtt_client(port, "quick-pub")
            quick.sendall(_pkt_publish("a/quick", b"before-eof") + b"\xe0\x00")
            quick.close()
            packet_type, body = _read_mqtt_packet(subscriber)
            if packet_type != 3 or b"before-eof" not in body:
                raise RuntimeError(
                    "MQTT broker discarded buffered PUBLISH before peer EOF: "
                    f"type={packet_type} body={body!r}"
                )
            scenarios.append({
                "name": "mqtt_buffer_before_eof",
                "status": "passed",
                "detail": "buffered PUBLISH processed before peer close",
            })
        except Exception as _e:
            scenarios.append({
                "name": "mqtt_buffer_before_eof",
                "status": "failed",
                "detail": str(_e),
            })

        # ---------------------------------------------- 3. malformed survival
        try:
            # Verify error handling: a malformed packet does not crash the broker;
            # a new client can still CONNECT afterwards.
            malformed = socket.create_connection(("127.0.0.1", port), timeout=2)
            malformed.sendall(b"\xff\xff\xff\xff\xff")
            malformed.close()
            time.sleep(0.1)
            survivor = _mqtt_client(port, "after-malformed")
            survivor.close()
            if process.poll() is not None:
                raise RuntimeError("MQTT broker exited after malformed packet")
            scenarios.append({
                "name": "mqtt_malformed_survival",
                "status": "passed",
                "detail": "broker accepted a later CONNECT after malformed packet",
            })
        except Exception as _e:
            scenarios.append({
                "name": "mqtt_malformed_survival",
                "status": "failed",
                "detail": str(_e),
            })

        # -------------------------------------------- 4. DISCONNECT handler
        try:
            # Verify the broker properly handles a client DISCONNECT: the
            # disconnecting client is cleaned up, the broker remains stable, and
            # other connected clients continue to receive messages.
            dc_pub = _mqtt_client(port, "dc-pub")
            dc_pub.sendall(_pkt_publish("a/disconnect", b"before-disconnect"))
            packet_type, body = _read_mqtt_packet(subscriber)
            if packet_type != 3 or b"before-disconnect" not in body:
                raise RuntimeError(
                    "MQTT DISCONNECT pre-check: expected PUBLISH to subscriber, "
                    f"got type={packet_type} body={body!r}"
                )
            # Send DISCONNECT and close the publisher.
            dc_pub.sendall(_pkt_disconnect())
            dc_pub.close()
    
            # Connect a new publisher — broker must still accept connections and
            # forward messages to the existing subscriber.
            after_pub = _mqtt_client(port, "after-dc-pub")
            sockets.append(after_pub)
            after_pub.sendall(_pkt_publish("a/after-disconnect", b"after-disconnect"))
            packet_type, body = _read_mqtt_packet(subscriber)
            if packet_type != 3 or b"after-disconnect" not in body:
                raise RuntimeError(
                    "MQTT DISCONNECT handler: broker did not forward PUBLISH "
                    f"after previous client disconnected — type={packet_type} body={body!r}"
                )
            scenarios.append({
                "name": "mqtt_disconnect_handler",
                "status": "passed",
                "detail": "DISCONNECT cleaned up client; new publisher and subscriber still work",
            })
        except Exception as _e:
            scenarios.append({
                "name": "mqtt_disconnect_handler",
                "status": "failed",
                "detail": str(_e),
            })

        # --------------------------------------------------- 5. smoke test
        try:
            # Verify the broker main loop handles multiple sequential pub/sub
            # cycles without crashing or losing messages.
            subscriber.sendall(_pkt_subscribe(2, "smoke/#"))
            if _read_mqtt_packet(subscriber)[0] != 9:
                raise RuntimeError("MQTT smoke test: SUBACK not received for smoke/#")
            for i in range(5):
                pub = _mqtt_client(port, f"smoke-pub-{i}")
                topic = f"smoke/{i}"
                payload = f"msg-{i}".encode()
                pub.sendall(_pkt_publish(topic, payload))
                packet_type, body = _read_mqtt_packet(subscriber)
                pub.sendall(_pkt_disconnect())
                pub.close()
                if packet_type != 3 or payload not in body:
                    raise RuntimeError(
                        f"MQTT smoke test iteration {i}: "
                        f"expected PUBLISH with {payload!r}, "
                        f"got type={packet_type} body={body!r}"
                    )
            scenarios.append({
                "name": "mqtt_smoke_test",
                "status": "passed",
                "detail": "5 sequential pub/sub cycles completed without failure",
            })
        except Exception as _e:
            scenarios.append({
                "name": "mqtt_smoke_test",
                "status": "failed",
                "detail": str(_e),
            })

        # ----------------------------------------- 6. mosquitto interop
        try:
            # Verify interoperability with mosquitto_sub / mosquitto_pub clients.
            # Skipped if the tools are not installed.
            if shutil.which("mosquitto_sub") and shutil.which("mosquitto_pub"):
                sub = subprocess.Popen(
                    [
                        "mosquitto_sub", "-h", "127.0.0.1", "-p", str(port),
                        "-t", "interop/topic", "-q", "0", "-C", "1", "-W", "3",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    time.sleep(0.4)
                    pub = subprocess.run(
                        [
                            "mosquitto_pub", "-h", "127.0.0.1", "-p", str(port),
                            "-t", "interop/topic", "-q", "0", "-m", "interop-ok",
                        ],
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
                    raise RuntimeError(
                        "mosquitto interoperability failed: "
                        f"{error or pub.stderr}".strip()
                    )
                scenarios.append({
                    "name": "mqtt_mosquitto_interop",
                    "status": "passed",
                    "detail": "mosquitto_sub received mosquitto_pub payload",
                })
            else:
                scenarios.append({
                    "name": "mqtt_mosquitto_interop",
                    "status": "skipped",
                    "detail": "mosquitto clients unavailable",
                })
        except Exception as _e:
            scenarios.append({
                "name": "mqtt_mosquitto_interop",
                "status": "failed",
                "detail": str(_e),
            })

    finally:
        for sock in sockets:
            sock.close()
        stop_process(process)
