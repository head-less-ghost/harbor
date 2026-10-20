package main

import (
	"bufio"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/netip"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"time"

	"golang.org/x/sys/unix"
	"golang.zx2c4.com/wireguard/tun"
	"golang.zx2c4.com/wireguard/tun/netstack"
)

type openVPN struct {
	tunnelNet
	dev  tun.Device
	pipe net.Conn
	cmd  *exec.Cmd
	done chan struct{}
	once sync.Once
}

func (t *openVPN) close() {
	t.once.Do(func() {
		_ = t.pipe.Close()
		if t.dev != nil {
			_ = t.dev.Close()
		}
		_ = t.cmd.Process.Kill()
		<-t.done
	})
}
func (t *openVPN) unhealthy() bool {
	select {
	case <-t.done:
		return true
	default:
		return false
	}
}

type stackAdapter struct {
	n      *netstack.Net
	v4, v6 netip.Addr
}

func (a stackAdapter) hasV4() bool { return a.v4.IsValid() }
func (a stackAdapter) hasV6() bool { return a.v6.IsValid() }
func (a stackAdapter) DialContext(ctx context.Context, network, address string) (net.Conn, error) {
	return a.n.DialContext(ctx, network, address)
}
func (a stackAdapter) LookupContextHost(ctx context.Context, host string) ([]string, error) {
	return a.n.LookupContextHost(ctx, host)
}
func (a stackAdapter) listenUDP(v6 bool, port uint16) (net.PacketConn, error) {
	ip := a.v4
	if v6 {
		ip = a.v6
	}
	return a.n.ListenUDPAddrPort(netip.AddrPortFrom(ip, port))
}

type coreEvent struct {
	Event     string   `json:"event"`
	Addresses []string `json:"addresses"`
	DNS       []string `json:"dns"`
	MTU       int      `json:"mtu"`
}

func startOpenVPN(req request) (*openVPN, error) {
	fds, err := unix.Socketpair(unix.AF_UNIX, unix.SOCK_DGRAM, 0)
	if err != nil {
		return nil, errors.New("Could not create the OpenVPN packet channel.")
	}
	parent := os.NewFile(uintptr(fds[0]), "vpn-packets")
	child := os.NewFile(uintptr(fds[1]), "vpn-core-packets")
	defer parent.Close()
	defer child.Close()
	pipe, err := net.FileConn(parent)
	if err != nil {
		return nil, err
	}
	exe, err := os.Executable()
	if err != nil {
		pipe.Close()
		return nil, err
	}
	cmd := exec.Command(filepath.Join(filepath.Dir(exe), "harbor-openvpn"))
	cmd.ExtraFiles = []*os.File{child}
	input, err := cmd.StdinPipe()
	if err != nil {
		pipe.Close()
		return nil, err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		input.Close()
		pipe.Close()
		return nil, err
	}
	cmd.Stderr = io.Discard
	if err = cmd.Start(); err != nil {
		input.Close()
		pipe.Close()
		return nil, errors.New("The bundled OpenVPN engine could not start.")
	}
	t := &openVPN{pipe: pipe, cmd: cmd, done: make(chan struct{})}
	events := make(chan coreEvent, 8)
	go func() {
		defer close(events)
		sc := bufio.NewScanner(stdout)
		for sc.Scan() {
			var e coreEvent
			if json.Unmarshal(sc.Bytes(), &e) == nil {
				events <- e
			}
		}
	}()
	go func() { _ = cmd.Wait(); close(t.done) }()
	go func() {
		defer input.Close()
		for _, s := range []string{req.Config, req.Username, req.Password} {
			_ = binary.Write(input, binary.BigEndian, uint32(len(s)))
			_, _ = io.WriteString(input, s)
		}
	}()
	timeout := time.NewTimer(45 * time.Second)
	defer timeout.Stop()
	for {
		select {
		case <-timeout.C:
			t.close()
			return nil, errors.New("OpenVPN timed out. Check the server and credentials.")
		case e, ok := <-events:
			if !ok || e.Event == "error" {
				t.close()
				return nil, errors.New("OpenVPN could not connect. Check the profile, credentials, and server certificate.")
			}
			if e.Event == "configuration" {
				if t.dev != nil {
					t.close()
					return nil, errors.New("The VPN changed its network settings. Reconnect to continue.")
				}
				var addrs, dns []netip.Addr
				for _, s := range e.Addresses {
					a, err := netip.ParseAddr(s)
					if err != nil {
						t.close()
						return nil, errors.New("The VPN assigned an invalid address.")
					}
					addrs = append(addrs, a)
				}
				for _, s := range e.DNS {
					a, err := netip.ParseAddr(s)
					if err == nil {
						dns = append(dns, a)
					}
				}
				if len(addrs) == 0 {
					t.close()
					return nil, errors.New("OpenVPN did not assign a tunnel address.")
				}
				if len(dns) == 0 {
					dns = defaultDNS
				}
				dev, stack, err := netstack.CreateNetTUN(addrs, dns, e.MTU)
				if err != nil {
					t.close()
					return nil, err
				}
				t.dev = dev
				adapter := stackAdapter{n: stack}
				for _, a := range addrs {
					if a.Is4() {
						adapter.v4 = a
					} else {
						adapter.v6 = a
					}
				}
				t.tunnelNet = adapter
				go t.pumpToCore()
				go t.pumpFromCore()
			}
			if e.Event == "connected" && t.dev != nil {
				go func() {
					for range events { // Any disconnect/reconnect retires the endpoint; Harbor reconnects explicitly.
						t.close()
						return
					}
				}()
				return t, nil
			}
		}
	}
}
func (t *openVPN) pumpToCore() {
	defer t.close()
	buf := make([]byte, 65540)
	sizes := make([]int, 1)
	for {
		n, err := t.dev.Read([][]byte{buf}, sizes, 4)
		if err != nil || n == 0 {
			return
		}
		family := uint32(unix.AF_INET)
		if buf[4]>>4 == 6 {
			family = uint32(unix.AF_INET6)
		}
		binary.BigEndian.PutUint32(buf[:4], family)
		if _, err = t.pipe.Write(buf[:sizes[0]+4]); err != nil {
			return
		}
	}
}
func (t *openVPN) pumpFromCore() {
	defer t.close()
	buf := make([]byte, 65540)
	for {
		n, err := t.pipe.Read(buf)
		if err != nil {
			return
		}
		if n > 4 {
			if _, err = t.dev.Write([][]byte{buf[:n]}, 4); err != nil {
				return
			}
		}
	}
}
