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

## Setting Up a Linux Server (the Dell spare)

Production runs on the Windows office PC (see [Setting Up the Windows Production PC](#setting-up-the-windows-production-pc)). The Dell is the cold spare: restore the latest backup onto it if the PC fails, and use it to test restores. These steps set it up. Steps run as the user that will own the service; `~` is that user's home.

### 1. Install the code

Needs `git`, Python 3.12+ (developed on 3.14) and `sqlite3` (only for moving existing data). The repository is private, so the machine needs GitHub access (an SSH key or `gh auth login`).

```bash
git clone https://github.com/lystiger/HASC-OCR.git ~/HASC-OCR
cd ~/HASC-OCR
git checkout v1.0.1
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

## Setting Up the Windows Production PC

The office PC (Intel i3-8100, 8 GB RAM, Windows) hosts the website and the worker for the factory. Office staff also use this PC, so HASC runs as its own Windows account, `hasc`, and its data is encrypted to that account with EFS: staff logged in as themselves cannot open the database or photos. Run the commands below in **PowerShell as Administrator** unless a step says otherwise.

### Quick install: the bundle (recommended)

Each release has a zip, `hasc-<version>-win64.zip`, on the GitHub **Releases** page. It holds the code, a portable Python with every library installed, `nssm.exe`, and three tools: `install.cmd`, `update.cmd` and `delete.cmd`. The PC needs no git, no Python installer and no `pip`. Before a bundle is published, CI runs its tools without the windows (with parameters instead) on a clean Windows machine: install, update from the local zip, delete keeping the data, reinstall from that data, and delete erasing it. CI does not test the download from GitHub or the windows themselves.

Each tool is a double-click: Windows asks to allow it to make changes (choose **Yes**), and small windows ask the questions.

**Install**

1. Copy the zip and `known_names.json` to the PC (and, to bring existing data, the `hasc.db` backup with its `photos` folder next to it).
2. Unzip it anywhere except `C:\HASC` (for example `Downloads\hasc-bundle`) and double-click `install.cmd`.
3. Answer the windows: a password for the `hasc` Windows account (at least 8 characters; keep it, updates ask for it), `known_names.json`, the OpenRouter key, existing data or a fresh start, and the office network. A fresh start shows the admin's first password once: write it down.
4. On Windows Pro a black window opens as `hasc` to back up the encryption key: type a password for the key file there. Then copy the selected `hasc-efs-key.pfx` to a USB stick before pressing OK.
5. The website opens when it is done. The last window lists the phone addresses, where each device gets the certificate (see HTTPS below), and what to do by hand: BIOS power-on after power loss, a fixed address in the router, Windows Update paused.

The installer adds **HASC website** and **HASC update** to the desktop, and both plus **HASC delete** to the Start menu.

**HTTPS: trusting the website once on each device**

The website runs on HTTPS at `https://<PC-IP>/login`. The installer makes its own certificate authority, `HASC local CA`, and trusts it on the office PC itself. Every other device trusts it once; until then the browser shows a security warning. Plain `http://<PC-IP>:8080/...` addresses keep working: they send the browser on to HTTPS.

- **Windows PC or laptop:** open `http://<PC-IP>:8080/ca.crt`, open the downloaded `hasc-ca.crt`, choose **Install Certificate**, **Local Machine**, **Place all certificates in the following store**, **Trusted Root Certification Authorities**, then **Finish**. Restart the browser.
- **Android:** open `http://<PC-IP>:8080/ca.crt` in Chrome. Then **Settings**, **Security** (or **Security & privacy**), **More security settings**, **Encryption & credentials**, **Install a certificate**, **CA certificate**, **Install anyway**, and pick `hasc-ca.crt`. The menu names differ a little between phone makers.
- **iPhone:** open `http://<PC-IP>:8080/ca.crt` in Safari and allow the download. Then **Settings**, **Profile Downloaded**, **Install**. Then **Settings**, **General**, **About**, **Certificate Trust Settings**, and turn on **HASC local CA**.

The certificate names the PC's addresses at the time of the install or update, so give the PC a fixed address in every router it serves (the office and the factory Wi-Fi). If an address changes, run the update again (or `install.cmd`): it makes a new certificate for the new address, and phones keep trusting it, since the authority stays the same. Delete removes the authority from the PC; a reinstall makes a new one, which each device trusts again.

**Update:** double-click **HASC update**. It asks GitHub for the latest release, says what will change, downloads the bundle, checks it against the checksum published with it, and installs it, keeping the data, settings and firewall rule. Before the new code starts, the database is copied to `C:\HASC\data\backups\before-<date>` (the last 5 are kept). It asks for the `hasc` password. The first time it asks for a read-only GitHub token (github.com, Settings, Developer settings, Fine-grained tokens: repository HASC-OCR only, Contents read-only) and keeps it encrypted to your Windows user (each administrator who runs the update has their own). Without internet, unzip a newer bundle and double-click its `install.cmd` instead.

**Delete:** **HASC delete** in the Start menu (or `C:\HASC\tools\delete.cmd`). It asks whether to keep the database and photos: kept, they are moved, readable by administrators only, to `C:\HASC-backup-<date>`, and `install.cmd` can bring them back; otherwise they are erased. Keeping needs the `hasc` account: if it is already gone, delete stops and asks you to import the EFS key backup first. Everything else goes: the services, the firewall rule, `C:\HASC`, the shortcuts, and the `hasc` account with its profile and encryption key (the profile at the next start, by a task that retries at each start until it succeeds, if Windows still holds it).

**Warning:** never reset the `hasc` password from another account. Windows then throws away the encryption keys and the data can no longer be read.

The numbered steps below do the same by hand, for when the installer cannot be used.

### 1. Check the Windows edition

```powershell
(Get-CimInstance Win32_OperatingSystem).Caption
```

- **Pro** (or Enterprise, Education): follow every step.
- **Home:** EFS is not available. In step 5, open the `hasc` window but skip the `cipher` commands. The folder permissions in step 3 still keep out anyone who logs in with a **standard** account, so give office staff standard accounts, not administrator ones.

An unactivated Windows runs all of this; activation only unlocks personalization settings. Windows 10 gets no more security updates (support ended in October 2025), so the firewall rule in step 9 matters: keep the PC reachable only from the office network.

### 2. Install the tools

Download these in a browser and run them as Administrator:

1. **Python 3.12.10:** <https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe>. Choose **Customize installation** and tick **Install Python 3.12 for all users** and **Add Python to environment variables**. It must install under `C:\Program Files\Python312`: a Python installed only in the Administrator's profile cannot be used by the `hasc` service. Or run it silently from the Downloads folder:

   ```powershell
   .\python-3.12.10-amd64.exe /quiet InstallAllUsers=1 PrependPath=1 Include_launcher=1
   ```

2. **Git for Windows:** <https://git-scm.com/download/win>, default options. The repository is private: the first `git clone` opens a browser to sign in to GitHub.
3. **NSSM:** <https://nssm.cc/ci/nssm-2.24-101-g897c7ad.zip> (this pre-release build fixes services failing to start on Windows 10; the older 2.24 has that bug). Unzip it and copy `win64\nssm.exe` to `C:\Windows\System32`.

Close and reopen PowerShell, then check:

```powershell
py -3.12 --version
git --version
nssm version
```

### 3. Create the `hasc` account and the folders

```powershell
net user hasc * /add          # type a strong password; keep it in your password manager
mkdir C:\HASC\data\photos, C:\HASC\config, C:\HASC\logs
icacls C:\HASC\data   /inheritance:r /grant:r "hasc:(OI)(CI)F" "Administrators:(OI)(CI)F"
icacls C:\HASC\config /inheritance:r /grant:r "hasc:(OI)(CI)F" "Administrators:(OI)(CI)F"
icacls C:\HASC\logs   /inheritance:r /grant:r "hasc:(OI)(CI)F" "Administrators:(OI)(CI)F"
```

Never reset the `hasc` password from another account (`net user hasc newpass`): Windows then throws away the EFS keys and the data can no longer be read. Change it only while logged in as `hasc`.

### 4. Install the code

```powershell
git clone https://github.com/lystiger/HASC-OCR.git C:\HASC\HASC-OCR
cd C:\HASC\HASC-OCR
git checkout v1.0.1
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m unittest discover -s tests
# hasc may read and run the code, but not change it; other accounts get nothing:
icacls C:\HASC\HASC-OCR /inheritance:r /grant:r "hasc:(OI)(CI)RX" "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F"
```

`v1.0.1` is the first release that runs on Windows.

### 5. Encrypt the data folders to `hasc`, and back up the key

Every step that creates or copies files in `C:\HASC\data` or `C:\HASC\config` must run **as `hasc`**, so the files are encrypted to that account. Open a command prompt as `hasc` and keep it for steps 5–7:

```powershell
runas /user:hasc cmd
```

In that `hasc` window:

```bat
cipher /e /s:C:\HASC\data
cipher /e /s:C:\HASC\config
cipher /x C:\HASC\logs\hasc-efs-key
```

`cipher /x` asks for a password and writes `C:\HASC\logs\hasc-efs-key.pfx`. Copy that file to a USB stick kept away from the PC, then delete it from the PC. Without it, a broken `hasc` profile means the data is lost.

A file that another account (such as Administrator) creates or copies into `C:\HASC\data` or `C:\HASC\config` is encrypted to that account, and the service cannot read it. `cipher /c <file>` lists who can open a file; it must show `hasc`.

### 6. Worker settings

Still in the `hasc` window, create `C:\HASC\config\worker.env` (for example with `notepad C:\HASC\config\worker.env`), and copy `known_names.json` into `C:\HASC\config\` by hand:

```ini
OPENROUTER_API_KEY=your_openrouter_api_key_here
HASC_READER_MODEL=google/gemini-3.8-flash
HASC_READER_FALLBACK=qwen/qwen3.8-27b
HASC_KNOWN_NAMES=C:\HASC\config\known_names.json
```

### 7. Data: start fresh, or bring the existing data

Still in the `hasc` window.

**Fresh:**

```bat
set HASC_DB_PATH=C:\HASC\data\hasc.db
C:\HASC\HASC-OCR\.venv\Scripts\python C:\HASC\HASC-OCR\scripts\create_user.py --username admin --role admin
```

**Move from another machine:** take the `.backup` copy on the old machine as in the Linux section, step 3, copy `hasc.db` into `C:\HASC\data\` and the photos into `C:\HASC\data\photos\` (from the `hasc` window, so they are encrypted), then point the stored paths at the new folder. Replace `OLD` with the old machine's user name; this uses the venv's Python, so no extra tool is needed:

```bat
C:\HASC\HASC-OCR\.venv\Scripts\python -c "import sqlite3; c = sqlite3.connect(r'C:\HASC\data\hasc.db'); old = '/home/OLD/.local/share/hasc-ocr-dev/photos/'; new = 'C:\\HASC\\data\\photos\\'; fix = lambda p: p and p.replace(old, new).replace('/', '\\'); [c.execute('UPDATE photos SET path = ?, small_copy_path = ? WHERE id = ?', (fix(p), fix(s), i)) for i, p, s in c.execute('SELECT id, path, small_copy_path FROM photos').fetchall()]; c.commit(); print(c.execute('SELECT path FROM photos LIMIT 3').fetchall())"
```

Close the `hasc` window.

### 8. Run the website and the worker as services

Back in PowerShell as Administrator. Replace `HASC_PASSWORD` with the `hasc` password, in single quotes (`'...'`) so PowerShell does not change characters such as `$`, `&` or `;`:

```powershell
nssm install hasc-web C:\HASC\HASC-OCR\.venv\Scripts\python.exe "-m uvicorn hasc.web:create_app --factory --host 0.0.0.0 --port 8080"
nssm set hasc-web AppDirectory C:\HASC\HASC-OCR
nssm set hasc-web AppEnvironmentExtra HASC_DB_PATH=C:\HASC\data\hasc.db
nssm set hasc-web ObjectName .\hasc HASC_PASSWORD
nssm set hasc-web Start SERVICE_AUTO_START
nssm set hasc-web AppStdout C:\HASC\logs\web.log
nssm set hasc-web AppStderr C:\HASC\logs\web.log

nssm install hasc-worker C:\HASC\HASC-OCR\.venv\Scripts\python.exe "-u -m hasc.worker"
nssm set hasc-worker AppDirectory C:\HASC\HASC-OCR
nssm set hasc-worker AppEnvironmentExtra HASC_DB_PATH=C:\HASC\data\hasc.db HASC_WORKER_ENV=C:\HASC\config\worker.env
nssm set hasc-worker ObjectName .\hasc HASC_PASSWORD
nssm set hasc-worker Start SERVICE_AUTO_START
nssm set hasc-worker AppStdout C:\HASC\logs\worker.log
nssm set hasc-worker AppStderr C:\HASC\logs\worker.log

nssm start hasc-web
nssm start hasc-worker
```

NSSM restarts either service if it stops. If a service will not start with error 1069, give `hasc` the "Log on as a service" right: `secpol.msc`, Local Policies, User Rights Assignment, Log on as a service, add `hasc`.

### 9. Keep it running

- **Network port:** open 8080 to the office network only.

  ```powershell
  New-NetFirewallRule -DisplayName "HASC website" -Direction Inbound -Protocol TCP -LocalPort 8080 -RemoteAddress 192.168.1.0/24 -Profile Private -Action Allow
  ```

  Change `192.168.1.0/24` if the office network uses other addresses (`ipconfig` shows them). The office network must be set to **Private** in Windows network settings, or the rule does not apply.
- **Never sleep:**

  ```powershell
  powercfg /change standby-timeout-ac 0
  powercfg /change hibernate-timeout-ac 0
  ```

- **After a power cut:** in the BIOS, set "Restore on AC power loss" (sometimes "AC Recovery") to **Power On**. The services start at boot, before anyone logs in, so nothing needs to be started by hand.
- **Windows Update:** the shifts cover all 24 hours, longer than Windows' 18-hour active hours, so updates could restart the PC during a shift. Pause updates (Settings, Windows Update, Pause), install them during a planned shutdown, then pause again.
- **Fixed address:** give the PC a DHCP reservation in the router, so phones keep reaching `http://<pc-ip>:8080/login`.
- The site is plain HTTP until 1.1.0 adds HTTPS: keep it on the office network and never forward port 8080 from the router to the internet.

### 10. Check it works

```powershell
nssm status hasc-web
nssm status hasc-worker
Get-Content C:\HASC\logs\worker.log -Tail 20 -Wait
```

Log in from a phone, upload one test photo, and watch the worker read it.

### Updating

There is no separate staging machine: CI runs every PR on Linux and Windows, and each release gets a short check on this PC before the next shift.

```powershell
cd C:\HASC\HASC-OCR
git fetch --tags
git checkout <new-tag>
.venv\Scripts\python -m pip install -r requirements.txt
nssm restart hasc-web
nssm restart hasc-worker
```

Then log in from a phone and upload one test photo before the shift starts.
