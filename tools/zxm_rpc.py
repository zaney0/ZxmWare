#!/usr/bin/env python3
"""Shared Flipper protobuf-RPC helpers for ZxmWare tooling.

The plain text CLI corrupts binary payloads, so all file transfers go over the
protobuf RPC (same protocol qFlipper uses). Requires: pyserial, flipperzero-protobuf.
"""
import glob
import hashlib
import os
import time

try:
    import serial
    from flipperzero_protobuf.flipper_proto import FlipperProto
    HAVE_RPC = True
except Exception:  # pragma: no cover
    HAVE_RPC = False


def find_port():
    """Return the first connected Flipper serial port, or None."""
    ports = sorted(glob.glob("/dev/cu.usbmodemflip_*") + glob.glob("/dev/ttyACM*"))
    return ports[0] if ports else None


def _md5(data):
    return hashlib.md5(data).hexdigest()


class Flipper:
    """Minimal RPC wrapper: connect, mkdir -p, verified file push."""

    def __init__(self, port=None):
        if not HAVE_RPC:
            raise RuntimeError("pyserial + flipperzero-protobuf required")
        self.port = port or find_port()
        if not self.port:
            raise RuntimeError("No Flipper connected")
        self.ser = None
        self.proto = None

    def __enter__(self):
        self.ser = serial.Serial(self.port, timeout=1)
        time.sleep(0.3)
        self.proto = FlipperProto(serial_port=self.ser, debug=0)
        self.proto.start_rpc_session()
        return self

    def __exit__(self, *a):
        try:
            self.ser.close()
        except Exception:
            pass

    def mkdir_p(self, path):
        parts, cur = [p for p in path.strip("/").split("/") if p], ""
        for p in parts:
            cur += "/" + p
            try:
                self.proto.rpc_mkdir(cur)
            except Exception:
                pass  # already exists

    def push_verified(self, local, dest, retries=3):
        """Push a local file to `dest` on the SD, md5-verified, with retries."""
        with open(local, "rb") as f:
            data = f.read()
        want = _md5(data)
        self.mkdir_p(os.path.dirname(dest))
        for _ in range(retries):
            self.proto.rpc_write(dest, data)
            if self.proto.rpc_md5sum(dest) == want:
                return True
            try:
                self.proto.send_cmd("storage_delete_request", path=dest)
            except Exception:
                pass
        return False

    def md5(self, path):
        try:
            return self.proto.rpc_md5sum(path)
        except Exception:
            return None
