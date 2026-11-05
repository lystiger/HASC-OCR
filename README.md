# HASC Production Records (Hồ sơ sản xuất HASC)

Production records digitization system for HASC VN. Operators upload production sheet photos, an asynchronous worker processes them via OCR, and office members review and confirm the extracted data in Excel format.

## Setup

Requires Python 3.12+ (or active virtual environment).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## Creating the First Admin Account

Use the CLI script `scripts/create_user.py` to create the initial admin user:

```bash
python3 scripts/create_user.py --username admin --role admin
```

- When `--password` is omitted, a random temporary password is generated and displayed. The admin must change this password upon first login.
- To set a specific permanent password directly:

```bash
python3 scripts/create_user.py --username admin --role admin --password "your-secure-password" --no-temporary
```

Optional arguments:
- `--db <path>`: Path to SQLite database file (defaults to `HASC_DB_PATH` or `./hasc.db`).
- Roles available: `operator`, `employee`, `boss`, `admin`.

## Running the Website Locally

The website is a FastAPI application rendered with server-side templates and htmx.

1. Set the database path and optional photo directory:

```bash
export HASC_DB_PATH="hasc.db"
# Optional photo folder setting (defaults to photos/ beside the database, or ~/.local/share/hasc/photos):
export HASC_PHOTOS_DIR="$HOME/.local/share/hasc/photos"
```

The photo folder setting `HASC_PHOTOS_DIR` specifies where uploaded photos are saved. When unset, photos default to a `photos/` folder alongside `HASC_DB_PATH`, or `~/.local/share/hasc/photos`.

2. Start the web server with Uvicorn:

```bash
uvicorn hasc.web:create_app --factory --host 127.0.0.1 --port 8000
```

