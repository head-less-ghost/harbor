#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${VPN_BUILD_DIRECTORY:-$(mktemp -d /tmp/harbor-vpn-build.XXXXXX)}"
mkdir -p "$WORK"
OPENVPN_REF=1b4db00849bfb27e05c226cd13f6985527b6bb79
ASIO_REF=asio-1-36-0
OPENSSL_REF=openssl-3.6.4
FMT_REF=12.2.0
fetch() {
  local repo="$1" ref="$2" dest="$3"
  if [ ! -f "$dest/.harbor-source" ]; then
    mkdir -p "$dest"
    curl --fail --location --retry 3 "https://api.github.com/repos/$repo/tarball/$ref" -o "$WORK/source.tar.gz"
    tar -xzf "$WORK/source.tar.gz" -C "$dest" --strip-components=1
    touch "$dest/.harbor-source"
  fi
}
fetch OpenVPN/openvpn3 "$OPENVPN_REF" "$WORK/openvpn3"
fetch chriskohlhoff/asio "$ASIO_REF" "$WORK/asio"
fetch fmtlib/fmt "$FMT_REF" "$WORK/fmt"
export GOMODCACHE="$WORK/gomod" GOCACHE="$WORK/gocache"
export MACOSX_DEPLOYMENT_TARGET=15.6
for architecture in arm64 x86_64; do
  ssl="$WORK/openssl-$architecture"
  fetch openssl/openssl "$OPENSSL_REF" "$ssl"
  if [ ! -f "$ssl/libssl.a" ]; then
    target=darwin64-arm64-cc
    if [ "$architecture" = x86_64 ]; then target=darwin64-x86_64-cc; fi
    (cd "$ssl" && ./Configure "$target" no-shared no-tests no-apps no-docs no-legacy CC="clang -arch $architecture" && make -j8 build_libs)
  fi
  output="$ROOT/Vendor/VPNRuntime/$architecture/bin"
  mkdir -p "$output"
  goarch=arm64
  if [ "$architecture" = x86_64 ]; then goarch=amd64; fi
  (cd "$ROOT/Tools/VPNRuntime" && GOOS=darwin GOARCH="$goarch" CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o "$output/harbor-vpn" .)
  clang++ -std=c++20 -arch "$architecture" -mmacosx-version-min="$MACOSX_DEPLOYMENT_TARGET" -O2 \
    -DUSE_TUN_BUILDER -DUSE_ASIO -DASIO_STANDALONE -DUSE_OPENSSL -DFMT_HEADER_ONLY \
    -I"$WORK/openvpn3" -I"$WORK/asio/asio/include" -I"$WORK/fmt/include" -I"$ssl/include" \
    "$ROOT/Tools/VPNRuntime/core/main.cpp" "$WORK/openvpn3/client/ovpncli.cpp" \
    "$WORK/openvpn3/openvpn/crypto/data_epoch.cpp" "$ssl/libssl.a" "$ssl/libcrypto.a" \
    -framework CoreFoundation -framework SystemConfiguration -framework IOKit -o "$output/harbor-openvpn"
  strip "$output/harbor-openvpn"
  codesign --force --sign - "$output/harbor-vpn"
  codesign --force --sign - "$output/harbor-openvpn"
done
