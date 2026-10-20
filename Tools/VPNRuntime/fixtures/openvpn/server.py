import http.server,socket,struct,threading
PAYLOAD=bytes(range(256))*4096
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200)
  self.send_header('Content-Length',str(len(PAYLOAD)))
  self.end_headers();self.wfile.write(PAYLOAD)
 def log_message(self,*args):pass
def echo():
 s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('0.0.0.0',9000))
 while True:
  d,a=s.recvfrom(65535);s.sendto(d,a)
def dns():
 s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('0.0.0.0',53))
 while True:
  d,a=s.recvfrom(4096)
  answer=d[:2]+b'\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00'+d[12:]+b'\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04'+socket.inet_aton('10.99.0.1')
  s.sendto(answer,a)
threading.Thread(target=echo,daemon=True).start();threading.Thread(target=dns,daemon=True).start()
http.server.ThreadingHTTPServer(('0.0.0.0',8080),Handler).serve_forever()
