ARG GO_IMAGE
FROM ${GO_IMAGE} AS build
ARG SOURCE_URL
ARG SOURCE_SHA256
ADD --checksum=sha256:${SOURCE_SHA256} ${SOURCE_URL} /src.tar.gz
RUN mkdir /src && tar -xzf /src.tar.gz --strip-components=1 -C /src
WORKDIR /src
ENV GOMAXPROCS=2 GOMEMLIMIT=2GiB GOTOOLCHAIN=local
RUN go get golang.org/x/net@v0.60.0
RUN CGO_ENABLED=0 go build -mod=mod -p=2 -trimpath -o /node_exporter .
FROM scratch
COPY --from=build /node_exporter /bin/node_exporter
COPY --from=build /src/LICENSE /LICENSE
USER 65534:65534
ENTRYPOINT ["/bin/node_exporter"]
