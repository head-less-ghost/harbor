#!/usr/bin/env python3
"""Local VPN transfer and failure test. Requires Docker, Go, and vendored runtimes."""
import base64
import hashlib
import http.server
import json
import os
import pathlib
import platform
import select
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
ARCH = "arm64" if platform.machine() == "arm64" else "x86_64"
RUNTIME = ROOT / "Vendor" / "VPNRuntime" / ARCH / "bin"
PAYLOAD = bytes(range(256)) * 4096


def command(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def receive(sock, count):
    data = b""
    while len(data) < count:
        part = sock.recv(count - len(data))
        assert part, "Unexpected socket closure"
        data += part
    return data


def authenticate(event, password=None):
    sock = socket.create_connection(("127.0.0.1", event["port"]), timeout=15)
    sock.sendall(b"\x05\x01\x02")
    assert receive(sock, 2) == b"\x05\x02"
    user = event["username"].encode()
    password = (password or event["password"]).encode()
    sock.sendall(b"\x01" + bytes([len(user)]) + user + bytes([len(password)]) + password)
    reply = receive(sock, 2)
    if reply != b"\x01\x00":
        sock.close()
        raise PermissionError("SOCKS authentication rejected")
    return sock


def bencode(value):
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(map(bencode, value)) + b"e"
    return b"d" + b"".join(bencode(k) + bencode(value[k]) for k in sorted(value)) + b"e"


def torrent(url, name="payload.bin"):
    piece = 256 * 1024
    return base64.b64encode(bencode({
        "info": {"length": len(PAYLOAD), "name": name, "piece length": piece,
                 "pieces": b"".join(hashlib.sha1(PAYLOAD[i:i+piece]).digest()
                                    for i in range(0, len(PAYLOAD), piece))},
        "url-list": [url],
    })).decode()


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def check_profile(protocol, config, work):
    helper = subprocess.Popen([str(RUNTIME / "harbor-vpn")], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True)
    aria = None
    try:
        helper.stdin.write(json.dumps({"protocol": protocol, "config": config}) + "\n")
        helper.stdin.flush()
        assert select.select([helper.stdout], [], [], 50)[0], "VPN startup timed out"
        event = json.loads(helper.stdout.readline())
        assert event["event"] == "ready", event
        try:
            authenticate(event, "incorrect-password")
            raise AssertionError("Unauthenticated VPN use was allowed")
        except PermissionError:
            pass
        with authenticate(event) as sock:
            host = b"vpn.fixture"
            sock.sendall(b"\x05\x01\x00\x03" + bytes([len(host)]) + host + struct.pack("!H", 8080))
            reply = receive(sock, 4)
            assert reply[1] == 0, "Tunneled DNS/TCP connection failed"
            receive(sock, 6 if reply[3] == 1 else 18)
            sock.sendall(b"GET /payload.bin HTTP/1.0\r\nHost: vpn.fixture\r\n\r\n")
            data = b""
            while part := sock.recv(65536):
                data += part
            assert data.split(b"\r\n\r\n", 1)[1] == PAYLOAD
        with authenticate(event) as control, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            control.sendall(b"\x05\x03\x00\x01" + b"\x00" * 6)
            reply = receive(control, 10)
            assert reply[1] == 0
            endpoint = (socket.inet_ntoa(reply[4:8]), struct.unpack("!H", reply[8:])[0])
            udp.settimeout(10)
            packet = b"\x00\x00\x00\x01" + socket.inet_aton("10.99.0.1") + struct.pack("!H", 9000) + b"harbor-udp"
            udp.sendto(packet, endpoint)
            assert udp.recvfrom(65536)[0][10:] == b"harbor-udp"

        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            rpc_port = reserve.getsockname()[1]
        state = work / protocol
        state.mkdir()
        out = state / "output"
        out.mkdir()
        proxy = f"socks5://{event['username']}:{event['password']}@127.0.0.1:{event['port']}"
        with (state / "engine.log").open("w") as log:
            aria = subprocess.Popen([
                str(ROOT / "Vendor" / "TorrentRuntime" / ARCH / "bin" / "aria2-next"),
                "--enable-rpc=true", "--rpc-listen-all=false", f"--rpc-listen-port={rpc_port}",
                "--rpc-secret=fixture", f"--state-dir={state}", f"--save-session={state}/session",
                f"--stop-with-process={os.getpid()}", "--seed-ratio=0", "--enable-dht=false",
                "--bt-enable-lpd=false", "--bt-port-mapping=false", "--bt-interface=lo0", f"--bt-proxy={proxy}",
            ], stdout=log, stderr=log)
            def rpc(method, params=None):
                data = json.dumps({"jsonrpc": "2.0", "id": "fixture", "method": "aria2." + method,
                                   "params": ["token:fixture"] + (params or [])}).encode()
                response = json.load(urllib.request.urlopen(urllib.request.Request(
                    f"http://127.0.0.1:{rpc_port}/jsonrpc", data=data), timeout=3))
                assert "error" not in response, response
                return response["result"]
            for _ in range(50):
                try:
                    rpc("getVersion")
                    break
                except OSError:
                    time.sleep(0.1)
            gid = rpc("addTorrent", [torrent("http://10.99.0.1:8080/payload.bin"), [], {"dir": str(out)}])
            for _ in range(120):
                status = rpc("tellStatus", [gid, ["status", "completedLength", "errorMessage"]])
                if int(status["completedLength"]) == len(PAYLOAD) or status["status"] == "error":
                    break
                time.sleep(0.25)
            assert int(status["completedLength"]) == len(PAYLOAD), status
            assert (out / "payload.bin").read_bytes() == PAYLOAD

            class Trap(http.server.BaseHTTPRequestHandler):
                hits = 0
                def do_GET(self):
                    Trap.hits += 1
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(PAYLOAD)))
                    self.end_headers()
                    self.wfile.write(PAYLOAD)
                def log_message(self, *args):
                    pass
            trap = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Trap)
            threading.Thread(target=trap.serve_forever, daemon=True).start()
            try:
                direct_url = f"http://127.0.0.1:{trap.server_port}/payload.bin"
                assert urllib.request.urlopen(direct_url).read() == PAYLOAD
                assert Trap.hits == 1, "Direct downloads must remain independent of the VPN"
                # Close the owner pipe during use; leave aria2 alive to detect proxy fallback.
                helper.stdin.close()
                helper.wait(timeout=5)
                assert helper.returncode == 0
                gid = rpc("addTorrent", [torrent(direct_url, "must-not-download.bin"), [], {"dir": str(out)}])
                time.sleep(4)
                status = rpc("tellStatus", [gid, ["completedLength"]])
                assert Trap.hits == 1 and int(status["completedLength"]) == 0, "Torrent bypassed the stopped VPN"
                assert urllib.request.urlopen(direct_url).read() == PAYLOAD
            finally:
                trap.shutdown()
                trap.server_close()
        print(f"{protocol}: authenticated TCP/DNS, UDP, torrent checksum, direct HTTP, and tunnel loss PASS", flush=True)
    finally:
        if aria:
            stop(aria)
        stop(helper)


