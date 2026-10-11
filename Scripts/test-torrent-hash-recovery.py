#!/usr/bin/env python3
"""Check the bundled engine's progress after a rejected piece, using loopback only."""

import base64
import hashlib
import http.server
import json
from pathlib import Path
import platform
import re
import secrets
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request


def bencode(value):
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    return b"d" + b"".join(
        bencode(key) + bencode(item) for key, item in sorted(value.items())
    ) + b"e"


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def receive(connection, count):
    data = b""
    while len(data) < count:
        chunk = connection.recv(count - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


class CorruptPeer:
    """Advertise one piece and return invalid bytes for every requested block."""

    def __init__(self, info_hash):
        self.info_hash = info_hash
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self.accept, daemon=True)
        self.thread.start()

    def accept(self):
        while True:
            try:
                connection, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self.serve, args=(connection,), daemon=True).start()

    def serve(self, connection):
        try:
            with connection:
                connection.settimeout(15)
                handshake = receive(connection, 68)
                if handshake[:20] != b"\x13BitTorrent protocol" or handshake[28:48] != self.info_hash:
                    return  # Let the engine retry an encrypted handshake in plain text.
                connection.sendall(
                    b"\x13BitTorrent protocol" + bytes(8) + self.info_hash
                    + b"-HB0001-" + secrets.token_bytes(12)
                )
                connection.sendall(struct.pack("!I", 2) + b"\x05\x80")
                connection.sendall(struct.pack("!I", 1) + b"\x01")
                while True:
                    size = struct.unpack("!I", receive(connection, 4))[0]
                    message = receive(connection, size)
                    if message and message[0] == 6:
                        piece, offset, length = struct.unpack("!III", message[1:])
                        connection.sendall(
                            struct.pack("!I", 9 + length) + b"\x07"
                            + struct.pack("!II", piece, offset) + bytes(length)
                        )
        except (OSError, EOFError):
            pass  # The engine disconnects the peer after rejecting its piece.


def validate(engine):
    piece_length = 65536
    payload = secrets.token_bytes(piece_length * 8)
    info = {
        b"name": b"payload.bin", b"length": len(payload), b"private": 1,
        b"piece length": piece_length,
        b"pieces": b"".join(
            hashlib.sha1(payload[start:start + piece_length]).digest()
            for start in range(0, len(payload), piece_length)
        ),
    }
    info_hash = hashlib.sha1(bencode(info)).digest()

    class GoodSeed(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            match = re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers.get("Range", ""))
            first, last = map(int, match.groups()) if match else (0, len(payload) - 1)
            body = payload[first:last + 1]
            self.send_response(206 if match else 200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Range", f"bytes {first}-{last}/{len(payload)}")
            self.end_headers()
            self.wfile.write(body)

    port, secret = free_port(), secrets.token_hex(16)

    def rpc(method, *params):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/jsonrpc",
            data=json.dumps({"jsonrpc": "2.0", "id": "recovery-test",
                             "method": method, "params": ["token:" + secret, *params]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.load(response)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result["result"]

    with tempfile.TemporaryDirectory(prefix="harbor-hash-recovery-") as directory:
        root = Path(directory)
        with (root / "engine.log").open("w+b") as log:
            process = subprocess.Popen([
                str(engine), "--no-conf=true", "--enable-rpc=true",
                "--rpc-listen-all=false", f"--rpc-listen-port={port}",
                f"--rpc-secret={secret}", f"--state-dir={root / 'state'}",
                f"--dir={root}", f"--listen-port={free_port()}",
                "--enable-dht=false", "--bt-enable-lpd=false",
                "--bt-port-mapping=false", "--enable-peer-exchange=false",
                "--disable-ipv6=true", "--bt-interface=127.0.0.1",
                "--seed-time=0", "--summary-interval=0", "--show-console-readout=false",
            ], stdout=log, stderr=log)
            seed = peer = None
            try:
                for _ in range(100):
                    try:
                        rpc("aria2.getVersion")
                        break
                    except OSError:
                        if process.poll() is not None:
                            raise RuntimeError("Torrent engine exited during startup")
                        time.sleep(0.1)
                else:
                    raise TimeoutError("Torrent RPC did not start")
                gid = rpc("aria2.addTorrent", base64.b64encode(bencode({b"info": info})).decode(),
                          [], {"dir": str(root), "seed-time": "0"})
                # Wait for the native handle before adding the test peer.
                for _ in range(100):
                    status = rpc("aria2.tellStatus", gid)
                    if status.get("bittorrent", {}).get("state") == "downloading":
                        break
                    time.sleep(0.1)
                peer = CorruptPeer(info_hash)
                rpc("aria2.addBtPeers", gid, [f"127.0.0.1:{peer.port}"])
                for _ in range(100):
                    status = rpc("aria2.tellStatus", gid)
                    if int(status.get("bittorrent", {}).get("failedLength", "0")):
                        break
                    time.sleep(0.2)
                else:
                    raise AssertionError("The corrupt peer did not cause a hash failure")
                print("Rejected corrupt piece; now download from the valid seed.", flush=True)
                seed = http.server.ThreadingHTTPServer(("127.0.0.1", 0), GoodSeed)
                threading.Thread(target=seed.serve_forever, daemon=True).start()
                rpc("aria2.replaceBtWebSeeds", gid,
                    [f"http://127.0.0.1:{seed.server_port}/payload.bin"])
                for _ in range(100):
                    status = rpc("aria2.tellStatus", gid)
                    if status["status"] == "complete":
                        break
                    time.sleep(0.2)
                correct = (root / "payload.bin").read_bytes() == payload
                assert correct, "Recovered file does not match the original payload"
                assert status["status"] == "complete", (
                    f"File is correct, but status is {status['status']} at "
                    f"{status['completedLength']}/{status['totalLength']} bytes"
                )
                assert int(status["completedLength"]) == len(payload)
                print("PASS: correct file, full progress, and completion without restart.")
            except BaseException:
                log.flush()
                log.seek(0)
                print(log.read().decode(errors="replace")[-4000:], file=sys.stderr)
                raise
            finally:
                if seed:
                    seed.shutdown()
                    seed.server_close()
                if peer:
                    peer.listener.close()
                if process.poll() is None:
                    try:
                        rpc("aria2.forceShutdown")
                    except (OSError, RuntimeError):
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


if __name__ == "__main__":
    default = Path(__file__).resolve().parents[1] / "Vendor/TorrentRuntime" / platform.machine() / "bin/aria2-next"
    validate(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else default)
