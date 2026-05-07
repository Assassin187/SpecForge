#!/usr/bin/env python3
import socket
import struct
import sys


def encode_string(value):
    data = value.encode("utf-8")
    return struct.pack("!H", len(data)) + data


def encode_remaining_length(value):
    out = []
    while True:
        digit = value % 128
        value //= 128
        if value:
            digit |= 128
        out.append(digit)
        if not value:
            return bytes(out)


def packet(packet_type, body=b""):
    return bytes([packet_type]) + encode_remaining_length(len(body)) + body


def recv_packet(sock, timeout=2.0):
    sock.settimeout(timeout)
    header = sock.recv(1)
    if not header:
        raise RuntimeError("connection closed before packet header")

    multiplier = 1
    remaining = 0
    while True:
        digit = sock.recv(1)[0]
        remaining += (digit & 127) * multiplier
        if not digit & 128:
            break
        multiplier *= 128

    body = b""
    while len(body) < remaining:
        chunk = sock.recv(remaining - len(body))
        if not chunk:
            raise RuntimeError("connection closed before packet body")
        body += chunk

    return header + encode_remaining_length(remaining) + body


def expect(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected.hex()}, got {actual.hex()}")
    print(f"{name}: ok ({actual.hex()})")


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 18884

    connect_body = (
        encode_string("MQTT")
        + bytes([4, 2])
        + struct.pack("!H", 30)
        + encode_string("client_a")
    )
    subscribe_body = struct.pack("!H", 1) + encode_string("demo/topic") + bytes([0])
    publish_body = encode_string("demo/topic") + b"hello"

    with socket.create_connection((host, port), timeout=2.0) as sock:
        sock.sendall(packet(0x10, connect_body))
        expect("CONNACK", recv_packet(sock), bytes.fromhex("20020000"))

        sock.sendall(packet(0x82, subscribe_body))
        expect("SUBACK", recv_packet(sock), bytes.fromhex("9003000100"))

        sock.sendall(packet(0x30, publish_body))
        received = recv_packet(sock)
        expected_publish = packet(0x30, publish_body)
        expect("PUBLISH loopback", received, expected_publish)

        sock.sendall(packet(0xC0))
        expect("PINGRESP", recv_packet(sock), bytes.fromhex("d000"))

        sock.sendall(packet(0xE0))


if __name__ == "__main__":
    main()
