# Tests

## `test_framing.py`

Regression test for the TCP reassembly / framing bug in `velserv.c`'s `server()`
rebroadcast path.

velserv shares one Velbus interface with several TCP clients by rebroadcasting
every frame from one client to all others. TCP delivers a byte stream with no
message boundaries, so a single `recv()` may contain a partial frame or several
frames. The buggy version assumed one `recv()` == one frame and, when a frame
arrived split across two reads, padded the missing bytes with zeros and forwarded
a corrupt message. Downstream (Home Assistant / velbus-aio) this showed up as:

```
ERROR [velbusaio.raw_message] Could not parse the message b'0ffb3c07e62fa02600000000000f'. Truncating invalid data.
```

The bug is timing-dependent: it only triggers when `recv()` happens to split a
frame, which is more likely on a busy bus. This test reproduces it
deterministically — no Velbus hardware and no reliance on bus timing — by
connecting two TCP clients and having one send frames in deliberately fragmented
writes while the other checks the rebroadcast arrives intact.

### Running

```bash
python3 tests/test_framing.py
```

Requires `gcc` (the test compiles `velserv/velserv.c` itself) and Python 3.9+.
It starts velserv in server-only mode (`-s`) on port 47880, so no serial device
is needed. Exit code 0 means all scenarios passed.

## `test_serial_gone.py`

velserv must exit -- not spin -- when the serial device disappears. A pty stands
in for the VMB1USB; closing its master makes every `read()` fail with `EIO`,
which is what a vanished `ttyACM` does. Before 1.7.6 `com_to_sock()` ignored
`read() <= 0` and looped at one full core while passing no traffic (seen in
production for 2.5 days, 11/2026). The fix exits with a failure code so the
add-on watchdog restarts velserv and the device is reopened.

## `test_client_leak.py`

100 000 connect/reset cycles against a server-only velserv, comparing RSS before
and after. Before 1.7.6 every accepted connection leaked its peer-address string
(~24-28 bytes) unless it hung up cleanly with `verbose > 3`. Takes ~1.5 min.
Clients reset (`SO_LINGER 0`) rather than close, so no `TIME_WAIT` sockets are
left on ports the other tests need.

## `test_length_check.py`

A Velbus frame has at most 8 data bytes, but the length nibble allows 15. Before
1.7.7 velserv trusted it: on the serial side a corrupt header made it wait for
up to 20 bytes and swallow the next good frame; in `server()` a "frame" with
9..15 data bytes was forwarded to every client, and a bad header followed by a
good frame in one `recv()` left the good frame stuck. Covers both paths, the
truncated-frame edge case (the rejected length byte is the next frame's `0x0F`),
and that every legal length 0..8 still passes. Rule taken from the ESP32 gateway
in github.com/MDAR/velbus_AI_commisioning.

Note: the fixed test ports (47880-47887) sit inside this machine's local port
range (44620-48715). If an outgoing connection happens to use one as its
source port, `bind()` fails and that test reports "connection refused" /
"exited early" -- rerun, or check with `ss -tanp | grep :<port>`.
