FROM python:3.12-alpine

# Install Pillow's native deps + DejaVu font (for date overlay)
RUN apk add --no-cache \
        jpeg-dev \
        zlib-dev \
        freetype-dev \
        libpng-dev \
        font-dejavu \
    && pip install --no-cache-dir pillow

WORKDIR /app
COPY immich_photoframe.py .

# Run as non-root
RUN adduser -D photoframe
USER photoframe

EXPOSE 8765
CMD ["python", "immich_photoframe.py"]
