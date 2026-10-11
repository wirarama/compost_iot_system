# The emulated CompostIQ device. Standard library only, so the image is just
# Python on Alpine plus a few small files.
#
# Build from the repo root, because the stage table is shared with the
# simulator (compose.yaml in this folder already does this):
#   docker build -f emulator/Dockerfile -t compostiq-emulator .
FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEVICE_HOST=0.0.0.0 \
    DEVICE_PORT=8080 \
    STATE_FILE=/data/device.json

RUN adduser -D -H -u 10001 device && mkdir /data && chown device /data

WORKDIR /app
COPY simulator/composting_stages.py ./
COPY emulator/*.py ./
COPY emulator/static ./static

USER device
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3)"

CMD ["python", "main.py"]
