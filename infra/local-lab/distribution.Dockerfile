ARG GO_IMAGE
FROM ${GO_IMAGE} AS build
ARG SOURCE_URL
ARG SOURCE_SHA256
ADD --checksum=sha256:${SOURCE_SHA256} ${SOURCE_URL} /src.tar.gz
RUN mkdir /src && tar -xzf /src.tar.gz --strip-components=1 -C /src
WORKDIR /src
ENV GOMAXPROCS=2 GOMEMLIMIT=2GiB GOTOOLCHAIN=local
RUN go get github.com/go-jose/go-jose/v4@v4.1.6 github.com/klauspost/compress@v1.18.7 golang.org/x/net@v0.60.0
RUN CGO_ENABLED=0 go build -mod=mod -p=2 -trimpath -o /registry ./cmd/registry
FROM scratch
COPY --from=build /registry /bin/registry
COPY --from=build /src/LICENSE /LICENSE
USER 65534:65534
ENTRYPOINT ["/bin/registry", "serve", "/etc/distribution/config.yml"]
