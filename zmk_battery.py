#!/usr/bin/env python3
"""Print the split halves' battery levels as one JSON line, read from the dongle over Studio RPC.

    ./zmk_battery.py                   {"ok": true, "sides": [{"label": "L", "level": 87, ...}]}
    ./zmk_battery.py --labels 1=R,2=L  swap the names if the halves come out reversed

Needs CONFIG_ZMK_BATTERY_HISTORY on every board and CONFIG_ZMK_BATTERY_HISTORY_STUDIO_RPC on
the dongle. Levels are the latest entry each half recorded, not a live reading.
Standard library only (Linux, Python 3.10+).
"""
import argparse, fcntl, glob, json, os, select, sys, termios, time

SOF, ESC, EOF = 0xAB, 0xAC, 0xAD
SUBSYSTEM = "zmk__battery_history"
GET_HISTORY = b"\x0a\x00"  # zmk.battery_history.Request{get_history: {}}
PORT_GLOB = "/dev/serial/by-id/usb-ZMK_Project_*"
META_ERRORS = ["generic error", "unlock required", "rpc not found", "decode failed", "encode failed"]

# Field numbers from zmk-studio-messages (studio.proto, custom.proto, meta.proto)
REQ_ID, SUB_META, SUB_CUSTOM = 1, 2, 100
RES_REQUEST_RESPONSE, RES_NOTIFICATION = 1, 2
CUSTOM_LIST, CUSTOM_CALL = 1, 2


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


def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def pb(*fields):
    """Encode (field, value) pairs: int as varint, bytes as length-delimited; proto3 zero ints are omitted."""
    out = bytearray()
    for field, val in fields:
        if isinstance(val, int):
            if val:
                out += varint(field << 3) + varint(val)
        else:
            out += varint(field << 3 | 2) + varint(len(val)) + val
    return bytes(out)


def pb_fields(data):
    """Decode a protobuf message into {field: [values]}, enough for the Studio and battery history payloads."""
    out, i = {}, 0

    def read_varint():
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
        key = read_varint()
        field, wire = key >> 3, key & 7
        if wire == 0:
            val = read_varint()
        elif wire == 2:
            n = read_varint()
            val, i = data[i:i + n], i + n
        elif wire == 5:
            val, i = data[i:i + 4], i + 4
        elif wire == 1:
            val, i = data[i:i + 8], i + 8
        else:
            raise ValueError(f"unsupported wire type {wire}")
        out.setdefault(field, []).append(val)
    return out


def first(msg, field, default=None):
    return msg.get(field, [default])[0]


class Fail(Exception):
    pass


class Serial:
    """Raw, exclusively locked CDC ACM port."""

    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            raise Fail("port busy, close ZMK Studio or any other client")
        attrs = termios.tcgetattr(self.fd)
        attrs[0] = attrs[1] = attrs[3] = 0
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[6][termios.VMIN], attrs[6][termios.VTIME] = 0, 0
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        termios.tcflush(self.fd, termios.TCIOFLUSH)

    def read(self, timeout=0.2):
        if not select.select([self.fd], [], [], timeout)[0]:
            return b""
        try:
            return os.read(self.fd, 4096)
        except BlockingIOError:
            return b""

    def write(self, data):
        while data:
            select.select([], [self.fd], [], 1.0)
            try:
                data = data[os.write(self.fd, data):]
            except BlockingIOError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        os.close(self.fd)


