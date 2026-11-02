FROM busybox:1.37.0-musl AS probe
FROM grafana/alloy:v1.11.2
COPY --from=probe /bin/busybox /bin/busybox
USER alloy
