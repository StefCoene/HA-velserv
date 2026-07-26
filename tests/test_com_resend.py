#!/usr/bin/env python3
"""Regression test for the velserv duplicate-frame bug in com_to_sock().

com_to_sock() reads the serial port one byte at a time and reassembles Velbus
frames with a small state machine. `valid` is set to 1 when a frame terminates
correctly, and the forwarding step at the bottom of the loop is guarded by
`if (valid == 1)`. But `valid` is never reset to 0 after forwarding -- the only
assignment back to 0 sits in the "a new frame starts" branch, which requires an
incoming 0x0F while start_recv == 0.

Consequently every byte that is *not* a frame start byte falls through both
if-blocks without changing any state, reaches the unguarded forwarding step, and
re-sends the previous frame verbatim. One stray byte on the serial line = one
duplicate. A stream of them turns a single genuine bus event into a flood of
thousands of identical, checksum-valid copies -- exactly what showed up in the
Home Assistant logs (5831 copies of one PushButtonStatusMessage in 38 s, with no
other frame in sight, and a different address in each capture because it is
always whichever frame happened to be received last).

The same defect exists in sock_to_com(), where the resend would go to the bus
instead of to the TCP clients -- a duplicated SwitchRelayOn would switch a light
straight back on after Home Assistant switched it off. That direction turns out
not to be reachable in practice: server() forwards only complete, validated
frames and drops everything else (velserv.c:550-573), so the internal bridge
socket never sees a stray byte and `valid` is always reset by the next frame's
0x0F. Phase 2 below pins that down, so a future change to server()'s filtering
cannot quietly open the path.

The test drives both directions deterministically with a pty standing in for the
Velbus USB interface: it feeds velserv one valid frame followed by stray bytes
and counts how many frames come out the other side.

Run:  python3 tests/test_com_resend.py
Exit code 0 = no duplication (bug fixed), 1 = duplicates observed.
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
PORT = 47881  # unlikely to clash with a real velserv on 3788
HOST = "127.0.0.1"

# The exact frame that flooded the reporter's logs: PushButtonStatusMessage
# from module 0x07 (relaismod 7), channel 5. Checksum 0xde, ETX 0x04.
FRAME = binascii.unhexlify("0ff8070400100000de04")

# Bytes that are not 0x0F, so they never start a new frame and therefore never
# reset `valid`. This is what a desynced / noisy serial stream looks like.
STRAY = bytes([0x00, 0xAA, 0x00, 0x55, 0xFB, 0x00, 0x12, 0x00])


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


def drain(sock: socket.socket, settle: float = 0.4) -> bytes:
    """Collect everything velserv has broadcast to this client so far."""
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


def count_frames(data: bytes) -> int:
    """Count whole copies of FRAME in a byte stream."""
    return data.count(FRAME)


def drain_bus(master: int, settle: float = 0.6) -> bytes:
    """Collect everything velserv has written to the serial device.

    sock_to_com() sleeps 60 ms after every frame it writes, so allow for that
    when several frames are expected.
    """
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


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    with tempfile.TemporaryDirectory() as tmp:
        binary = os.path.join(tmp, "velserv")
        compile_velserv(binary)

        # A pty stands in for the Velbus USB interface. velserv opens the slave
        # by path, so close our own slave fd first -- otherwise both ends
        # compete for the bytes we write to the master.
        master, slave = pty.openpty()
        slave_name = os.ttyname(slave)
        os.close(slave)

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

            obs = client()
            time.sleep(0.3)

            # Baseline: nothing has been received from the serial side yet.
            # With `valid` uninitialised this may already emit garbage.
            pre = drain(obs, settle=0.3)
            ok = pre == b""
            results.append(
                (
                    "no traffic before any frame arrives",
                    ok,
                    f"{len(pre)} bytes: {binascii.hexlify(pre, ' ').decode()}"
                    if pre
                    else "nothing, as expected",
                )
            )

            # 1. One valid frame in, exactly one frame out.
            os.write(master, FRAME)
            got = drain(obs)
            n = count_frames(got)
            results.append(
                (
                    "1 valid frame -> 1 forwarded frame",
                    n == 1 and len(got) == len(FRAME),
                    f"{n} copies, {len(got)} bytes",
                )
            )

            # 2. The bug: stray bytes after that frame, each one re-sending it.
            os.write(master, STRAY)
            got = drain(obs)
            n = count_frames(got)
            results.append(
                (
                    f"{len(STRAY)} stray bytes -> 0 forwarded frames",
                    n == 0 and got == b"",
                    f"{n} duplicate copies ({len(got)} bytes) from "
                    f"{len(STRAY)} stray bytes",
                )
            )

            # 3. Scale it up: this is the flood mechanism at realistic volume.
            bulk = bytes([0x00]) * 500
            os.write(master, bulk)
            got = drain(obs, settle=1.0)
            n = count_frames(got)
            results.append(
                (
                    "500 stray bytes -> 0 forwarded frames",
                    n == 0,
                    f"{n} duplicate copies ({len(got)} bytes) from 500 stray bytes",
                )
            )

            # 4. Two genuine frames separated by stray bytes: the receiver must
            #    see each frame once, not the first one amplified.
            frame2 = binascii.unhexlify("0ff8350400000200be04")  # module 0x35
            os.write(master, FRAME + STRAY + frame2)
            got = drain(obs)
            results.append(
                (
                    "frame + stray + frame -> each frame once",
                    got == FRAME + frame2,
                    f"expected {len(FRAME + frame2)} bytes, got {len(got)}: "
                    f"{binascii.hexlify(got, ' ').decode()[:120]}",
                )
            )

            # ---------------------------------------------------------------
            # Phase 2: TCP -> serial bus, the sock_to_com() direction.
            # A duplicate here would be written onto the Velbus itself.
            # ---------------------------------------------------------------
            os.set_blocking(master, False)
            drain_bus(master, settle=0.2)  # discard anything left over

            sender = client()
            time.sleep(0.3)

            sender.sendall(FRAME)
            bus = drain_bus(master)
            n = count_frames(bus)
            results.append(
                (
                    "1 frame from a TCP client -> 1 frame on the bus",
                    n == 1 and bus == FRAME,
                    f"{n} copies, {len(bus)} bytes",
                )
            )

            # Stray bytes from a client must not amplify the previous frame.
            # server() drops them before they ever reach sock_to_com().
            sender.sendall(STRAY)
            bus = drain_bus(master)
            results.append(
                (
                    f"{len(STRAY)} stray bytes from a TCP client -> nothing on the bus",
                    bus == b"",
                    f"{count_frames(bus)} duplicate copies ({len(bus)} bytes)",
                )
            )

            sender.sendall(bytes([0x00]) * 500)
            bus = drain_bus(master, settle=1.0)
            results.append(
                (
                    "500 stray bytes from a TCP client -> nothing on the bus",
                    bus == b"",
                    f"{count_frames(bus)} duplicate copies ({len(bus)} bytes)",
                )
            )

            # Two real commands must arrive on the bus exactly once each.
            frame2 = binascii.unhexlify("0ff8350400000200be04")  # module 0x35
            sender.sendall(FRAME + STRAY + frame2)
            bus = drain_bus(master, settle=1.0)
            results.append(
                (
                    "frame + stray + frame from a TCP client -> each once on the bus",
                    bus == FRAME + frame2,
                    f"expected {len(FRAME + frame2)} bytes, got {len(bus)}: "
                    f"{binascii.hexlify(bus, ' ').decode()[:120]}",
                )
            )

            sender.close()
            obs.close()
        finally:
            proc.terminate()
            proc.wait()
            os.close(master)

    print(f"FRAME (10 bytes): {binascii.hexlify(FRAME, ' ').decode()}\n")
    for name, ok, detail in results:
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name}")
        print(f"         {detail}")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} scenarios passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
