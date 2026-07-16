#!/usr/bin/env python3

# ─────────────────────────────────────────────────────────────────────────────
# Configuration — set via environment variables (see .env.example / docker-compose.yml)
# ─────────────────────────────────────────────────────────────────────────────

import os, sys

def _require_env(name):
    val = os.environ.get(name, "").strip()
    if not val:
        print(f"[photoframe] ERROR: required environment variable '{name}' is not set.", file=sys.stderr)
        sys.exit(1)
    return val

IMMICH_URL          = _require_env("IMMICH_URL").rstrip("/")
API_KEY             = _require_env("IMMICH_API_KEY")
HORIZONTAL_ALBUM_ID = _require_env("HORIZONTAL_ALBUM_ID")
VERTICAL_ALBUM_ID   = _require_env("VERTICAL_ALBUM_ID")
PORT                = int(os.environ.get("PORT", "8765"))

# ─────────────────────────────────────────────────────────────────────────────
# Display / overlay tuning (optional overrides)
# ─────────────────────────────────────────────────────────────────────────────

DATE_FORMAT      = os.environ.get("DATE_FORMAT", "%b %-d, %y")       # e.g. "Jul 4, 26"
OVERLAY_OPACITY  = int(os.environ.get("OVERLAY_OPACITY",  "40"))     # 0–255
TEXT_OPACITY     = int(os.environ.get("TEXT_OPACITY",     "180"))    # 0–255
FONT_SIZE_DIVISOR= int(os.environ.get("FONT_SIZE_DIVISOR","30"))     # larger = smaller font
MARGIN_DIVISOR   = int(os.environ.get("MARGIN_DIVISOR",   "70"))     # larger = smaller margin

# ─────────────────────────────────────────────────────────────────────────────

import io
import json
import random
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from PIL import Image, ImageDraw, ImageFont

# Target display dimensions — (width, height) — must match your device
DISPLAY_SIZES = {
    "/horizontal": (800, 480),
    "/vertical":   (480, 800),
}
ROUTES = {
    "/horizontal": HORIZONTAL_ALBUM_ID,
    "/vertical":   VERTICAL_ALBUM_ID,
}

def center_crop(img, target_w, target_h):
    """
    Scale the image so it fills the target dimensions (cover), then
    center-crop to exactly target_w x target_h — matching what the
    ESP32 firmware does internally. Doing it here means the overlay
    is placed on the final pixel grid, so nothing gets cut off.
    """
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    scaled_w = round(src_w * scale)
    scaled_h = round(src_h * scale)
    img = img.resize((scaled_w, scaled_h), Image.LANCZOS)
    left = (scaled_w - target_w) // 2
    top  = (scaled_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))

