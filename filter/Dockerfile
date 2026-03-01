# ── Stage 1: Build ──────────────────────────
FROM gcc:13-bookworm AS builder

RUN apt-get update && apt-get install -y cmake libssl-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .

RUN mkdir -p build && cd build \
    && cmake .. -DCMAKE_BUILD_TYPE=Release \
    && make -j$(nproc)

# ── Stage 2: Runtime ───────────────────────
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libssl3 libstdc++6 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /build/build/filter /app/filter

CMD ["/app/filter"]
