set -eu
mkdir /vpn
cd /vpn
openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.crt -subj /CN=Harbor-Test-CA -days 2 2>/dev/null
for role in server client; do
  openssl req -newkey rsa:2048 -nodes -keyout "$role.key" -out "$role.csr" -subj "/CN=Harbor-Test-$role" 2>/dev/null
  usage=clientAuth
  if [ "$role" = server ]; then usage=serverAuth; fi
  echo "extendedKeyUsage=$usage" > ext
  openssl x509 -req -in "$role.csr" -CA ca.crt -CAkey ca.key -CAcreateserial -out "$role.crt" -days 2 -extfile ext 2>/dev/null
 done
cat > server.conf <<'CONFIG'
port 1194
proto udp
# This interface exists only inside the disposable Linux container.
dev tun
topology subnet
server 10.99.0.0 255.255.255.0
ca /vpn/ca.crt
cert /vpn/server.crt
key /vpn/server.key
dh none
auth SHA256
data-ciphers AES-256-GCM:AES-128-GCM
push "dhcp-option DNS 10.99.0.1"
keepalive 2 10
verb 3
CONFIG
