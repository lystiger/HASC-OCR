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

1. Set the database path (optional, defaults to `./hasc.db`):

```bash
export HASC_DB_PATH="hasc.db"
```

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

To keep API keys and proprietary names outside git, configure worker settings in an env file stored outside the repository (for example at `~/.config/hasc/worker.env`):

```ini
OPENROUTER_API_KEY=your_openrouter_api_key_here
HASC_READER_MODEL=google/gemini-2.5-flash
HASC_KNOWN_NAMES=/absolute/path/outside/repo/known_names.json
# Optional fallback model:
# HASC_READER_FALLBACK=google/gemini-2.5-pro
```

### Starting the Worker

Set `HASC_WORKER_ENV` pointing to your external env file, then start the worker:

```bash
export HASC_WORKER_ENV="$HOME/.config/hasc/worker.env"
# Optional if database is not in the default location:
# export HASC_DB_PATH="hasc.db"

python3 -m hasc.worker
```

To run the worker once (process all currently queued jobs until empty, then exit):

```bash
python3 -m hasc.worker --once
```

## Running Checks

Run the automated checks (runs the import-boundary check and full unittest suite with no network or secrets):

```bash
./scripts/check
```