class Client:
    def __init__(self, port):
        self.port, self.d, self.rid, self.notes = port, Deframer(), 0, []

    def pump(self, until):
        """Read once and buffer every notification; frames after a matching reply must not be dropped."""
        found = None
        for raw in self.d.feed(self.port.read()):
            res = pb_fields(raw)
            if RES_NOTIFICATION in res:
                custom = first(pb_fields(res[RES_NOTIFICATION][0]), SUB_CUSTOM)
                if custom is not None:
                    note = first(pb_fields(custom), 1)
                    if note is not None:
                        self.notes.append(pb_fields(note))
            elif RES_REQUEST_RESPONSE in res:
                rr = pb_fields(res[RES_REQUEST_RESPONSE][0])
                if found is None and until(rr):
                    found = rr
        return found

    def call(self, custom_request, timeout=5.0):
        self.rid += 1
        self.port.write(frame(pb((REQ_ID, self.rid), (SUB_CUSTOM, custom_request))))
        end = time.time() + timeout
        while time.time() < end:
            rr = self.pump(lambda rr: first(rr, REQ_ID, 0) == self.rid)
            if rr is not None:
                if SUB_META in rr:
                    meta = pb_fields(rr[SUB_META][0])
                    code = first(meta, 2, 0)
                    reason = META_ERRORS[code] if code < len(META_ERRORS) else f"error {code}"
                    raise Fail(f"the dongle returned an error: {reason}")
                return pb_fields(first(rr, SUB_CUSTOM, b""))
        raise Fail(f"no reply to request {self.rid}")


def read_levels(c, quiet=1.5, timeout=8.0):
    res = c.call(pb((CUSTOM_LIST, b"")))
    subsystems = [pb_fields(s) for s in pb_fields(first(res, CUSTOM_LIST, b"")).get(1, [])]
    index = next((first(s, 1, 0) for s in subsystems if first(s, 2, b"").decode() == SUBSYSTEM), None)
    if index is None:
        raise Fail("battery history is not enabled in the dongle firmware")

    # Entries stream in as notifications, some may arrive before the call's own reply
    seen = len(c.notes)
    c.call(pb((CUSTOM_CALL, pb((1, index), (2, GET_HISTORY)))))

    latest, last_seen, end = {}, time.time(), time.time() + timeout
    while time.time() < end and time.time() - last_seen < quiet:
        c.pump(lambda rr: False)
        fresh, seen = c.notes[seen:], len(c.notes)
        for note in fresh:
            if first(note, 1, 0) != index:
                continue
            last_seen = time.time()
            hist = pb_fields(first(pb_fields(first(note, 2, b"")), 1, b""))
            source = first(hist, 1, 0)
            entry = pb_fields(hist[2][0]) if 2 in hist else {}
            if entry:
                pos = first(hist, 5, 0)
                if source not in latest or pos >= latest[source]["index"]:
                    latest[source] = {"index": pos, "level": first(entry, 2, 0), "timestamp": first(entry, 1, 0)}
    return latest


def product(path):
    """USB product string of a tty, the same name lsusb shows."""
    d = os.path.realpath(f"/sys/class/tty/{os.path.basename(os.path.realpath(path))}/device")
    for _ in range(4):
        try:
            with open(os.path.join(d, "product")) as f:
                return f.read().strip()
        except OSError:
            d = os.path.dirname(d)
    return os.path.basename(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help=f"serial port, by default every match of {PORT_GLOB}")
    ap.add_argument("--labels", default="1=L,2=R", help="source id to name, 0 is the dongle")
    ap.add_argument("--device", help="USB product name to use, as lsusb shows it; by default the first that answers")
    ap.add_argument("--with-dongle", action="store_true", help="also report source 0")
    ap.add_argument("--attempts", type=int, default=5, help="requests before giving up on a half")
    args = ap.parse_args()
    labels = dict(p.split("=", 1) for p in args.labels.split(","))

    result = {"ok": False, "name": None, "devices": [], "sides": [], "error": None, "time": int(time.time())}
    ports = [args.port] if args.port else sorted(glob.glob(PORT_GLOB))
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
                with Serial(path) as port:
                    client = Client(port)
                    latest = {}
                    # The dongle drops some relayed entries, so ask again until every half answered
                    for _ in range(args.attempts):
                        for source, e in read_levels(client).items():
                            if source not in latest or e["index"] > latest[source]["index"]:
                                latest[source] = e
                        if all(int(s) in latest for s in labels):
                            break
            except (Fail, OSError, ValueError, IndexError) as e:
                errors.append(f"{os.path.basename(path)}: {e}")
                latest = None
                continue
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
    except (Fail, OSError) as e:
        result["error"] = str(e)
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
