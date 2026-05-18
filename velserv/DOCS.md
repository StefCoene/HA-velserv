# VelServ

TCP-to-Velbus gateway that shares a single USB Velbus interface over TCP with multiple clients simultaneously (VelbusLink, Home Assistant, OpenHAB, ...).

Based on [velserv by jeroends](https://github.com/jeroends/velserv).

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `device` | `/dev/ttyACM0` | Path to the Velbus USB interface |
| `port` | `3788` | TCP port the gateway listens on |
| `verbose_level` | `0` | Logging verbosity (0 = off, 4 = socket debug, 6 = full debug) |

## Finding the correct device path

At every startup the addon logs the available serial devices:

```
INFO: Available serial devices:
INFO:   /dev/serial/by-id/usb-Velleman_Projects_VMB1USB_Velbus_USB_interface-if00 -> /dev/ttyACM1
```

Use the `/dev/serial/by-id/` path instead of `/dev/ttyACM*` — this path stays stable when the device is reconnected or the system reboots.

## Connecting Home Assistant

Configure the Velbus integration with:
- **Host:** IP address of your Home Assistant machine
- **Port:** the port configured above (default `3788`)
