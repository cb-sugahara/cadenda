#!/usr/bin/env python3
"""Probe fm serve over a private Unix socket; always stop our own process."""
from datetime import datetime, timezone
import json
import os
import signal
import socket
import subprocess
import time
import uuid

from probe_environment import ROOT


def main() -> None:
    destination = ROOT / "state" / "fm-serve" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination.mkdir(parents=True, mode=0o700)
    # macOS Unix socket paths have a short length limit.
    socket_path = f"/private/tmp/cadenda-fm-{uuid.uuid4().hex[:12]}.sock"
    argv = ["fm", "serve", "--socket", socket_path]
    summary = {"schema_version": 1, "argv": argv, "responses": {}}
    with (destination / "stdout.txt").open("wb") as stdout, (destination / "stderr.txt").open("wb") as stderr:
        process = subprocess.Popen(argv, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while not os.path.exists(socket_path):
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("fm serve did not create socket within 10 seconds")
                time.sleep(0.1)
            for endpoint in ("/health", "/v1/models"):
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(5)
                    client.connect(socket_path)
                    client.sendall(f"GET {endpoint} HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode())
                    chunks = []
                    while data := client.recv(65536):
                        chunks.append(data)
                        if sum(map(len, chunks)) > 1024 * 1024:
                            raise RuntimeError("health response exceeds 1 MiB")
                response = b"".join(chunks).decode()
                summary["responses"][endpoint] = response
                if not response.startswith(("HTTP/1.1 200", "HTTP/1.0 200")):
                    raise RuntimeError(f"{endpoint} did not return 200")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            summary["process_stopped"] = process.poll() is not None
            if os.path.exists(socket_path):
                os.unlink(socket_path)
            (destination / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
            print(f"Evidence: {destination}")
    print("fm serve health/models passed; no inference was requested through the server.")


if __name__ == "__main__":
    main()
