#!/bin/sh
# Start squid in the foreground; rotate the access log daily (90 kept) and keep the logs readable by the broker,
# which mounts the log volume read-only and only reads domains and byte counts.
set -e
mkdir -p /var/log/squid
chown -R proxy:proxy /var/log/squid
(
  while true; do
    i=0
    while [ $i -lt 288 ]; do sleep 300; chmod -R a+rX /var/log/squid 2>/dev/null || true; i=$((i + 1)); done
    squid -k rotate 2>/dev/null || true
  done
) &
exec squid -N -f /etc/squid/squid.conf
