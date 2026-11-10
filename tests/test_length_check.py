#!/usr/bin/env python3
"""Regression test: a frame header claiming more than 8 data bytes is rejected.

A Velbus frame carries at most 8 data bytes, but the length sits in a 4-bit
field (byte 3, low nibble), so a corrupted header can claim up to 15. velserv
trusted it blindly:

- com_to_sock() / sock_to_com() then waited for up to 20 bytes before checking
  the ETX. Everything in between -- including the next, perfectly good frame --
  was swallowed as "payload" of the bogus one. One flipped bit on the line cost
  two frames, not one.
- server() forwarded any "frame" with 9..15 data bytes to every client, as long
  as a 0x04 happened to sit at the computed end. And a bogus header followed by
  a good frame in one recv() left that good frame parked in the reassembly
  buffer, waiting for bytes that would never complete the bogus one.

The fix borrows the rule from the ESP32 Velbus gateway firmware
(github.com/MDAR/velbus_AI_commisioning, TCP_Gateway): data length > 8 means
the header is garbage -- drop it and resync on the next 0x0F.

Run:  python3 tests/test_length_check.py
Exit code 0 = all scenarios pass, 1 = a bogus length swallowed or leaked frames.
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
PORT_COM = 47886
PORT_SRV = 47887
HOST = "127.0.0.1"

# A real PushButtonStatusMessage (same one test_com_resend.py uses).
FRAME = binascii.unhexlify("0ff8070400100000de04")
# A second, different valid frame.
FRAME2 = binascii.unhexlify("0ff8350400000200be04")
# A header whose length nibble says 13 data bytes -- impossible on Velbus.
BAD_HEADER = binascii.unhexlify("0ff8070d")


def bogus_but_terminated(n: int) -> bytes:
    """A 'frame' with n (> 8) data bytes that does end on 0x04 where velserv
    would look for it, so only the length check can stop it."""
    body = bytes([0x0F, 0xF8, 0x07, n]) + bytes([0x11] * n)
    cks = (-sum(body)) & 0xFF
    return body + bytes([cks, 0x04])


def compile_velserv(dst: str) -> None:
    subprocess.run(
        ["gcc", "-Wall", "-o", dst, SRC, "-lpthread"],
        check=True,
        capture_output=True,
    )


def client(port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((HOST, port))
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return s


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


def hexs(b: bytes) -> str:
    return binascii.hexlify(b, " ").decode()[:90] if b else "nothing"


def phase_com(binary: str, results: list) -> None:
    """Serial -> TCP: com_to_sock()."""
    master, slave = pty.openpty()
    slave_name = os.ttyname(slave)
    os.close(slave)
    proc = subprocess.Popen(
        [binary, "-f", "-p", str(PORT_COM), "-d", slave_name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(2.0)
        obs = client(PORT_COM)
        time.sleep(0.3)
        drain(obs, 0.2)

        # Sanity: a good frame passes.
        os.write(master, FRAME)
        got = drain(obs)
        results.append(("bus: valid frame passes", got == FRAME, hexs(got)))

        # A bogus but well-terminated 'frame' must not be forwarded at all.
        # (Runs before the bad-header case: that one, unfixed, leaves the state
        # machine mid-frame, which would swallow this input and fake a pass.)
        os.write(master, bogus_but_terminated(12) + FRAME)
        got = drain(obs)
        results.append(
            (
                "bus: 12-data-byte 'frame' dropped, following frame passes",
                got == FRAME,
                hexs(got),
            )
        )

        # The bug: a bogus header right before a good frame swallows it.
        os.write(master, BAD_HEADER + FRAME2)
        got = drain(obs)
        results.append(
            (
                "bus: bad length header does not swallow the next frame",
                got == FRAME2,
                hexs(got),
            )
        )

        # A truncated frame directly followed by a real one: the "length" byte
        # is then the real frame's 0x0F. Rejecting it must restart on it, or
        # the real frame loses its start byte and is gone too.
        os.write(master, FRAME[:3] + FRAME2)
        got = drain(obs)
        results.append(
            (
                "bus: truncated frame + real frame -> real frame survives",
                got == FRAME2,
                hexs(got),
            )
        )

        # Every legal length 0..8 must still pass.
        legal = b""
        for n in range(9):
            body = bytes([0x0F, 0xF8, 0x07, n]) + bytes([0x22] * n)
            legal += body + bytes([(-sum(body)) & 0xFF, 0x04])
        os.write(master, legal)
        got = drain(obs, 0.8)
        results.append(
            ("bus: frames with 0..8 data bytes all pass", got == legal,
             f"{len(got)}/{len(legal)} bytes")
        )
        obs.close()
    finally:
        proc.kill()
        proc.wait()
        os.close(master)


def phase_server(binary: str, results: list) -> None:
    """TCP client -> other TCP clients: server()."""
    proc = subprocess.Popen(
        [binary, "-s", "-f", "-p", str(PORT_SRV)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(1.0)
        sender, obs = client(PORT_SRV), client(PORT_SRV)
        time.sleep(0.3)

        sender.sendall(FRAME)
        got = drain(obs)
        results.append(("tcp: valid frame passes", got == FRAME, hexs(got)))

        sender.sendall(bogus_but_terminated(12))
        got = drain(obs)
        results.append(
            ("tcp: 12-data-byte 'frame' is not forwarded", got == b"", hexs(got))
        )

        sender.sendall(BAD_HEADER + FRAME2)
        got = drain(obs)
        results.append(
            (
                "tcp: bad length header + good frame in one write -> good frame now",
                got == FRAME2,
                hexs(got),
            )
        )
        sender.close()
        obs.close()
    finally:
        proc.kill()
        proc.wait()


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory() as tmp:
        binary = os.path.join(tmp, "velserv")
        compile_velserv(binary)
        phase_com(binary, results)
        phase_server(binary, results)

    failed = 0
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        failed += not ok
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
