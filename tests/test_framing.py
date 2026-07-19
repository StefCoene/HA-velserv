#!/usr/bin/env python3
"""Regression test for the velserv TCP reassembly / framing bug.

velserv rebroadcasts every Velbus frame received from one client to all other
clients. TCP is a byte stream with no message boundaries, so a single recv()
may hold a partial frame or several frames. An earlier version of server()
assumed one recv() == one frame: it read the length byte, copied that many
bytes from a zero-initialised buffer regardless of how many had actually
arrived, and forwarded the result immediately. A frame split across two recv()
calls was therefore padded with zeros and sent on as a corrupt, unparseable
message -- the "Could not parse the message ... Truncating invalid data"
errors seen by Home Assistant / velbus-aio on a busy bus.

This test drives the exact failure deterministically (no Velbus hardware, no
reliance on bus timing): it connects two TCP clients, has one send frames in
deliberately fragmented writes, and checks that the other receives them intact.

Run:  python3 tests/test_framing.py
Exit code 0 = all scenarios pass.
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
PORT = 47880  # unlikely to clash with a real velserv on 3788
HOST = "127.0.0.1"

# A real temperature frame from module 0x3C (checksum 0x7b, ETX 0x04),
# taken from the Home Assistant logs that first exposed the bug.
F1 = binascii.unhexlify("0ffb3c07e62fa026403de07b04")


def _velbus_frame(header_no_tail: bytes) -> bytes:
    """Append the Velbus checksum + end byte to a header+data byte string."""
    cks = (256 - (sum(header_no_tail) & 0xFF)) & 0xFF
    return header_no_tail + bytes([cks, 0x04])


# A short 6-byte frame (0 data bytes) from module 0xBE.
F2 = _velbus_frame(binascii.unhexlify("0ffbbe00"))


def compile_velserv(dst: str) -> None:
    subprocess.run(
        ["gcc", "-Wall", "-o", dst, SRC, "-lpthread"],
        check=True,
        capture_output=True,
    )


def client() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((HOST, PORT))
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return s


def run_scenario(name: str, chunks: list[bytes], expect: bytes) -> bool:
    """Send `chunks` (with a small gap so TCP does not coalesce them) from one
    client and check the rebroadcast received by another equals `expect`."""
    sender, receiver = client(), client()
    time.sleep(0.2)  # let velserv register both sockets
    for chunk in chunks:
        sender.sendall(chunk)
        time.sleep(0.02)
    time.sleep(0.2)

    receiver.setblocking(False)
    got = b""
    try:
        while True:
            data = receiver.recv(4096)
            if not data:
                break
            got += data
    except BlockingIOError:
        pass
    sender.close()
    receiver.close()

    ok = got == expect
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"         expected: {binascii.hexlify(expect, ' ').decode()}")
        print(f"         received: {binascii.hexlify(got, ' ').decode()}")
    return ok


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        binary = os.path.join(tmp, "velserv")
        compile_velserv(binary)

        # server-only mode (-s) needs no serial device; -v keeps it in the
        # foreground (verbose level 1 adds no output on the forwarding path, so
        # it does not mask the timing-dependent bug).
        proc = subprocess.Popen(
            [binary, "-s", "-v", "-p", str(PORT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(1.0)
            print(f"F1 (13 bytes): {binascii.hexlify(F1, ' ').decode()}")
            print(f"F2 ( 6 bytes): {binascii.hexlify(F2, ' ').decode()}\n")

            results = []

            # The original bug: one frame split at every interior byte.
            print("Fragmented single frame (the logged failure):")
            for split in range(4, len(F1)):
                results.append(
                    run_scenario(
                        f"F1 split after {split:2d} bytes",
                        [F1[:split], F1[split:]],
                        F1,
                    )
                )

            print("\nEdge cases:")
            results.append(
                run_scenario("two frames coalesced in one send", [F1 + F2], F1 + F2)
            )
            results.append(
                run_scenario("F1 byte-by-byte", [F1[k : k + 1] for k in range(len(F1))], F1)
            )
            results.append(
                run_scenario("garbage (00 ff aa) before F1", [b"\x00\xff\xaa" + F1], F1)
            )
            results.append(
                run_scenario(
                    "F1+F2 split in the middle of F2",
                    [(F1 + F2)[:15], (F1 + F2)[15:]],
                    F1 + F2,
                )
            )
            results.append(
                run_scenario(
                    "F1 twice, split on the frame boundary",
                    [F1[:10], F1[10:] + F1[:4], F1[4:]],
                    F1 + F1,
                )
            )
            results.append(
                run_scenario("half header, then the rest", [F1[:2], F1[2:]], F1)
            )
        finally:
            proc.terminate()
            proc.wait()

    passed = results.count(True)
    total = len(results)
    print(f"\n{passed}/{total} scenarios passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
