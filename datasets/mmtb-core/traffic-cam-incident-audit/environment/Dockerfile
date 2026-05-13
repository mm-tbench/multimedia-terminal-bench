FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl && \
    rm -rf /var/lib/apt/lists/*

# Pre-install whisper + tesseract so agents don't burn budget on heavy
# CUDA-laden pip installs that don't fit on 2 CPUs / 4 GB RAM.
RUN pip install --no-cache-dir huggingface-hub openai-whisper && \
    apt-get update && apt-get install -y --no-install-recommends tesseract-ocr libtesseract-dev && \
    pip install --no-cache-dir pytesseract opencv-python-headless && \
    rm -rf /var/lib/apt/lists/*

# Pre-cache whisper-tiny model so first invocation doesn't pay download time.
RUN python -c "import whisper; whisper.load_model('tiny')"

WORKDIR /app

COPY . /tmp/build/

ARG HF_TOKEN=""
ENV HF_TOKEN=${HF_TOKEN}

RUN \
    if [ -d /tmp/build/assets ]; then \
        mkdir -p /app/assets && cp -r /tmp/build/assets/. /app/assets/; \
    fi && \
    python /tmp/build/fetch_media.py /tmp/build/media.toml \
        --task-name traffic-cam-incident-audit \
        --output-dir /app/assets && \
    rm -rf /tmp/build

RUN mv /app/assets/videos /app/videos && \
    mv /app/assets/dispatch_audio /app/dispatch_audio && \
    rmdir /app/assets 2>/dev/null || true && \
    mkdir -p /app/submission
