ARG PLUGIN_BUILDER
FROM ${PLUGIN_BUILDER} AS plugins
USER 0:0
ADD --checksum=sha256:982cfb81977352db65404cec671952453454643bd9a6c883041c38be519a74ea https://grafana.com/api/plugins/loki/versions/13.2.5/download?os=linux&arch=amd64 /archives/loki.zip
ADD --checksum=sha256:d24df3eaff9ae61f00d7e857f508fc306912dcf50d657150d1e472e851799fc4 https://grafana.com/api/plugins/prometheus/versions/13.2.5/download?os=linux&arch=amd64 /archives/prometheus.zip
ADD --checksum=sha256:2717de75a5b6a1a27d79603e3eedd728a5d824be4411cf81af0fe3fcc7a779b2 https://grafana.com/api/plugins/tempo/versions/13.4.2/download?os=linux&arch=amd64 /archives/tempo.zip
COPY infra/local-lab/extract-plugins.py /extract-plugins.py
RUN python /extract-plugins.py
FROM grafana/grafana@sha256:488984b716c0862639f91a698adbe2862677a5a07bad42da5f03f3030883b205
USER 0:0
RUN rm -rf /usr/share/grafana/data/plugins-bundled/cloudwatch /usr/share/grafana/data/plugins-bundled/elasticsearch /usr/share/grafana/data/plugins-bundled/grafana-postgresql-datasource /usr/share/grafana/data/plugins-bundled/grafana-pyroscope-datasource /usr/share/grafana/data/plugins-bundled/influxdb /usr/share/grafana/data/plugins-bundled/jaeger /usr/share/grafana/data/plugins-bundled/loki /usr/share/grafana/data/plugins-bundled/mssql /usr/share/grafana/data/plugins-bundled/mysql /usr/share/grafana/data/plugins-bundled/opentsdb /usr/share/grafana/data/plugins-bundled/prometheus /usr/share/grafana/data/plugins-bundled/stackdriver /usr/share/grafana/data/plugins-bundled/tempo /usr/share/grafana/data/plugins-bundled/zipkin
COPY --from=plugins --chown=472:0 /out/ /usr/share/grafana/data/plugins-bundled/
USER 472:0
