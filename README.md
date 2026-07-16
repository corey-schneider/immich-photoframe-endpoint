# immich-photoframe-endpoint

A tiny Python server that bridges [Immich](https://immich.app/) with an [ESP32 photoframe](https://github.com/aitjcize/esp32-photoframe) (or any device that fetches images from a URL). It picks a random photo from a specified album and serves it as a JPEG, with an optional subtle date overlay in the bottom corner.

_Tested and working with Immich v3.0.2+_

---

## Features

- Serves a random photo from any Immich album on each request
- Separate endpoints for horizontal and vertical albums - point each frame orientation at the right one
- Subtle date overlay (from EXIF or file metadata) burned into the bottom-right corner
- Auto-detects system fonts across macOS, Debian/Ubuntu, and FreeBSD/TrueNAS
- Pure stdlib except for [Pillow](https://python-pillow.org/) (required for the date overlay)
- Tiny footprint - idles at ~0% CPU, ~10MB RAM

---

## Requirements

- Python 3.8+
- Pillow

```
pip install pillow
```

> **macOS / externally-managed environments:** use a virtual environment:
> ```
> python3 -m venv ~/photoframe-venv
> ~/photoframe-venv/bin/pip install pillow
> ~/photoframe-venv/bin/python3 immich_photoframe.py
> ```

---

## Immich API Key Permissions

Create a dedicated API key in Immich (**Account Settings → API Keys**) with at minimum:

| Permission | Required for |
|---|---|
| `album.read` | Listing album contents |
| `asset.read` | Reading asset metadata (date, EXIF) |
| `asset.view` | Fetching the image preview |

> `asset.download` is **not** required. The script uses the preview endpoint (`/api/assets/{id}/thumbnail?size=preview`) which is covered by `asset.view`.

---

## Setup

1. **Clone the repo and open `immich_photoframe.py`**
2. **Fill in the config block at the top:**

```python
IMMICH_URL          = "http://<YOUR-IMMICH-SERVER>:2283"
API_KEY             = "YOUR_IMMICH_API_KEY"
HORIZONTAL_ALBUM_ID = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
VERTICAL_ALBUM_ID   = "yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy"
PORT                = 8765
```

To find an album ID, open the album in Immich - the UUID is in the URL bar.

3. **Run it:**

```
python3 immich_photoframe.py
```

---

## Endpoints

| URL | Returns |
|---|---|
| `http://your-server:8765/horizontal` | Random photo from the horizontal album |
| `http://your-server:8765/vertical` | Random photo from the vertical album |

Paste the applicable orientation endpoint directly into the ESP32 photoframe's **Auto Rotate URL** field in its web UI.

---

## Date Overlay

The overlay is configured via constants at the top of the script:

| Setting | Default | Description |
|---|---|---|
| `DATE_FORMAT` | `"%B %d, %Y"` | Date format string - e.g. `July 04, 2021`. Use `"%Y-%m-%d"` for compact ISO style. |
| `OVERLAY_OPACITY` | `20` | Background pill opacity (0–255) |
| `TEXT_OPACITY` | `120` | Text opacity (0–255) |
| `FONT_SIZE_DIVISOR` | `50` | Font size = image width ÷ this value. Lower = larger text. |
| `MARGIN_DIVISOR` | `70` | Corner margin = image width ÷ this value. |

The date is sourced from EXIF (`dateTimeOriginal`) when available, falling back to Immich's `localDateTime` and file creation fields. If no date can be found, the image is served without any overlay.

---

## Docker (recommended for TrueNAS SCALE / any container host)

A `Dockerfile` and `docker-compose.yml` are included.

### Build & run with Docker Compose

```bash
docker compose up -d --build
```

The container listens on port **8765** and restarts automatically unless stopped.

### Or build & run manually

```bash
docker build -t immich-photoframe .
docker run -d --name immich-photoframe --restart unless-stopped -p 8765:8765 immich-photoframe
```

### TrueNAS SCALE

1. Push the image to a registry (Docker Hub, GHCR, or your local one), **or** copy the `Dockerfile` + `immich_photoframe.py` to a dataset and build there.
2. In TrueNAS SCALE, add the container via **Apps → Custom App** (or Docker Compose via the shell), mapping host port 8765 → container port 8765.
3. Set **Restart Policy** to `Unless Stopped`.

---

## Testing with Postman

Point Postman at your running server to verify each endpoint without rebooting drives:

| Method | URL | Expected response |
|--------|-----|-------------------|
| `GET` | `http://<server>:8765/horizontal` | `200 image/jpeg` — a random cropped photo |
| `GET` | `http://<server>:8765/vertical` | `200 image/jpeg` — a random cropped photo |
| `GET` | `http://<server>:8765/anything` | `404` — "Not found. Try /horizontal or /vertical" |

**To test the Immich API directly** (bypasses the Python layer entirely):

```
POST http://192.168.1.200:2283/api/search/metadata
x-api-key: <your-key>
Content-Type: application/json

{
  "albumIds": ["xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"],
  "page": 1,
  "size": 5
}
```

You should get a JSON response with `assets.total > 0` and a populated `assets.items` array. If that works but `/horizontal` still returns an error, the issue is in the Python layer (check logs).

---

### TrueNAS Community Edition (bare-metal / venv)

Put the script and venv on a **dataset** (not the boot pool - it gets wiped on updates):

```
python3 -m venv /mnt/APP_POOL/apps/photoframe-venv
/mnt/APP_POOL/apps/photoframe-venv/bin/pip install pillow
```

1. Find your Python version (probably 3.11)
`python3 -c 'import sys; print(sys.version_info[:2])'`

2. Download the virtualenv zipapp (replace 3.11 if yours differs)
```
cd /mnt/APP_POOL/apps
sudo wget "https://bootstrap.pypa.io/virtualenv/3.11/virtualenv.pyz"
```

3. Create the venv using it (this one comes with pip built in)
`sudo python3 virtualenv.pyz photoframe-venv`

4. Install Pillow
`sudo photoframe-venv/bin/pip install pillow`

5. Sanity check
`sudo photoframe-venv/bin/python3 -c "from PIL import Image; print('Pillow OK')"`


6. Add to **System → Advanced → Init/Shutdown Scripts** as a **Post Init** command:

```
/mnt/APP_POOL/apps/photoframe-venv/bin/python3 /mnt/APP_POOL/apps/immich_photoframe.py &
```

The `&` is important - it runs the server in the background so the init script doesn't hang.

---

## ESP32 Photoframe Configuration

This script is designed to work with the [aitjcize/esp32-photoframe](https://github.com/aitjcize/esp32-photoframe) firmware. In the photoframe web UI:

1. Go to **Settings → Auto Rotate**
2. Set the source to **URL**
3. Paste your horizontal or vertical endpoint URL
4. Set your desired rotation interval

Since horizontal and vertical photos are in separate albums, you configure each physical frame independently - no logic or switching needed in the script.
