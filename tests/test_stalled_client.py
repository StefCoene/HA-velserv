#!/usr/bin/env python3
"""Regression test: one stalled client must not stop velserv serving the rest.

Reported symptom: a laptop running VelbusLink over a VPN went to sleep, and
some time later velserv passed no traffic at all and stopped accepting new
connections, while the process was still running. Only restarting the add-on
brought the bus back.

A sleeping machine behind a VPN leaves a half-open TCP connection: no FIN, no
RST, the packets simply stop arriving. velserv has no way to know the client is
gone, keeps rebroadcasting frames to that socket, and its send buffer fills up.
server() then blocks in send() -- and it does everything in one thread, so the
whole star stops: no traffic for the other clients, and accept() is never
called again, so new connections just time out.

The test stands in for that laptop with a client that connects and never reads,
and measures how much velserv still takes from a healthy client meanwhile. It
runs velserv in server-only mode (-s), so no serial device is needed.

It measures throughput rather than proving the deadlock: socket buffers absorb
several megabytes, so a blocking send() throttles velserv hard without
necessarily wedging it within the seconds this test runs. On this machine the
difference is about a hundredfold -- roughly 600 MB moved with the fix against
5 MB without -- so the threshold sits far below the healthy figure and far
above the stalled one. Treat a failure as "one dead client is holding
everything up", not as an exact reproduction of the reported outage.

Run:  python3 tests/test_stalled_client.py
Exit code 0 = velserv kept serving while one client was stalled.
"""

import binascii
import os
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "velserv", "velserv.c")
PORT = 47884  # unlikely to clash with a real velserv on 3788
HOST = "127.0.0.1"

FRAME = binascii.unhexlify("0ff8070400100000de04")
# Enough to fill the send buffer towards a client that never reads.
BURST = FRAME * 500
FLOOD_SECONDS = 6.0
# Enough to wedge a client with a small receive buffer, and no more: a real bus
# trickles frames, and burying every client under megabytes would say nothing
# about the failure being reproduced here.
FLOOD_BYTES = 300_000
# Far under what a healthy velserv moves in the window below, and far over what
# one throttled by a stalled client manages. See the note in the docstring.
THROUGHPUT_FLOOR = 50_000_000


def compile_velserv(dst: str) -> None:
    subprocess.run(
        ["gcc", "-o", dst, SRC, "-lpthread"], check=True, capture_output=True
    )


def stalled_client() -> socket.socket:
    """A client that connects and never reads a byte, like a sleeping laptop."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # A small receive buffer fills quickly, so the test does not have to push
    # megabytes to reach the state a sleeping client reaches on its own.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
    sock.connect((HOST, PORT))
    return sock


def main() -> int:
    results: list[tuple[str, bool]] = []

    with tempfile.TemporaryDirectory() as tmp:
        binary = os.path.join(tmp, "velserv")
        compile_velserv(binary)

        proc = subprocess.Popen(
            [binary, "-s", "-f", "-p", str(PORT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(1.5)
            if proc.poll() is not None:
                print(f"velserv exited early (rc={proc.returncode})")
                return 2

            stalled = stalled_client()
            sender = socket.create_connection((HOST, PORT))
            sender.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            time.sleep(0.3)

            # Push frames at the stalled client. Non-blocking, because velserv
            # stops reading once it is stuck and sendall() would hang the test
            # instead of reporting on it.
            sender.setblocking(False)
            sent = 0
            deadline = time.time() + FLOOD_SECONDS
            while time.time() < deadline and sent < FLOOD_BYTES:
                try:
                    sent += sender.send(BURST)
                except BlockingIOError:
                    time.sleep(0.05)
            print(f"# pushed {sent} bytes towards a client that never reads")
            time.sleep(2.0)  # let the backlog drain before asking anything

            # The question is not whether that client keeps up, but whether
            # everyone else is still served.
            start = time.time()
            try:
                fresh = socket.create_connection((HOST, PORT), timeout=5)
                fresh.close()
                accepted = True
            except OSError:
                accepted = False
            results.append(
                (f"still accepts clients ({time.time() - start:.1f}s)", accepted)
            )

            # The sharpest signal that velserv is wedged: a thread stuck in
            # send() is not reading either, so what this sender offers stops
            # being taken. A healthy velserv keeps draining it.
            moved = 0
            deadline = time.time() + 3.0
            while time.time() < deadline:
                try:
                    moved += sender.send(BURST)
                except BlockingIOError:
                    time.sleep(0.05)
            moved_mb = moved / 1e6
            results.append(
                (
                    f"one stalled client does not throttle the rest "
                    f"({moved_mb:.0f} MB moved)",
                    moved > THROUGHPUT_FLOOR,
                )
            )
            stalled.close()
            sender.close()
            results.append(("velserv still running", proc.poll() is None))
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    for name, ok in results:
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
