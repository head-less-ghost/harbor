// A disposable WireGuard server with an in-memory network. No OS routes or TUN.
package main

import (
	"bytes"
	"crypto/ecdh"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/netip"
	"os"
	"strings"
	"time"

	"golang.zx2c4.com/wireguard/conn"
	"golang.zx2c4.com/wireguard/device"
	"golang.zx2c4.com/wireguard/tun/netstack"
)

func main() {
	server, _ := ecdh.X25519().GenerateKey(rand.Reader)
	client, _ := ecdh.X25519().GenerateKey(rand.Reader)
	ip := netip.MustParseAddr("10.99.0.1")
	tun, stack, err := netstack.CreateNetTUN([]netip.Addr{ip}, nil, 1420)
	if err != nil {
		panic(err)
	}
	dev := device.NewDevice(tun, conn.NewDefaultBind(), device.NewLogger(device.LogLevelSilent, ""))
	defer dev.Close()
	err = dev.IpcSet(fmt.Sprintf("private_key=%s\nlisten_port=0\npublic_key=%s\nallowed_ip=10.99.0.2/32\n", hex.EncodeToString(server.Bytes()), hex.EncodeToString(client.PublicKey().Bytes())))
	if err != nil {
		panic(err)
	}
	if err = dev.Up(); err != nil {
		panic(err)
	}
	status, _ := dev.IpcGet()
	var actualPort int
	for _, line := range strings.Split(status, "\n") {
		_, _ = fmt.Sscanf(line, "listen_port=%d", &actualPort)
	}
	if actualPort == 0 {
		panic("No server port")
	}
	payload := make([]byte, 1024*1024)
	for i := range payload {
		payload[i] = byte(i)
	}
	tcp, err := stack.ListenTCPAddrPort(netip.AddrPortFrom(ip, 8080))
	if err != nil {
		panic(err)
	}
	go http.Serve(tcp, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.ServeContent(w, r, "payload.bin", time.Time{}, bytes.NewReader(payload))
	}))
	for _, port := range []uint16{9000, 53} {
		socket, err := stack.ListenUDPAddrPort(netip.AddrPortFrom(ip, port))
		if err != nil {
			panic(err)
		}
		go func(socket net.PacketConn, dns bool) {
			defer socket.Close()
			buf := make([]byte, 65535)
			for {
				n, addr, err := socket.ReadFrom(buf)
				if err != nil {
					return
				}
				data := buf[:n]
				if dns && len(data) > 12 {
					answer := append([]byte{}, data[:2]...)
					answer = append(answer, 0x81, 0x80, 0, 1, 0, 1, 0, 0, 0, 0)
					answer = append(answer, data[12:]...)
					answer = append(answer, 0xc0, 0x0c, 0, 1, 0, 1, 0, 0, 0, 60, 0, 4, 10, 99, 0, 1)
					data = answer
				}
				_, _ = socket.WriteTo(data, addr)
			}
		}(socket, port == 53)
	}
	config := fmt.Sprintf("[Interface]\nPrivateKey = %s\nAddress = 10.99.0.2/32\nDNS = 10.99.0.1\n[Peer]\nPublicKey = %s\nEndpoint = 127.0.0.1:%d\nAllowedIPs = 0.0.0.0/0\nPersistentKeepalive = 1\n", base64.StdEncoding.EncodeToString(client.Bytes()), base64.StdEncoding.EncodeToString(server.PublicKey().Bytes()), actualPort)
	_ = json.NewEncoder(os.Stdout).Encode(map[string]string{"config": config})
	_, _ = io.Copy(io.Discard, os.Stdin)
}
