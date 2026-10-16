# Reference Tier S forecaster image (RUN-01). Built once per season, pinned by digest in
# rounds/<season>/season.json. CUDA build of llama-cpp-python; the host passes --gpus.
#
#   docker build -t fc-forecaster:2026.10 -f runner/forecast/Dockerfile .
#   docker image inspect --format '{{index .RepoDigests 0}}' fc-forecaster:2026.10   # after push
#
# CPU-only variant: build with --build-arg BASE=python:3.12-slim --build-arg CMAKE_ARGS="".
ARG BASE=nvidia/cuda:12.4.1-runtime-ubuntu22.04
FROM ${BASE}
ARG CMAKE_ARGS="-DGGML_CUDA=on"
ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip python3-venv \
    build-essential cmake git && rm -rf /var/lib/apt/lists/*
WORKDIR /hub
COPY pyproject.toml README.md ./
COPY fc ./fc
RUN python3 -m pip install --upgrade pip && python3 -m pip install . \
    && CMAKE_ARGS="${CMAKE_ARGS}" python3 -m pip install "llama-cpp-python>=0.3,<0.4"
COPY runner/forecast/forecast.py /hub/forecast.py
# Non-root; no shell needed at runtime. The host mounts /model /packet /questions /prompt /out.
RUN useradd -m -u 10001 forecaster && mkdir -p /out && chown forecaster /out
USER forecaster
ENTRYPOINT ["python3", "/hub/forecast.py"]
