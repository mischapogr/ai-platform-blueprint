ARG CADDY_BASE
FROM ${CADDY_BASE}
RUN setcap -r /usr/bin/caddy
