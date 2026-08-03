#!/usr/bin/env python3
"""Regression test: velserv must keep working after a client reconnects.

Reported symptom: after restarting Home Assistant, velserv still accepts the
connection but no traffic passes in either direction, and only restarting
velserv itself brings the bus back.

velserv runs two roles in one process. server() listens on the TCP port and
rebroadcasts every frame from one client to all the others. Separately,
sock_to_com()/com_to_sock() own the serial port and reach the other clients by
connecting to velserv's own listening port as just another client. The serial
port therefore hangs off an internal loopback connection, and every client
depends on it.

That internal connection is made once, at startup, and neither thread checks
the result of its recv()/send(). If it ever goes away, velserv keeps listening
and keeps accepting clients -- so connecting still works -- while nothing
reaches the bus any more.

This test drives the reported sequence with a pty standing in for the Velbus
USB interface: one client exchanges traffic in both directions, disconnects the
way a restarting Home Assistant does, reconnects, and exchanges traffic again.

Run:  python3 tests/test_client_reconnect.py
Exit code 0 = traffic still flows after the reconnect.
"""

import binascii
import os
import pty
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "velserv", "velserv.c")
PORT = 47882  # unlikely to clash with a real velserv on 3788
HOST = "127.0.0.1"

# A module status frame, bus -> client.
FROM_BUS = binascii.unhexlify("0ff8070400100000de04")
# A relay switch frame, client -> bus.
TO_BUS = binascii.unhexlify("0ff8320402010000c604")


def compile_velserv(dst: str) -> None:
    subprocess.run(
        ["gcc", "-Wall", "-o", dst, SRC, "-lpthread"],
        check=True,
        capture_output=True,
    )


def client() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, PORT))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return sock


def drain(sock: socket.socket, settle: float = 0.5) -> bytes:
    time.sleep(settle)
    sock.setblocking(False)
    got = b""
    try:
        while True:
            data = sock.recv(65536)
            if not data:
                break
            got += data
    except BlockingIOError:
        pass
    sock.setblocking(True)
    return got


def drain_bus(master: int, settle: float = 0.6) -> bytes:
    time.sleep(settle)
    got = b""
    while True:
        try:
            chunk = os.read(master, 65536)
        except BlockingIOError:
            break
        if not chunk:
            break
        got += chunk
    return got


def exchange(sock: socket.socket, master: int, label: str) -> list[tuple[str, bool]]:
    """Send a frame each way and report whether each one arrived."""
    drain(sock, 0.1)
    drain_bus(master, 0.1)

    os.write(master, FROM_BUS)
    got = drain(sock)

    sock.sendall(TO_BUS)
    on_bus = drain_bus(master)

    return [
        (f"{label}: bus -> client", FROM_BUS in got),
        (f"{label}: client -> bus", TO_BUS in on_bus),
    ]


def main() -> int:
    results: list[tuple[str, bool]] = []

    with tempfile.TemporaryDirectory() as tmp:
        binary = os.path.join(tmp, "velserv")
        compile_velserv(binary)

        master, slave = pty.openpty()
        slave_name = os.ttyname(slave)
        os.close(slave)
        os.set_blocking(master, False)

        proc = subprocess.Popen(
            [binary, "-f", "-p", str(PORT), "-d", slave_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(2.0)  # velserv sleeps 1 s before opening the device
            if proc.poll() is not None:
                print(f"velserv exited early (rc={proc.returncode})")
                return 2

            first = client()
            results += exchange(first, master, "before restart")

            # How a restarting Home Assistant leaves: the socket just closes.
            first.close()
            time.sleep(1.0)

            second = client()
            results += exchange(second, master, "after reconnect")
            second.close()

            results.append(("velserv still running", proc.poll() is None))
        finally:
            proc.terminate()
            proc.wait(timeout=5)
            os.close(master)

    for name, ok in results:
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