def main():
    with tempfile.TemporaryDirectory(prefix="harbor-vpn-test-") as directory:
        work = pathlib.Path(directory)
        fixture = ROOT / "Tools" / "VPNRuntime" / "fixtures"
        name = "harbor-vpn-test-" + uuid.uuid4().hex[:10]
        command("docker", "build", "-q", "--build-arg", f"TEST_CERTIFICATE_GENERATION={uuid.uuid4().hex}",
                "-t", "harbor-vpn-fixture:local", str(fixture / "openvpn"))
        command("docker", "run", "-d", "--name", name, "--cap-add", "NET_ADMIN", "--device", "/dev/net/tun",
                "-p", "127.0.0.1::1194/udp", "harbor-vpn-fixture:local")
        try:
            port = command("docker", "port", name, "1194/udp").rsplit(":", 1)[1]
            config = f"client\ndev tun\nproto udp\nremote 127.0.0.1 {port}\nremote-cert-tls server\nauth SHA256\n"
            for tag, file in [("ca", "ca.crt"), ("cert", "client.crt"), ("key", "client.key")]:
                pem = command("docker", "exec", name, "cat", "/vpn/" + file)
                config += f"<{tag}>\n{pem}\n</{tag}>\n"
            check_profile("openvpn", config, work)
        finally:
            command("docker", "rm", "-f", name)
        environment = os.environ | {"GOMODCACHE": str(work / "gomod"), "GOCACHE": str(work / "gocache")}
        subprocess.run(["go", "build", "-o", str(work / "wireguard-server"), "./fixtures/wireguard-server"],
                       cwd=ROOT / "Tools" / "VPNRuntime", env=environment, check=True)
        server = subprocess.Popen([str(work / "wireguard-server")], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True)
        try:
            config = json.loads(server.stdout.readline())["config"]
            check_profile("wireguard", config, work)
        finally:
            server.stdin.close()
            stop(server)


if __name__ == "__main__":
    main()
