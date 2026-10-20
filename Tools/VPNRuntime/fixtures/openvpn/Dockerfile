FROM alpine:3.22
RUN apk add --no-cache openvpn openssl python3
COPY setup.sh /setup.sh
COPY server.py /server.py
ARG TEST_CERTIFICATE_GENERATION
RUN sh /setup.sh
CMD openvpn --config /vpn/server.conf & exec python3 /server.py
