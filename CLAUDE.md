# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

This is a **Home Assistant add-on** called VelServ — a TCP-to-Velbus gateway. It lets multiple TCP clients (VelbusLink, Home Assistant Velbus integration, OpenHAB, …) share a single USB Velbus interface simultaneously.

The core is `velserv/velserv.c`, a C program compiled statically during the Docker build. The add-on wraps it with a bashio entrypoint script (`velserv/run.sh`).

## Building

The add-on is built by Home Assistant's add-on build system using `build.yaml`. To build locally for testing:

```bash
docker build --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest -t velserv-test velserv/
```

To compile just the C binary (for quick iteration):

```bash
cd velserv
gcc -o velserv velserv.c -lpthread
```

## Architecture

```
config.yaml      ← HA add-on manifest (version, options schema, port mapping)
build.yaml       ← Multi-arch base images (aarch64, amd64, armhf, armv7, i386)
repository.yaml  ← HA add-on repository metadata
velserv/
  Dockerfile     ← Two-stage build: Alpine+musl static compile → HA base image
  run.sh         ← bashio entrypoint; reads config, validates device, launches velserv
  velserv.c      ← The gateway binary (upstream: jeroends/velserv)
  config.yaml    ← Add-on options: device path, verbose_level (0–6)
  DOCS.md        ← End-user documentation shown in the HA UI
```

### How velserv works

The binary runs in combined **server + client** mode simultaneously:

- **Server thread** (`server()`): listens on TCP port 3788, accepts multiple clients, and broadcasts valid Velbus frames between all connected sockets.
- **Client threads** (`sock_to_com()`, `com_to_sock()`): open the serial device and connect to the server's own loopback (127.0.0.1:3788), bridging serial ↔ TCP.

The Velbus frame format: starts with `0x0F`, ends with `0x04`. Frame length is derived from byte 3 (`(buf[3] & 0xF) + 5`).

The serial port is configured at **38400 baud** (`BAUDRATE B38400`) with RTS set and DTR cleared (required by the Velbus USB interface).

### run.sh → velserv invocation

`run.sh` always passes `-f` (foreground) and `-p 3788` (fixed port). The `verbose_level` config option adds that many `-v` flags. Note that `-v` also implicitly enables foreground mode in the C code.

### Versioning

The add-on version lives in `velserv/config.yaml` (`version:`). The `VERSION` string in `velserv.c` is the upstream version and does not need to match.

## Config options

| Option | Type | Default | Notes |
|---|---|---|---|
| `device` | `str` | `/dev/ttyACM0` | Path to Velbus USB interface |
| `verbose_level` | `int(0,6)` | `0` | Each level adds one `-v`; levels 2–3 log serial↔socket frames, 4–6 log server socket activity |

Always recommend `/dev/serial/by-id/` paths over `/dev/ttyACM*` — they stay stable across reboots and reconnects. The startup log lists available devices for discovery.
