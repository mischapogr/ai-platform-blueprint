# Loki's minimal image has no HTTP probe binary. Add only static BusyBox.
FROM busybox:1.37.0-musl AS probe
FROM grafana/loki:3.6.1
COPY --from=probe /bin/busybox /bin/busybox
