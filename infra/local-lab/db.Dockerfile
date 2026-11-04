ARG POSTGRES_IMAGE
ARG GO_IMAGE
FROM ${POSTGRES_IMAGE} AS build
ARG SOURCE_URL
ARG SOURCE_SHA256
ADD --checksum=sha256:${SOURCE_SHA256} ${SOURCE_URL} /src.tar.gz
RUN apk add --no-cache build-base
RUN mkdir /src && tar -xzf /src.tar.gz --strip-components=1 -C /src
WORKDIR /src
RUN make -j2 OPTFLAGS="" with_llvm=no && make install with_llvm=no
FROM ${GO_IMAGE} AS gosu
ARG GOSU_SOURCE_URL
ARG GOSU_SOURCE_SHA256
ADD --checksum=sha256:${GOSU_SOURCE_SHA256} ${GOSU_SOURCE_URL} /gosu.tar.gz
RUN mkdir /src && tar -xzf /gosu.tar.gz --strip-components=1 -C /src
WORKDIR /src
ENV GOMAXPROCS=2 GOMEMLIMIT=1GiB GOTOOLCHAIN=local
RUN CGO_ENABLED=0 go build -mod=mod -trimpath -o /gosu .
FROM ${POSTGRES_IMAGE}
COPY --from=gosu /gosu /usr/local/bin/gosu
COPY --from=gosu /src/LICENSE /usr/share/licenses/gosu/LICENSE
COPY --from=build /usr/local/lib/postgresql/vector.so /usr/local/lib/postgresql/vector.so
COPY --from=build /usr/local/share/postgresql/extension/vector* /usr/local/share/postgresql/extension/
COPY --from=build /src/LICENSE /usr/share/licenses/pgvector/LICENSE