def immich_get(path):
    """Make an authenticated GET request to the Immich API."""
    req = urllib.request.Request(
        f"{IMMICH_URL}{path}",
        headers={"x-api-key": API_KEY, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())

def immich_post(path, body):
    """Make an authenticated POST request to the Immich API with a JSON body."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{IMMICH_URL}{path}",
        data=data,
        headers={
            "x-api-key": API_KEY,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())

def get_random_asset(album_id):
    """
    Return a random asset dict from the given album.

    Uses POST /api/search/metadata with albumId filter — required since
    Immich removed the 'assets' field from GET /api/albums/{id} in a
    recent update. We also include withExif so date parsing works
    without a second round-trip.
    """
    page = 1
    page_size = 1000

    data = immich_post("/api/search/metadata", {
        "albumIds": [album_id],
        "withExif": True,
        "page": page,
        "size": page_size,
    })

    assets = data.get("assets", {}).get("items", [])
    total  = data.get("assets", {}).get("total", 0)

    if not assets:
        raise ValueError(f"Album {album_id} has no assets")

    # If there are more assets than one page, randomly pick a later page too
    if total > page_size:
        total_pages = (total + page_size - 1) // page_size
        rand_page   = random.randint(1, total_pages)
        if rand_page != page:
            data2  = immich_post("/api/search/metadata", {
                "albumIds": [album_id],
                "withExif": True,
                "page": rand_page,
                "size": page_size,
            })
            assets = data2.get("assets", {}).get("items", []) or assets

    return random.choice(assets)

def fetch_image_bytes(asset_id):
    """Fetch the preview JPEG for an asset and return raw bytes."""
    url = f"{IMMICH_URL}/api/assets/{asset_id}/thumbnail?size=preview"
    req = urllib.request.Request(url, headers={"x-api-key": API_KEY})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()

def parse_date(asset):
    """Extract a date string from asset metadata, best field available."""
    # Prefer EXIF date, fall back to file creation date
    for field in ("exifInfo", ):
        exif = asset.get(field, {})
        if exif:
            for key in ("dateTimeOriginal", "modifyDate"):
                val = exif.get(key)
                if val:
                    try:
                        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                        return dt.strftime(DATE_FORMAT)
                    except ValueError:
                        pass
    for key in ("localDateTime", "fileCreatedAt", "fileModifiedAt", "createdAt"):
        val = asset.get(key)
        if val:
            try:
                dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                return dt.strftime(DATE_FORMAT)
            except ValueError:
                pass
    return None

def add_date_overlay(image_bytes, date_str, target_size=None):
    """
    Burn a subtle semi-transparent date label into the bottom-right corner.
    Returns JPEG bytes.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    if target_size:
        img = center_crop(img, *target_size)
    w, h = img.size

    short_side = min(w, h)
    font_size = max(12, short_side // FONT_SIZE_DIVISOR)
    margin    = max(8,  short_side // MARGIN_DIVISOR)

    # Try common system font locations (macOS, Debian/Ubuntu, FreeBSD/TrueNAS, Docker/Alpine)
    font_candidates = [
        "/System/Library/Fonts/Helvetica.ttc",                            # macOS
        "/System/Library/Fonts/Supplemental/Arial.ttf",                   # macOS
        "/Library/Fonts/Arial.ttf",                                       # macOS
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",                # Debian/Ubuntu
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",# RHEL/CentOS
        "/usr/local/share/fonts/dejavu/DejaVuSans.ttf",                   # FreeBSD/TrueNAS
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",                         # FreeBSD alt
        "/usr/share/fonts/dejavu-sans/DejaVuSans.ttf",                    # Alpine Linux (Docker)
    ]
    font = None
    for path in font_candidates:
        try:
            font = ImageFont.truetype(path, font_size)
            break
        except (IOError, OSError):
            continue
    if font is None:
        # Pillow 10.1+ supports size= on load_default; older versions ignore it
        try:
            font = ImageFont.load_default(size=font_size)
        except TypeError:
            font = ImageFont.load_default()

    # Measure text
    dummy = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bbox  = dummy.textbbox((0, 0), date_str, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    pad_x, pad_y = int(font_size * 0.5), int(font_size * 0.3)

    # Position: bottom-right
    bg_x1 = w - text_w - pad_x * 2 - margin
    bg_y1 = h - text_h - pad_y * 2 - margin
    bg_x2 = w - margin
    bg_y2 = h - margin

    # Semi-transparent dark pill behind the text
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)
    draw.rounded_rectangle(
        [bg_x1, bg_y1, bg_x2, bg_y2],
        radius=int(font_size * 0.4),
        fill=(0, 0, 0, OVERLAY_OPACITY),
    )
    # White text
    draw.text(
        (bg_x1 + pad_x, bg_y1 + pad_y),
        date_str,
        font=font,
        fill=(255, 255, 255, TEXT_OPACITY),
    )

    composited = Image.alpha_composite(img, overlay).convert("RGB")
    out = io.BytesIO()
    composited.save(out, format="JPEG", quality=92)
    return out.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Suppress default noisy access log; swap for `pass` if you want silence
        print(f"[photoframe] {self.address_string()} {fmt % args}")

    def do_GET(self):
        path = self.path.split("?")[0]   # ignore any query string

        if path not in ROUTES:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found. Try /horizontal or /vertical")
            return

        album_id = ROUTES[path]
        try:
            target_size = DISPLAY_SIZES[path]
            asset       = get_random_asset(album_id)
            image_bytes = fetch_image_bytes(asset["id"])
            date_str    = parse_date(asset)
            if date_str:
                image_bytes = add_date_overlay(image_bytes, date_str, target_size)
            else:
                # Still crop even if there's no date to overlay
                img = center_crop(
                    Image.open(io.BytesIO(image_bytes)).convert("RGB"),
                    *target_size,
                )
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=92)
                image_bytes = out.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(image_bytes)))
            self.end_headers()
            self.wfile.write(image_bytes)
        except Exception as e:
            print(f"[photoframe] ERROR: {e}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode())


if __name__ == "__main__":
    print(f"[photoframe] Listening on port {PORT}")
    print(f"[photoframe]  /horizontal → album {HORIZONTAL_ALBUM_ID}")
    print(f"[photoframe]  /vertical   → album {VERTICAL_ALBUM_ID}")
    HTTPServer(("", PORT), Handler).serve_forever()
