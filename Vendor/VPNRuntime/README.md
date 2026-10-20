# Private VPN runtime

Harbor routes torrent traffic through an authenticated loopback SOCKS5 endpoint.
The endpoint uses an in-memory TCP/IP stack. It does not install network routes,
change system DNS, or create a macOS network adapter. HTTP and API traffic use
Harbor's normal connection.

- `harbor-vpn`: WireGuard and SOCKS5 helper, built from `Tools/VPNRuntime`.
- `harbor-openvpn`: OpenVPN 3 Core adapter with a private packet socket.
- OpenVPN 3 Core: `1b4db00849bfb27e05c226cd13f6985527b6bb79`.
- WireGuard/gVisor versions are locked in `Tools/VPNRuntime/go.mod` and `go.sum`.
- OpenSSL 3.6.4 is linked statically. The helper has no Homebrew runtime dependency.
- The WireGuard parser, transport, and SOCKS implementation originate from
  BATorrent `ebf63e989d313f85903e2ba277eeaea4ba814745` (MIT). See `licenses/`.

Run `Scripts/vendor-vpn-runtime.sh` to build both supported Mac architectures.
Xcode copies and signs these runtimes with the existing vendored runtime phases.
The build requires Go 1.26 or later and Xcode Command Line Tools.
Run `python3 Scripts/test-vpn-runtime.py` with Docker running to check both
protocols, DNS, TCP, UDP, torrent transfers, and blocking after tunnel loss.

Profiles and credentials enter over stdin. The parent keeps the pipe open as a
lifetime lease. The endpoint reports only connection state and temporary proxy
credentials on stdout. Provider secrets must not enter process arguments or logs.

OpenVPN profiles must include their CA/certificates and keys inline. External
certificate stores, encrypted private keys, scripts, and interactive browser/MFA
authentication are not supported by this first experimental version.

When the feature is enabled, Harbor blocks torrents until the endpoint is ready.
Disconnection leaves torrents blocked. Turning off the Experimental toggle is an
explicit return to Harbor's normal torrent route.

TODO: Add provider port forwarding after the profile-based connection is stable.
