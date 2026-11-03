#!/usr/bin/env bash
# Flower TLS material on the central VM (idempotent): a Flower CA of its own and the SuperLink's
# certificate, in $TLS_DIR (default ~/flhub-runtime/flower/tls, mode 700, never in git).
# The SuperLink uses the certificate on all its APIs: the Fleet API for the hospitals and, on
# 127.0.0.1, the HTTP API for flwr run and the ServerApp. Hospitals get only ca.crt (public)
# and check its sha256, which this script prints. No hospital key is made here.
#   bash deploy/central/flower/tls.sh            # make what is missing, print fingerprints
#   bash deploy/central/flower/tls.sh --renew    # new SuperLink certificate, same CA
set -euo pipefail
. "$(dirname "$0")/common.sh"
renew=0; [ "${1:-}" = "--renew" ] && renew=1
CA_DAYS=3650; SERVER_DAYS=825
SAN="IP:$FLHUB_CENTRAL_IP,IP:127.0.0.1,DNS:${FLHUB_CENTRAL_HOSTNAME:-flhub-central},DNS:localhost"

umask 077
mkdir -p "$TLS_DIR" && chmod 700 "$TLS_DIR"
cd "$TLS_DIR"
if [ ! -f ca.key ]; then
  openssl ecparam -name secp384r1 -genkey -noout -out ca.key
  openssl req -x509 -new -key ca.key -sha384 -days "$CA_DAYS" -subj "/O=AgnosticFL/CN=flhub Flower CA" \
      -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign" \
      -out ca.crt
  echo "tls: new Flower CA $TLS_DIR/ca.crt"
fi
if [ ! -f server.crt ] || [ "$renew" = 1 ]; then
  openssl ecparam -name secp384r1 -genkey -noout -out server.key
  openssl req -new -key server.key -sha384 -subj "/O=AgnosticFL/CN=flhub-central" -out server.csr
  printf 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\nsubjectAltName=%s\n' \
      "$SAN" > server.ext
  openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -sha384 \
      -days "$SERVER_DAYS" -extfile server.ext -out server.crt 2>/dev/null
  rm -f server.csr server.ext
  echo "tls: new SuperLink certificate ($SAN); restart the SuperLink to use it"
fi
chmod 600 ./*.key; chmod 644 ca.crt server.crt
openssl verify -CAfile ca.crt server.crt >/dev/null
echo "tls: $TLS_DIR: ca.crt, server.crt (valid until $(openssl x509 -in server.crt -noout -enddate | cut -d= -f2))"
echo "ca.crt sha256 $(sha256sum ca.crt | cut -d' ' -f1)   (hospitals compare this with the ca.crt they got)"
