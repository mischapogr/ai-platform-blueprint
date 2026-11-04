ARG GO_IMAGE
FROM ${GO_IMAGE} AS build
ARG SOURCE_REVISION
ARG SOURCE_SHA256
ADD --checksum=sha256:${SOURCE_SHA256} https://github.com/oliver006/redis_exporter/archive/${SOURCE_REVISION}.tar.gz /src.tar.gz
RUN mkdir /src && tar -xzf /src.tar.gz --strip-components=1 -C /src
WORKDIR /src
RUN CGO_ENABLED=0 go build -trimpath -o /redis_exporter .
FROM scratch
COPY --from=build /redis_exporter /redis_exporter
COPY --from=build /src/LICENSE /LICENSE
USER 65534:65534
ENTRYPOINT ["/redis_exporter"]
