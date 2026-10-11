#!/usr/bin/env python3
"""Verify upload policy with isolated torrent engines and loopback peers."""

import base64
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import platform
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def bencode(value):
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    return b"d" + b"".join(bencode(k) + bencode(v) for k, v in sorted(value.items())) + b"e"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(check, description, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError(description)


class Engine:
    def __init__(self, executable, root, transport, encryption, uploads=True,
                 restore=False, interface="127.0.0.1"):
        self.peer_address = "::1" if interface == "::1" else "127.0.0.1"
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.rpc_port, self.peer_port = free_port(), free_port()
        self.token = "token:" + secrets.token_hex(16)
        self.log = (root / "engine.log").open("wb")
        args = [str(executable), "--enable-rpc=true", "--rpc-listen-all=false",
                f"--rpc-listen-port={self.rpc_port}", f"--rpc-secret={self.token[6:]}",
                f"--listen-port={self.peer_port}", f"--bt-interface={interface}",
                f"--bt-transport={transport}", f"--bt-encryption={encryption}",
                f"--bt-peer-id-prefix=-HB{secrets.token_hex(2)}-",
                f"--bt-enable-uploads={str(uploads).lower()}",
                f"--state-dir={root / 'state'}", f"--save-session={root / 'session'}",
                "--save-session-interval=0", "--force-save=true", "--seed-ratio=0",
                "--enable-dht=false", "--bt-enable-lpd=false", "--bt-port-mapping=false",
                "--enable-peer-exchange=false", "--pause=false",
                "--summary-interval=0", "--show-console-readout=false"]
        if restore:
            args.append(f"--input-file={root / 'session'}")
        self.process = subprocess.Popen(args, stdout=self.log, stderr=self.log)
        try:
            wait_for(self.ready, "RPC did not start")
        except BaseException:
            self.close()
            raise

    def ready(self):
        if self.process.poll() is not None:
            raise RuntimeError(f"Engine exited: {self.root / 'engine.log'}")
        try:
            return self.rpc("aria2.getVersion")
        except OSError:
            return False

    def rpc(self, method, *params):
        data = json.dumps({"jsonrpc": "2.0", "id": "upload-policy", "method": method,
                           "params": [self.token, *params]}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{self.rpc_port}/jsonrpc", data,
                                         {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.load(response)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result["result"]

    def add(self, torrent):
        return self.rpc("aria2.addTorrent", base64.b64encode(torrent).decode(), [],
                        {"dir": str(self.root), "seed-ratio": "0", "pause": "false"})

    def status(self, gid):
        return self.rpc("aria2.tellStatus", gid)

    def connect(self, gid, other):
        address = f"[{other.peer_address}]" if ":" in other.peer_address else other.peer_address
        self.rpc("aria2.addBtPeers", gid, [f"{address}:{other.peer_port}"])

    def close(self):
        if self.process.poll() is None:
            try:
                self.rpc("aria2.forceShutdown")
            except (OSError, RuntimeError):
                self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.log.close()


def validate(executable, transport, encryption):
    piece = 65536
    payload = secrets.token_bytes(piece * 8)
    info = {b"name": b"payload.bin", b"length": len(payload), b"private": 1,
            b"piece length": piece,
            b"pieces": b"".join(hashlib.sha1(payload[i:i + piece]).digest()
                                  for i in range(0, len(payload), piece))}
    torrent = bencode({b"info": info})
    with tempfile.TemporaryDirectory(prefix="harbor-upload-policy-") as folder, ExitStack() as stack:
        root = Path(folder)

        def start(name, uploads=True, restore=False):
            # Different loopback families let the source and receiver connect
            # at the same time without bypassing the engine's one-peer-per-IP rule.
            interface = {"source": "::1", "protected": "127.0.0.1,::1"}.get(name, "127.0.0.1")
            engine = Engine(executable, root / name, transport, encryption, uploads, restore, interface)
            stack.callback(engine.close)
            return engine

        for name, data in (("protected", payload[:piece * 2] + bytes(piece * 6)),
                           ("source", payload)):
            (root / name).mkdir()
            (root / name / "payload.bin").write_bytes(data)
        protected, source = start("protected"), start("source")
        gid, source_gid = protected.add(torrent), source.add(torrent)
        wait_for(lambda: int(protected.status(gid)["completedLength"]) == piece * 2,
                 "Existing partial payload did not verify")
        wait_for(lambda: source.status(source_gid).get("seeder") == "true", "Source not ready")
        baseline = start("baseline")
        baseline_gid = baseline.add(torrent)
        wait_for(lambda: baseline.status(baseline_gid).get("bittorrent", {}).get("state") == "downloading",
                 "Baseline receiver not ready")
        baseline.connect(baseline_gid, protected)
        wait_for(lambda: int(baseline.status(baseline_gid)["completedLength"]) > 0,
                 "Baseline peer could not download from protected torrent")
        assert int(protected.status(gid)["completedLength"]) < len(payload), "Baseline was not downloading"
        protected.rpc("aria2.changeGlobalOption", {"bt-enable-uploads": "false"})
        wait_for(lambda: not protected.rpc("aria2.getPeers", gid),
                 "Live policy change did not disconnect the existing peer")
        baseline.close()
        # Allow bytes sent before the policy change to leave the old connection.
        time.sleep(0.5)
        upload_at_stop = int(protected.status(gid)["uploadLength"])

        def blocked_receiver(name, target):
            receiver = start(name)
            receiver_gid = receiver.add(torrent)
            wait_for(lambda: receiver.status(receiver_gid).get("bittorrent", {}).get("state") == "downloading",
                     "Receiver not ready")
            receiver.connect(receiver_gid, target)
            peer = wait_for(lambda: next((peer for peer in receiver.rpc("aria2.getPeers", receiver_gid)
                                          if peer["state"] == "connected"), None),
                            "Blocked receiver never completed its handshake")
            assert peer["transport"] == transport, "Peer used the wrong transport"
            if encryption == "required":
                assert peer["encryption"] != "plain", "Peer used an unencrypted connection"
            else:
                assert peer["encryption"] == "plain", "Peer used an encrypted connection"
            time.sleep(1)
            assert int(receiver.status(receiver_gid)["completedLength"]) == 0, "File bytes reached blocked receiver"
            return receiver, receiver_gid

        receiver, receiver_gid = blocked_receiver("during-download", protected)
        assert int(protected.status(gid)["uploadLength"]) == upload_at_stop, "Uploaded after live stop"
        receiver.close()
        protected.rpc("aria2.saveSession")
        protected.close()
        protected = start("protected", uploads=False, restore=True)
        wait_for(lambda: int(protected.status(gid)["completedLength"]) == piece * 2,
                 "Partial torrent did not restore")
        # Counters restart with the engine; compare within this restored session.
        upload_at_stop = int(protected.status(gid)["uploadLength"])
        receiver, receiver_gid = blocked_receiver("after-partial-restart", protected)
        source.rpc("aria2.addBtPeers", source_gid, [f"[::1]:{protected.peer_port}"])
        wait_for(lambda: int(protected.status(gid)["completedLength"]) == len(payload),
                 "Download did not finish with uploads blocked")
        assert (root / "protected/payload.bin").read_bytes() == payload, "Downloaded payload is wrong"
        assert int(protected.status(gid)["uploadLength"]) == upload_at_stop, "Uploaded while downloading"
        assert int(receiver.status(receiver_gid)["completedLength"]) == 0, "Uploaded after completion"
        receiver.close()
        protected.rpc("aria2.saveSession")
        protected.close()
        protected = start("protected", uploads=False, restore=True)
        wait_for(lambda: protected.status(gid).get("seeder") == "true", "Completed torrent did not restore")
        upload_after_restart = int(protected.status(gid)["uploadLength"])
        receiver, receiver_gid = blocked_receiver("after-restart", protected)
        assert int(protected.status(gid)["uploadLength"]) == upload_after_restart, "Uploaded after restart"
        receiver.close()
        protected.rpc("aria2.changeGlobalOption", {"bt-enable-uploads": "true"})
        receiver = start("re-enabled")
        receiver_gid = receiver.add(torrent)
        wait_for(lambda: receiver.status(receiver_gid).get("bittorrent", {}).get("state") == "downloading",
                 "Receiver not ready after re-enable")
        receiver.connect(receiver_gid, protected)
        wait_for(lambda: int(receiver.status(receiver_gid)["completedLength"]) > 0,
                 "Uploads did not resume when enabled")
        print(f"PASS {transport}/{encryption}: live stop, partial restart, download completion, "
              "completed restart, and re-enable", flush=True)


if __name__ == "__main__":
    default = Path(__file__).resolve().parents[1] / "Vendor/TorrentRuntime" / platform.machine() / "bin/aria2-next"
    executable = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else default
    for transport, encryption in (("tcp", "disabled"), ("tcp", "required"), ("utp", "required")):
        validate(executable, transport, encryption)
