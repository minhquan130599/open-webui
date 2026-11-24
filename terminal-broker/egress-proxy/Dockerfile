# Egress proxy for the terminals (squid with an access log the broker reads).
#   docker build -t ncs-egress-proxy:latest terminal-broker/egress-proxy
FROM debian:trixie-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends squid ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY squid.conf /etc/squid/squid.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh && squid -k parse -f /etc/squid/squid.conf
EXPOSE 3128
ENTRYPOINT ["/entrypoint.sh"]
