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
