#!/usr/bin/env python3
"""Regression test: velserv must not spin when the serial device goes away.

com_to_sock() reads the serial port one byte at a time in a while(1) loop and
only handles `read() > 0`. When the device disappears or errors out -- a USB
hiccup, the VMB1USB being re-enumerated, a pass-through glitch in the VM --
read() returns -1 (EIO) or 0 immediately, every time. The loop falls straight
through and calls read() again, millions of times a second.

Seen in production 07/11/2026 -> 09/11/2026: the add-on sat at exactly one
full core (50 % on a 2-vCPU VM) for 2.5 days, passed zero bytes to Home
Assistant, and logged nothing. The process looked alive, so the watchdog never
restarted it; Velbus itself kept working in the house, so nobody noticed. Only a
manual restart of the add-on brought the data back.

The fix: on read() <= 0 (other than EINTR/EAGAIN) log the error and exit with a
failure code. The add-on watchdog restarts velserv, which reopens the device --
exactly what the manual restart did, but within seconds and with a log line.

The test uses a pty as the Velbus interface. Closing the master side makes every
read() on the slave fail with EIO, which is what a vanished ttyACM does.

Run:  python3 tests/test_serial_gone.py
Exit code 0 = velserv exits promptly with an error (fixed),
          1 = velserv keeps running / burns CPU (bug present).
"""

import os
import pty
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "velserv", "velserv.c")
PORT = 47884  # unlikely to clash with a real velserv on 3788
CLK = os.sysconf("SC_CLK_TCK")


def compile_velserv(dst: str) -> None:
    subprocess.run(
        ["gcc", "-Wall", "-o", dst, SRC, "-lpthread"],
        check=True,
        capture_output=True,
    )


def cpu_seconds(pid: int) -> float:
    """utime + stime of a process, in seconds (all threads)."""
    with open(f"/proc/{pid}/stat") as f:
        fields = f.read().rsplit(")", 1)[1].split()
    # fields[0] is state (field 3); utime/stime are fields 14/15 -> idx 11/12
    return (int(fields[11]) + int(fields[12])) / CLK


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    with tempfile.TemporaryDirectory() as tmp:
        binary = os.path.join(tmp, "velserv")
        compile_velserv(binary)

        master, slave = pty.openpty()
        slave_name = os.ttyname(slave)
        os.close(slave)

        proc = subprocess.Popen(
            [binary, "-f", "-p", str(PORT), "-d", slave_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            time.sleep(2.0)  # velserv sleeps 1 s before opening the device
            if proc.poll() is not None:
                print(f"velserv exited early (rc={proc.returncode})")
                return 2

            # Healthy baseline: an idle velserv blocks in read()/select().
            c0 = cpu_seconds(proc.pid)
            time.sleep(1.0)
            idle = cpu_seconds(proc.pid) - c0
            results.append(
                (
                    "idle velserv uses (almost) no CPU",
                    idle < 0.1,
                    f"{idle:.2f} s CPU in 1 s",
                )
            )

            # The device goes away.
            os.close(master)

            deadline = time.time() + 5.0
            while time.time() < deadline and proc.poll() is None:
                time.sleep(0.1)

            if proc.poll() is None:
                c0 = cpu_seconds(proc.pid)
                time.sleep(1.0)
                spin = cpu_seconds(proc.pid) - c0
                results.append(
                    (
                        "velserv exits within 5 s after the device is gone",
                        False,
                        f"still running, burning {spin:.2f} s CPU per second",
                    )
                )
            else:
                rc = proc.returncode
                err = proc.stderr.read().decode(errors="replace").strip()
                results.append(
                    (
                        "velserv exits within 5 s after the device is gone",
                        True,
                        f"exited with rc={rc}",
                    )
                )
                results.append(
                    (
                        "exit code signals failure (so the watchdog restarts it)",
                        rc != 0,
                        f"rc={rc}",
                    )
                )
                results.append(
                    (
                        "the reason is logged on stderr",
                        "serial" in err.lower(),
                        repr(err[-160:]) if err else "nothing on stderr",
                    )
                )
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    failed = 0
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        failed += not ok
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