3. Open your browser:
- Login: [http://localhost:8000/login](http://localhost:8000/login)
- Operator upload screen: [http://localhost:8000/operator](http://localhost:8000/operator)
- Office review screen: [http://localhost:8000/office](http://localhost:8000/office)

## Running the Worker Locally

The worker runs as a separate background process, reading photos from the queue and querying the cloud reader.

### Worker Environment File (Kept Outside the Repository)

To keep API keys and proprietary names outside git, configure worker settings in an env file stored outside the repository (for example at `~/.config/hasc/worker.env`), setting `HASC_READER_MODEL=google/gemini-3.8-flash` and optionally `HASC_READER_FALLBACK=qwen/qwen3.8-27b`:

```ini
OPENROUTER_API_KEY=your_openrouter_api_key_here
HASC_READER_MODEL=google/gemini-3.8-flash
HASC_KNOWN_NAMES=/absolute/path/outside/repo/known_names.json
# Optional fallback model:
# HASC_READER_FALLBACK=qwen/qwen3.8-27b
```

### Starting the Worker

Set `HASC_WORKER_ENV` pointing to your external env file, then start the worker:

```bash
export HASC_WORKER_ENV="$HOME/.config/hasc/worker.env"
# Optional if database or photo directory are not in default locations:
# export HASC_DB_PATH="hasc.db"
# export HASC_PHOTOS_DIR="$HOME/.local/share/hasc/photos"

python3 -m hasc.worker
```

To run the worker once (process all currently queued jobs until empty, then exit):

```bash
python3 -m hasc.worker --once
```

## Daily Report (Báo cáo sản xuất ngày)

The daily report aggregates production sheets across Ca 1 and Ca 2 for a given date into an Excel spreadsheet (form `HASC281224`).

- **Endpoint**: `GET /api/days/{production_date}/report` (date format `YYYY-MM-DD`, e.g. `/api/days/2026-11-04/report`).
- **File Name**: Downloaded as `HASC_BaoCaoNgay_{YYYY-MM-DD}.xlsx` (e.g. `HASC_BaoCaoNgay_2026-11-04.xlsx`).
- **Office Date Picker**: On the office review screen (`/office`), a date input labeled "Báo cáo ngày:" defaults to yesterday's date. Selecting a date updates the download link to fetch that day's report.
- **Who Can View**: Restricted to `employee`, `boss`, and `admin` roles. The `operator` role is forbidden (HTTP 403). Unauthenticated requests return HTTP 401.
- **Ghi chú `Chưa xác nhận`**: The report summarizes production lines by machine, customer, film type, and product code. If any sheet contributing to a summarized line has not yet been reviewed and confirmed by the office (`status != "reviewed"`), that line's Ghi chú column is marked `Chưa xác nhận`. When all contributing sheets are reviewed, the note remains blank. Waste, pending goods, and signature columns are left blank for manual office entry.

## Running Checks

Run the automated checks (runs the import-boundary check and full unittest suite with no network or secrets):

```bash
./scripts/check
```

## Setting Up a Server Machine

Everything needed to run the website and the worker on a dedicated machine (for example the office Dell) that the phones reach over the local network. Steps run as the user that will own the service; `~` is that user's home.

### 1. Install the code

Needs `git`, Python 3.12+ (developed on 3.14) and `sqlite3` (only for moving existing data). The repository is private, so the machine needs GitHub access (an SSH key or `gh auth login`).

```bash
git clone https://github.com/lystiger/HASC-OCR.git ~/HASC-OCR
cd ~/HASC-OCR
git checkout v1.0.0
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
./scripts/check
```

### 2. Files kept outside the repository

| Path | Holds |
|---|---|
| `~/.local/share/hasc-ocr/hasc.db` | The database. Tables are created and migrated when the website or worker starts. |
| `~/.local/share/hasc-ocr/photos/` | Uploaded photos (the default: `photos/` beside the database). |
| `~/.config/hasc-ocr/worker.env` | Worker settings, including the OpenRouter API key. |
| `~/.config/hasc-ocr/known_names.json` | Customer and product names the reader may use (`HASC_KNOWN_NAMES`). |

```bash
mkdir -p ~/.local/share/hasc-ocr/photos ~/.config/hasc-ocr
```

`~/.config/hasc-ocr/worker.env` (paths must be absolute):

```ini
OPENROUTER_API_KEY=your_openrouter_api_key_here
HASC_READER_MODEL=google/gemini-3.8-flash
HASC_READER_FALLBACK=qwen/qwen3.8-27b
HASC_KNOWN_NAMES=/home/USER/.config/hasc-ocr/known_names.json
```

```bash
chmod 600 ~/.config/hasc-ocr/worker.env
```

Copy the API key and `known_names.json` by hand (USB stick or `scp`). Never commit them. `GEMINI_API_KEY` is not used by the code.

### 3. Data: start fresh, or bring the existing data

**Fresh:** create the first admin (see [Creating the First Admin Account](#creating-the-first-admin-account)):

```bash
HASC_DB_PATH=~/.local/share/hasc-ocr/hasc.db .venv/bin/python scripts/create_user.py --username admin --role admin
```

**Move from another machine:**

1. On the old machine, stop the website and the worker so nothing writes while you copy.
2. Take a consistent copy of the database (this also folds in the `hasc.db-wal` file):

   ```bash
   sqlite3 ~/.local/share/hasc-ocr-dev/hasc.db ".backup /tmp/hasc.db"
   ```

3. Copy `/tmp/hasc.db` to `~/.local/share/hasc-ocr/hasc.db` on the new machine, and the old `photos/` folder's files into `~/.local/share/hasc-ocr/photos/`.
4. The database stores absolute photo paths. If the photo folder path differs, rewrite them (replace both paths with the real ones, each ending in `/`):

   ```bash
   sqlite3 ~/.local/share/hasc-ocr/hasc.db "
     UPDATE photos SET
       path = replace(path, '/home/OLD/.local/share/hasc-ocr-dev/photos/', '/home/NEW/.local/share/hasc-ocr/photos/'),
       small_copy_path = replace(small_copy_path, '/home/OLD/.local/share/hasc-ocr-dev/photos/', '/home/NEW/.local/share/hasc-ocr/photos/');
     SELECT path FROM photos LIMIT 3;"
   ```

### 4. Run the website and the worker as services

`~/.config/systemd/user/hasc-web.service`:

```ini
[Unit]
Description=HASC website
After=network-online.target

[Service]
WorkingDirectory=%h/HASC-OCR
Environment=HASC_DB_PATH=%h/.local/share/hasc-ocr/hasc.db
ExecStart=%h/HASC-OCR/.venv/bin/uvicorn hasc.web:create_app --factory --host 0.0.0.0 --port 8080
Restart=on-failure

[Install]
WantedBy=default.target
```

`~/.config/systemd/user/hasc-worker.service`:

```ini
[Unit]
Description=HASC photo reader worker
After=network-online.target

[Service]
WorkingDirectory=%h/HASC-OCR
Environment=HASC_DB_PATH=%h/.local/share/hasc-ocr/hasc.db
Environment=HASC_WORKER_ENV=%h/.config/hasc-ocr/worker.env
ExecStart=%h/HASC-OCR/.venv/bin/python -u -m hasc.worker
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now hasc-web hasc-worker
# Keep the services running when nobody is logged in, and start them at boot:
sudo loginctl enable-linger "$USER"
```

### 5. Network

- Give the machine a fixed address on the office network (a DHCP reservation in the router).
- Open port 8080 to the local network only. With `ufw`:

  ```bash
  sudo ufw allow from 192.168.1.0/24 to any port 8080 proto tcp
  ```

- Phones open `http://<machine-ip>:8080/login`.
- The site is plain HTTP: passwords and photos cross the office network unencrypted. Keep it on the local network and never forward port 8080 from the router to the internet.

### 6. Check it works

```bash
systemctl --user status hasc-web hasc-worker
journalctl --user -u hasc-worker -f
```

Log in from a phone, upload one test photo, and watch the worker read it. Then stop the website and worker on the old machine, so no phone keeps using it.

### Updating

```bash
cd ~/HASC-OCR
git fetch --tags
git checkout <new-tag>
.venv/bin/pip install -r requirements.txt
./scripts/check
systemctl --user restart hasc-web hasc-worker
```
