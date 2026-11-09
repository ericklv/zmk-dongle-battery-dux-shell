#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial==3.5", "protobuf==7.36.2"]
# ///
"""Print the split halves' battery levels as one JSON line, read from the dongle over Studio RPC.

    ./zmk_battery.py                   {"ok": true, "sides": [{"label": "L", "level": 87, ...}]}
    ./zmk_battery.py --labels 1=R,2=L  swap the names if the halves come out reversed

Needs CONFIG_ZMK_BATTERY_HISTORY on every board and CONFIG_ZMK_BATTERY_HISTORY_STUDIO_RPC on
the dongle. Levels are the latest entry each half recorded, not a live reading.
"""
import argparse, glob, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "studio_pb"))
import studio_pb2 as st  # noqa: E402

SOF, ESC, EOF = 0xAB, 0xAC, 0xAD
SUBSYSTEM = "zmk__battery_history"
GET_HISTORY = b"\x0a\x00"  # zmk.battery_history.Request{get_history: {}}
PORT_GLOB = "/dev/serial/by-id/usb-ZMK_Project_*"


def frame(data):
    out = bytearray([SOF])
    for b in data:
        if b in (SOF, ESC, EOF):
            out.append(ESC)
        out.append(b)
    out.append(EOF)
    return bytes(out)


class Deframer:
    def __init__(self):
        self.buf, self.inside, self.esc = bytearray(), False, False

    def feed(self, data):
        msgs = []
        for b in data:
            if not self.inside:
                if b == SOF:
                    self.inside, self.buf = True, bytearray()
            elif self.esc:
                self.buf.append(b)
                self.esc = False
            elif b == ESC:
                self.esc = True
            elif b == EOF:
                msgs.append(bytes(self.buf))
                self.inside = False
            else:
                self.buf.append(b)
        return msgs


def pb_fields(data):
    """Decode a protobuf message into {field: [values]}, enough for the battery history payloads."""
    out, i = {}, 0

    def varint():
        nonlocal i
        shift = val = 0
        while True:
            b = data[i]
            i += 1
            val |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                return val

    while i < len(data):
        key = varint()
        field, wire = key >> 3, key & 7
        if wire == 0:
            val = varint()
        elif wire == 2:
            n = varint()
            val, i = data[i:i + n], i + n
        elif wire == 5:
            val, i = data[i:i + 4], i + 4
        elif wire == 1:
            val, i = data[i:i + 8], i + 8
        else:
            raise ValueError(f"unsupported wire type {wire}")
        out.setdefault(field, []).append(val)
    return out


class Fail(Exception):
    pass


class Client:
    def __init__(self, port):
        self.port, self.d, self.rid, self.notes = port, Deframer(), 0, []

    def pump(self, until):
        for raw in self.d.feed(self.port.read(4096)):
            res = st.Response()
            res.ParseFromString(raw)
            if res.WhichOneof("type") == "notification":
                n = res.notification
                if n.WhichOneof("subsystem") == "custom":
                    self.notes.append(n.custom.custom_notification)
            elif until(res.request_response):
                return res.request_response
        return None

    def call(self, fill, timeout=5.0):
        self.rid += 1
        req = st.Request(request_id=self.rid)
        fill(req)
        self.port.write(frame(req.SerializeToString()))
        end = time.time() + timeout
        while time.time() < end:
            rr = self.pump(lambda rr: rr.request_id == self.rid)
            if rr is not None:
                if rr.WhichOneof("subsystem") == "meta":
                    raise Fail(f"the dongle returned an error: {str(rr.meta).strip()}")
                return rr
        raise Fail(f"no reply to request {self.rid}")


def read_levels(port, c=None, quiet=1.5, timeout=8.0):
    c = c or Client(port)
    subs = c.call(lambda r: r.custom.list_custom_subsystems.SetInParent())
    index = next((s.index for s in subs.custom.list_custom_subsystems.subsystems
                  if s.identifier == SUBSYSTEM), None)
    if index is None:
        raise Fail("battery history is not enabled in the dongle firmware")

    def fill(r):
        r.custom.call.subsystem_index = index
        r.custom.call.payload = GET_HISTORY
    # Entries stream in as notifications, some may arrive before the call's own reply
    seen = len(c.notes)
    c.call(fill)

    latest, done, last_seen, end = {}, set(), time.time(), time.time() + timeout
    while time.time() < end and time.time() - last_seen < quiet:
        c.pump(lambda rr: False)
        fresh, seen = c.notes[seen:], len(c.notes)
        for note in fresh:
            if note.subsystem_index != index:
                continue
            last_seen = time.time()
            hist = pb_fields(pb_fields(note.payload).get(1, [b""])[0])
            source = hist.get(1, [0])[0]
            entry = pb_fields(hist[2][0]) if 2 in hist else {}
            if entry:
                pos = hist.get(5, [0])[0]
                if source not in latest or pos >= latest[source]["index"]:
                    latest[source] = {"index": pos, "level": entry.get(2, [0])[0],
                                      "timestamp": entry.get(1, [0])[0]}
            if hist.get(3, [0])[0]:
                done.add(source)
    return latest


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help=f"serial port, by default every match of {PORT_GLOB}")
    ap.add_argument("--labels", default="1=L,2=R", help="source id to name, 0 is the dongle")
    ap.add_argument("--device", help="USB product name to use, as lsusb shows it; by default the first that answers")
    ap.add_argument("--with-dongle", action="store_true", help="also report source 0")
    ap.add_argument("--attempts", type=int, default=5, help="requests before giving up on a half")
    args = ap.parse_args()
    labels = dict(p.split("=", 1) for p in args.labels.split(","))

    import serial
    from serial.tools import list_ports
    result = {"ok": False, "name": None, "devices": [], "sides": [], "error": None, "time": int(time.time())}
    ports = [args.port] if args.port else sorted(glob.glob(PORT_GLOB))
    products = {p.device: p.product for p in list_ports.comports()}
    product = lambda path: products.get(os.path.realpath(path)) or os.path.basename(path)
    result["devices"] = sorted({product(p) for p in ports})
    if args.device:
        ports = [p for p in ports if product(p) == args.device]
    try:
        if not ports:
            raise Fail(f"{args.device} not connected" if args.device else "dongle not connected")
        latest, errors = None, []
        # Several ZMK boards may be plugged in: use the first one that has battery history
        for path in ports:
            try:
                with serial.Serial(path, timeout=0.2, exclusive=True) as port:
                    client = Client(port)
                    latest = {}
                    # The dongle drops some relayed entries, so ask again until every half answered
                    for _ in range(args.attempts):
                        for source, e in read_levels(port, client).items():
                            if source not in latest or e["index"] > latest[source]["index"]:
                                latest[source] = e
                        if all(int(s) in latest for s in labels):
                            break
            except (Fail, serial.SerialException, OSError) as e:
                errors.append(f"{os.path.basename(path)}: {e}")
                latest = None
                continue
            # USB product string, the same name lsusb shows
            result["name"] = product(path)
            break
        if latest is None:
            raise Fail("; ".join(errors))
        for source in sorted(latest):
            if source == 0 and not args.with_dongle:
                continue
            e = latest[source]
            result["sides"].append({"source": source, "label": labels.get(str(source), str(source)),
                                    "level": e["level"], "timestamp": e["timestamp"]})
        result["ok"] = True
    except (Fail, serial.SerialException, OSError) as e:
        result["error"] = str(e)
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
