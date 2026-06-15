from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path


def free_port(sock_type: int) -> int:
    with socket.socket(socket.AF_INET, sock_type) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_server(project_dir: Path, binary_name: str, port: int, extra_args: list[str] | None = None) -> subprocess.Popen[bytes]:
    cmd = [f"./{binary_name}", str(port)]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.Popen(
        cmd,
        cwd=project_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_process(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def wait_for_tcp(port: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise TimeoutError(f"server did not listen on TCP port {port}")
