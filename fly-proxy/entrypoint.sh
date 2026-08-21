#!/bin/sh
set -eu

: "${BASIC_AUTH_USER:?BASIC_AUTH_USER env var is required}"
: "${BASIC_AUTH_PASS:?BASIC_AUTH_PASS env var is required}"

mkdir -p /var/run/tinyproxy
envsubst '${BASIC_AUTH_USER} ${BASIC_AUTH_PASS}' \
  < /etc/tinyproxy/tinyproxy.conf.template \
  > /etc/tinyproxy/tinyproxy.conf

exec tinyproxy -d -c /etc/tinyproxy/tinyproxy.conf
