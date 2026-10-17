# Reference Tier S forecaster image (RUN-01). Built once per season, pinned by digest in
# rounds/<season>/season.json (runner_image_digest). Two stages: a CUDA "devel" stage compiles
# llama-cpp-python into a venv, and a CUDA "runtime" stage copies only that venv. The runtime
# image has no compiler toolchain and runs as a non-root user. The host passes --gpus.
#
#   docker build -t fc-forecaster:2026.10 -f runner/forecast/Dockerfile .
#   docker image inspect --format '{{index .RepoDigests 0}}' fc-forecaster:2026.10   # after push
#
# CPU-only variant (the same base in both stages keeps the venv interpreter path identical):
#   docker build -t fc-forecaster:cpu -f runner/forecast/Dockerfile \
#     --build-arg BUILD_BASE=ubuntu:24.04 --build-arg RUNTIME_BASE=ubuntu:24.04 \
#     --build-arg CMAKE_ARGS="-DGGML_NATIVE=off" .
#
# The container runs with --read-only and a /tmp tmpfs (fc/run.py docker_cmd), so nothing
# writes into the venv at runtime. CUDA 12.6 runs on the host driver (it reports CUDA 13.3).
ARG BUILD_BASE=nvidia/cuda:12.6.3-devel-ubuntu24.04
ARG RUNTIME_BASE=nvidia/cuda:12.6.3-runtime-ubuntu24.04

# --------------------------------------------------------------------------- build
FROM ${BUILD_BASE} AS build
# 0.3.35 is the first llama-cpp-python release verified to load Qwen3.5 GGUFs (GPU box,
# 2026-10-16). Bumping it changes the image digest pinned in
# rounds/2026-us-midterms/season.json runner_image_digest.
ARG LLAMA_CPP_VERSION=0.3.35
# `docker build` sees no GPU, so llama.cpp's "native" architecture detection fails: name the
# architectures explicitly. 86 is Ampere, 89 is Ada (the RTX 4070 SUPER).
ARG CMAKE_ARGS="-DGGML_CUDA=on -DGGML_NATIVE=off -DCMAKE_CUDA_ARCHITECTURES=86;89"
ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1
# Ubuntu 24.04's python3 is 3.12, which satisfies requires-python >= 3.11 (datetime.UTC).
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv \
    python3-dev build-essential cmake git && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
WORKDIR /src
COPY pyproject.toml README.md ./
COPY fc ./fc
RUN /opt/venv/bin/pip install /src \
    && CMAKE_ARGS="${CMAKE_ARGS}" /opt/venv/bin/pip install "llama-cpp-python==${LLAMA_CPP_VERSION}"

# --------------------------------------------------------------------------- runtime
FROM ${RUNTIME_BASE}
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PATH=/opt/venv/bin:$PATH
# Only the interpreter (same /usr/bin/python3.12 path as the build stage) and the OpenMP
# runtime llama.cpp links against; no compiler toolchain.
RUN apt-get update && apt-get install -y --no-install-recommends python3 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/venv /opt/venv
WORKDIR /hub
COPY runner/forecast/forecast.py /hub/forecast.py
# Non-root; no shell needed at runtime. The host mounts /model /packet /questions /prompt /out.
# (Ubuntu 24.04 images already own uid 1000, hence 10001.)
RUN useradd -m -u 10001 forecaster && mkdir -p /out && chown forecaster /out
USER forecaster
ENTRYPOINT ["/opt/venv/bin/python", "/hub/forecast.py"]
