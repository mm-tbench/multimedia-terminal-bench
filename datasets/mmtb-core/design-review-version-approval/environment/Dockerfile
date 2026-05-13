FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir huggingface-hub

WORKDIR /app

COPY . /tmp/build/

ARG HF_TOKEN=""
ENV HF_TOKEN=${HF_TOKEN}

RUN \
    if [ -d /tmp/build/assets ]; then \
        mkdir -p /app && cp -r /tmp/build/assets/. /app/; \
    fi && \
    python /tmp/build/fetch_media.py /tmp/build/media.toml \
        --task-name design-review-version-approval \
        --output-dir /app && \
    rm -rf /tmp/build
