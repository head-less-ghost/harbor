# Bundled Aria2 Next Runtime

This directory holds Harbor's self-contained `aria2-next` runtime for torrent support.

Current bundled architectures:

- arm64 and x86_64: `QZGao/aria2-next`, a fork of `AnInsomniacy/aria2-next` with modifications to support per-download HTTP headers for BitTorrent web-seeds.

## Local patches

The binaries use version 2.7.5 from commit `c8cfa3b6afcecd8e417ae82077045a1e69f53eeb`
with the patches in `patches/`:

- `hash-failure-progress.patch`: Refresh progress after a rejected piece. A piece
  hash failure does not start a full torrent check, so it must not wait for
  `torrent_checked_alert`. That wait froze completed bytes and prevented completion
  from reaching Harbor even when the recovered file was correct.
- `disable-torrent-uploads.patch`: Add the session-wide `bt-enable-uploads` option.
  When false, libtorrent rejects file-piece requests, blocks pending disk-read
  results, and closes existing peer connections on a live policy change. The
  default is true. Metadata and protocol messages remain enabled. The policy
  covers TCP, uTP, encrypted connections, local peers, and allowed-fast requests.
- `macos-cstring-include-order.patch`: Include the C++ string header before GPAC
  defines its string-function macros. This permits a build with the current Apple SDK.

TODO: Remove the local patches when an upstream release contains these fixes.

## Rebuild

Use CMake 3.25 or later, Ninja, Python 3, and Xcode command-line tools. Build in a
temporary directory. Set `architecture` to `arm64` or `x86_64` and repeat for both.

```sh
harbor_root="$PWD"
runtime_work=$(mktemp -d)
architecture=arm64
git clone https://github.com/QZGao/aria2-next.git "$runtime_work/source"
cd "$runtime_work/source"
git checkout c8cfa3b6afcecd8e417ae82077045a1e69f53eeb
git apply "$harbor_root/Vendor/TorrentRuntime/patches/"*.patch
export MACOSX_DEPLOYMENT_TARGET=11.0
cmake -S . -B build/runtime -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_SYSTEM_NAME=Darwin \
  -DCMAKE_SYSTEM_PROCESSOR="$architecture" \
  -DCMAKE_OSX_ARCHITECTURES="$architecture" \
  -DCMAKE_OSX_DEPLOYMENT_TARGET=11.0 -DBUILD_TESTING=ON
cmake --build build/runtime -j8
ctest --test-dir build/runtime/source --output-on-failure
python3 "$harbor_root/Scripts/test-torrent-hash-recovery.py" build/runtime/aria2-next
python3 "$harbor_root/Scripts/test-torrent-upload-policy.py" build/runtime/aria2-next
packaging/scripts/check-runtime-deps macos build/runtime/aria2-next
strip build/runtime/aria2-next
codesign --force --sign - build/runtime/aria2-next
cp build/runtime/aria2-next "$harbor_root/Vendor/TorrentRuntime/$architecture/bin/aria2-next"
```

The regression uses a corrupt local peer and a valid local web seed. It verifies
the recovered file, full progress, and completion without a restart. It does not
use the running Harbor session or external peers. An Intel binary requires
Rosetta when the test runs on Apple Silicon.

The upload-policy check uses isolated engines and loopback peers. It verifies a
live upload stop during a partial download, a partial-download restart, correct download completion, blocked
uploads after a completed-download restart, and uploads after re-enabling the option. It
covers plain TCP, encrypted TCP, and encrypted uTP.

Each `TorrentRuntime/<arch>/bin` folder includes the standalone `aria2-next` binary so Harbor can launch torrents without requiring Homebrew on the user's Mac.

Harbor starts the engine with DHT, local peer discovery, and port mapping disabled. It enables DHT and port mapping before a torrent add, resume, or magnet preview. A local RPC check disables them when no active or runnable queued torrents remain. Paused recovery entries do not keep discovery enabled. The engine process stays available for recovery and settings operations.

Idle transitions use `aria2.changeGlobalOption`; they do not restart the engine. Libtorrent can bootstrap DHT again when discovery resumes. Peer blocklists, proxy settings, and interface bindings still apply. The five-second task-session checkpoint remains separate from DHT routing-state writes.
