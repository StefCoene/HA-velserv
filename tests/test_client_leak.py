#!/usr/bin/env python3
"""Regression test: a TCP client that connects and disconnects must not leak.

server() mallocs a copy of the peer address for every accepted connection
(ip_add_arr[newfd]). It was only freed when the client hung up cleanly *and*
verbose > 3 -- the free() sat inside the logging block -- and never on a recv()
error or when a stalled client was dropped. With the add-on's default verbose
level every connection leaked its allocation.

Small per connection, but unbounded: a client in a reconnect loop grows velserv
for as long as it runs. Reported alongside the CPU spin in
https://github.com/StefCoene/velserver/issues/9 ("RAM can almost completely fill
up ... when I restart, everything appears to be fine again").

The test runs velserv in server-only mode (-s, no serial device), opens and
closes many connections and compares the resident set size before and after.

Run:  python3 tests/test_client_leak.py
Exit code 0 = no measurable growth, 1 = memory grows with the connection count.
"""

import os
import socket
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "velserv", "velserv.c")
PORT = 47885
HOST = "127.0.0.1"
ROUNDS = 100000


def compile_velserv(dst: str) -> None:
    subprocess.run(
        ["gcc", "-Wall", "-o", dst, SRC, "-lpthread"],
        check=True,
        capture_output=True,
    )


def rss_kib(pid: int) -> int:
    with open(f"/proc/{pid}/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    raise RuntimeError("no VmRSS")


LINGER_RST = struct.pack("ii", 1, 0)  # l_onoff=1, l_linger=0 -> close() sends RST


def churn(n: int) -> None:
    """Connect and drop n times.

    Every connection is reset (SO_LINGER 0) instead of closed with a FIN. That
    leaves no TIME_WAIT behind -- 100k clean closes would park the client side
    of each connection on a local port for a minute, and the local port range
    (here 44620-48715) includes the fixed ports the other tests listen on, so
    they failed to bind afterwards. A reset also lands velserv on its recv()
    error path, the one where the old code never freed at all.
    """
    for _ in range(n):
        s = socket.create_connection((HOST, PORT))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, LINGER_RST)
        s.close()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        binary = os.path.join(tmp, "velserv")
        compile_velserv(binary)
        proc = subprocess.Popen(
            [binary, "-s", "-f", "-p", str(PORT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(1.0)
            if proc.poll() is not None:
                print(f"velserv exited early (rc={proc.returncode})")
                return 2
            churn(2000)          # warm up: let malloc settle its arenas
            time.sleep(0.5)
            before = rss_kib(proc.pid)
            churn(ROUNDS)
            time.sleep(0.5)
            after = rss_kib(proc.pid)
        finally:
            proc.kill()
            proc.wait()

    grown = after - before
    # The old code leaked ~27 bytes per connection, ~2.6 MiB here; allow noise.
    ok = grown < 128
    print(
        f"[{'PASS' if ok else 'FAIL'}] {ROUNDS} connect/disconnect cycles: "
        f"RSS {before} -> {after} KiB ({grown:+d} KiB, "
        f"{grown * 1024 / ROUNDS:.1f} bytes/connection)"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
