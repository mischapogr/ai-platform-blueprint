ARG GO_IMAGE
ARG ALPINE_IMAGE
FROM ${GO_IMAGE} AS build
ARG SOURCE_URL
ARG SOURCE_SHA256
ARG LLAMA_SOURCE_URL
ARG LLAMA_SOURCE_SHA256
ADD --checksum=sha256:${SOURCE_SHA256} ${SOURCE_URL} /ollama.tar.gz
ADD --checksum=sha256:${LLAMA_SOURCE_SHA256} ${LLAMA_SOURCE_URL} /llama.tar.gz
RUN apk add --no-cache cmake ninja build-base git linux-headers
RUN mkdir /src /llama && tar -xzf /ollama.tar.gz --strip-components=1 -C /src && tar -xzf /llama.tar.gz --strip-components=1 -C /llama
WORKDIR /src
ENV GOMAXPROCS=2 GOMEMLIMIT=2GiB GOTOOLCHAIN=local CMAKE_BUILD_PARALLEL_LEVEL=2
RUN cmake -S llama/server --preset cpu -DFETCHCONTENT_SOURCE_DIR_LLAMA_CPP=/llama -DGGML_CPU_ALL_VARIANTS=OFF -DGGML_NATIVE=OFF && cmake --build build/llama-server-cpu -j2 && cmake --install build/llama-server-cpu --component llama-server --strip
RUN go build -mod=mod -p=2 -trimpath -ldflags="-X github.com/ollama/ollama/version.Version=0.40.14" -o /ollama .
FROM ${ALPINE_IMAGE}
RUN apk add --no-cache ca-certificates libstdc++ libgcc libgomp
COPY --from=build /ollama /usr/bin/ollama
COPY --from=build /src/dist/lib/ollama /usr/lib/ollama
COPY --from=build /src/LICENSE /usr/share/licenses/ollama/LICENSE
COPY --from=build /llama/LICENSE /usr/share/licenses/llama-cpp/LICENSE
ENV OLLAMA_HOST=0.0.0.0:11434 OLLAMA_MODELS=/models OLLAMA_NO_CLOUD=1 HOME=/tmp
USER 65534:65534
ENTRYPOINT ["/usr/bin/ollama"]
CMD ["serve"]
