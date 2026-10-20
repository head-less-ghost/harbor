// Harbor's private VPN endpoint. Configuration enters on stdin; closing stdin
// retires the endpoint and all tunnel traffic. No system routes are installed.
package main

import (
	"bufio"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"io"
	"net"
	"net/netip"
	"os"
	"sync"
	"time"
)

type request struct {
	Protocol string `json:"protocol"`
	Config   string `json:"config"`
	Username string `json:"username"`
	Password string `json:"password"`
}

var outputMu sync.Mutex

func emit(v map[string]any) {
	outputMu.Lock()
	defer outputMu.Unlock()
	_ = json.NewEncoder(os.Stdout).Encode(v)
}
func fail(message string) { emit(map[string]any{"event": "error", "message": message}); os.Exit(1) }
func logf(string, ...any) {} // Never emit provider profiles, credentials, or peer addresses.
func randomHex(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		fail("Could not create VPN endpoint credentials.")
	}
	return hex.EncodeToString(b)
}

type tunnelAdapter struct{ t *tunnel }

func (a tunnelAdapter) DialContext(ctx context.Context, network, address string) (net.Conn, error) {
	return a.t.tnet.DialContext(ctx, network, address)
}
func (a tunnelAdapter) LookupContextHost(ctx context.Context, host string) ([]string, error) {
	return a.t.tnet.LookupContextHost(ctx, host)
}
func (a tunnelAdapter) listenUDP(v6 bool, port uint16) (net.PacketConn, error) {
	addr := a.t.v4
	if v6 {
		addr = a.t.v6
	}
	return a.t.tnet.ListenUDPAddrPort(netip.AddrPortFrom(addr, port))
}
func (a tunnelAdapter) hasV4() bool { return a.t.v4.IsValid() }
func (a tunnelAdapter) hasV6() bool { return a.t.v6.IsValid() }

func main() {
	input := bufio.NewReader(os.Stdin)
	line, err := input.ReadBytes('\n')
	if err != nil || len(line) > 4*1024*1024 {
		fail("Could not read VPN configuration.")
	}
	var req request
	if json.Unmarshal(line, &req) != nil {
		fail("The VPN configuration is invalid.")
	}
	closed := make(chan struct{})
	go func() {
		_, _ = io.Copy(io.Discard, input)
		close(closed)
		// Startup can be waiting for a server. Bound the lease even in that state.
		time.Sleep(time.Second)
		os.Exit(0)
	}()
	var tn tunnelNet
	var cleanup func()
	var unhealthy func() bool
	switch req.Protocol {
	case "wireguard":
		cfg, err := parseConf(req.Config)
		if err != nil {
			fail("The WireGuard configuration is invalid.")
		}
		t, err := startTunnel(cfg, &udpBind{}, resolveEndpoint, logf)
		if err != nil {
			fail("Could not start WireGuard. Check the profile and server address.")
		}
		cleanup = t.close
		defer cleanup()
		if !t.waitHandshake(25 * time.Second) {
			fail("The WireGuard server did not answer. Check the profile and connection.")
		}
		tn = tunnelAdapter{t}
		unhealthy = func() bool { return time.Since(t.stats().handshake) > 3*time.Minute }
	case "openvpn":
		t, err := startOpenVPN(req)
		if err != nil {
			fail(err.Error())
		}
		tn = t
		cleanup = t.close
		defer cleanup()
		unhealthy = t.unhealthy
	default:
		fail("Select an OpenVPN or WireGuard profile.")
	}
	// The endpoint exists only after the tunnel has completed its authenticated
	// handshake. Failed tunnel sockets have no direct-network dialer to use.
	user, pass := randomHex(16), randomHex(24)
	server, err := newSocksServer("127.0.0.1:0", tn, user, pass, logf)
	if err != nil {
		fail("Could not start the private VPN endpoint.")
	}
	defer server.close()
	go server.serve()
	emit(map[string]any{"event": "ready", "port": server.port(), "username": user, "password": pass})
	tick := time.NewTicker(time.Second)
	defer tick.Stop()
	for {
		select {
		case <-closed:
			return
		case <-tick.C:
			if unhealthy() {
				server.close()
				cleanup()
				fail("The VPN connection stopped. Torrent traffic is blocked.")
			}
		}
	}
}
