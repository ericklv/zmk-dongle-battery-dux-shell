# ZMK Dongle Battery for Dux Shell

DankBar widget that shows the battery level of each half of a ZMK split keyboard driven by a USB dongle. It reads the levels from the dongle over ZMK Studio RPC, so it works without a Bluetooth link between the host and the keyboard.

The bar shows `L 87% R 92%`. Clicking it opens a popout with the outline of each half and its level. A half at 20% or lower turns red.

![Popout and bar pill](assets/screenshot.png)

## How it works

- `zmk_battery.py` opens the dongle's USB CDC ACM port (`/dev/serial/by-id/usb-ZMK_Project_*`) and speaks the Studio RPC framing.
- It finds the `zmk__battery_history` custom subsystem and sends `get_history`.
- Each half answers through the dongle with `BatteryHistoryNotification` messages. The script keeps the newest entry per source.
- The dongle sometimes drops relayed entries. The script repeats the request, up to 5 times by default, until every expected half has answered.
- Output is one JSON line on stdout. The widget runs the script every 10 minutes, when the popout opens and from the refresh button.

Levels are the latest entry each half recorded, not a live reading. How often the halves record depends on `CONFIG_ZMK_BATTERY_HISTORY_INTERVAL_MINUTES` and `CONFIG_ZMK_BATTERY_HISTORY_LEVEL_THRESHOLD`.

## Requirements

### Firmware

- **ZMK fork:** [cormoran's ZMK fork](https://github.com/cormoran/zmk) with the custom Studio protocol, for example the `main+dya` revision.
- **Module:** [zmk-module-battery-history](https://github.com/cormoran/zmk-module-battery-history).
- **Studio:** a dongle central with ZMK Studio over USB (`studio-rpc-usb-uart` snippet).
- **Kconfig:**

  | Board | Options |
  | --- | --- |
  | Dongle | `CONFIG_ZMK_STUDIO=y`, `CONFIG_ZMK_BATTERY_HISTORY=y`, `CONFIG_ZMK_BATTERY_HISTORY_STUDIO_RPC=y`, `CONFIG_ZMK_SPLIT_RELAY_EVENT=y` |
  | Each half | `CONFIG_ZMK_BATTERY_HISTORY=y`, `CONFIG_ZMK_SPLIT_RELAY_EVENT=y` |

  With `CONFIG_ZMK_STUDIO_LOCKING=y` the dongle must be unlocked before it answers.

### Host

- Dux Shell with plugin support.
- Python 3.10 or newer as `python3`. The script uses only the standard library and encodes the few Studio messages it needs by hand.
- Read and write access to `/dev/ttyACM*`. On Arch this means membership in the `uucp` group.

## Installation

```sh
git clone https://github.com/ericklv/zmk-dongle-battery-dux-shell \
  ~/.config/DankMaterialShell/plugins/zmkDongleStats
```

Then enable **ZMK Dongle Stats** in the plugin settings and add it to the bar.

## Command line

```sh
python3 zmk_battery.py
```

```json
{"ok": true, "name": "Eyelash Corne", "devices": ["Eyelash Corne"], "sides": [{"source": 1, "label": "L", "level": 87, "timestamp": 1}, {"source": 2, "label": "R", "level": 92, "timestamp": 1}], "error": null, "time": 1794263137}
```

| Option | Default | Description |
| --- | --- | --- |
| `--port PATH` | every `usb-ZMK_Project_*` port | Serial port to use. |
| `--device NAME` | first device that answers | USB product name, as `lsusb` shows it. |
| `--labels MAP` | `1=L,2=R` | Name for each source id. Swap it if the halves come out reversed. Source 0 is the dongle. |
| `--attempts N` | `5` | Requests before giving up on a missing half. |
| `--with-dongle` | off | Also report source 0. |

The exit code is `0` on success and `1` on error. Errors are reported in the `error` field.

## Multiple keyboards

The script tries every ZMK port and uses the first one that exposes battery history. When more than one is connected, the popout shows a chip for each USB product name. The selected name is stored in the plugin data and passed as `--device`.

## Outlines

The popout outline is chosen by matching the USB product name, lowercased and stripped of non-alphanumeric characters, against these keys:

| Key | Traced from |
| --- | --- |
| `corne` | crkbd PCB |
| `sofle` | Sofle case |
| `totem` | TOTEM case |
| `lily58` | Lily58 case |

Unknown names fall back to `corne`. Every outline is tilted 5 degrees and stored as fractions of the half's width in `ZmkDongleStatsWidget.qml`. The level is drawn at the center of the largest circle that fits inside each outline.

## Limitations

- The serial port is opened exclusively. While ZMK Studio or another Studio client holds it, reads fail and the widget dims the last known levels.
- A read takes about 2 seconds. Each retry for a dropped relay entry adds about 2 more.
- Only two halves are expected by default. Use `--labels` for other layouts.

## Troubleshooting

**A half still reads low after charging.** Its latest history entry predates the charge.

- With `CONFIG_ZMK_BATTERY_SKIP_IF_USB_POWERED=y`, the default, halves record nothing on USB power. After unplugging, the next record comes within `CONFIG_ZMK_BATTERY_HISTORY_INTERVAL_MINUTES`, 5 by default.
- If it stays stale, the half may still report USB power. Power cycle it: the first record after boot is always saved.
- Build the halves with `CONFIG_ZMK_BATTERY_SKIP_IF_USB_POWERED=n` to record while charging too.

`timestamp` is seconds since the half booted. `1` means the entry was recorded at boot.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).

Message field numbers follow [zmk-studio-messages](https://github.com/cormoran/zmk-studio-messages).
