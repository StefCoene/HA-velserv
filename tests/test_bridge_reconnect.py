#!/usr/bin/env python3
"""Regression test: velserv must rebuild its bridge socket after it breaks.

velserv runs two roles in one process. server() listens on the TCP port and
rebroadcasts every frame from one client to all the others. Separately,
sock_to_com()/com_to_sock() own the serial port and reach those clients over a
socket of their own -- in the default mode, velserv connecting to its own
listening port, so the Velbus interface is just another client in the star.

That bridge socket used to be made once, at startup, and neither thread looked
at what its recv()/send() returned. When the bridge went away, velserv kept
listening and kept accepting clients -- connecting still worked -- while no
traffic reached the bus any more. The only cure was restarting velserv, which
is exactly what was reported after a Home Assistant restart.

The test runs velserv in client-only mode (-c) so this test *is* the far end of
the bridge, with a pty standing in for the Velbus USB interface. It proves
traffic flows, drops the connection, and checks velserv comes back on its own
and carries traffic again.

Run:  python3 tests/test_bridge_reconnect.py
Exit code 0 = velserv reconnected and traffic resumed.
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
PORT = 47883  # unlikely to clash with a real velserv on 3788
HOST = "127.0.0.1"

# A module status frame, bus -> bridge.
FROM_BUS = binascii.unhexlify("0ff8070400100000de04")
# A relay switch frame, bridge -> bus.
TO_BUS = binascii.unhexlify("0ff8320402010000c604")

# velserv sleeps a second between reconnect attempts.
RECONNECT_TIMEOUT = 15.0


def compile_velserv(dst: str) -> None:
    subprocess.run(
        ["gcc", "-o", dst, SRC, "-lpthread"], check=True, capture_output=True
    )


def listener() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))
    sock.listen(5)
    return sock


def accept(server: socket.socket, timeout: float) -> socket.socket | None:
    server.settimeout(timeout)
    try:
        conn, _ = server.accept()
    except TimeoutError:
        return None
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return conn


def read_socket(conn: socket.socket, expect: bytes, timeout: float = 3.0) -> bool:
    conn.settimeout(0.3)
    got = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            chunk = conn.recv(65536)
        except TimeoutError:
            continue
        if not chunk:
            break
        got += chunk
        if expect in got:
            return True
    return expect in got


def read_bus(master: int, expect: bytes, timeout: float = 3.0) -> bool:
    got = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            chunk = os.read(master, 65536)
        except BlockingIOError:
            time.sleep(0.05)
            continue
        if not chunk:
            break
        got += chunk
        if expect in got:
            return True
    return expect in got


def exchange(conn: socket.socket, master: int, label: str) -> list[tuple[str, bool]]:
    os.write(master, FROM_BUS)
    to_client = read_socket(conn, FROM_BUS)

    conn.sendall(TO_BUS)
    to_bus = read_bus(master, TO_BUS)

    return [
        (f"{label}: bus -> bridge", to_client),
        (f"{label}: bridge -> bus", to_bus),
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

        server = listener()
        proc = subprocess.Popen(
            [binary, "-c", "-f", "-p", str(PORT), "-d", slave_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            first = accept(server, timeout=10.0)
            results.append(("velserv opens the bridge", first is not None))
            if first is None:
                raise SystemExit(1)

            results += exchange(first, master, "before the break")

            # What a restart on the other end looks like from here.
            first.close()

            second = accept(server, timeout=RECONNECT_TIMEOUT)
            results.append(("velserv rebuilds the bridge on its own", second is not None))
            if second is not None:
                results += exchange(second, master, "after the break")
                second.close()

            results.append(("velserv still running", proc.poll() is None))
        finally:
            proc.terminate()
            proc.wait(timeout=5)
            server.close()
            os.close(master)

    for name, ok in results:
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
