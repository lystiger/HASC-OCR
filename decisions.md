# HASC Production Records decisions

## ADR 1: Website and reading run as two processes (2026-11-02)
**Context:** The Dell has 8 GB RAM and 2 CPU cores. A reading may use most of both for minutes, while 9 users must keep working with no errors (spec criterion 41).

**Options:** One process with a background thread. Two processes sharing SQLite. Two processes plus Redis and Celery.

**Decision (Lystiger):** Two processes sharing SQLite. The website saves the photo and adds a row to `read_jobs`. A separate worker takes one job at a time. Each runs as its own systemd service, and SQLite runs in WAL mode.

**Consequences:** A worker crash leaves uploads working. The worker can have lower CPU priority and a memory cap. There are two services to run, and a job's state must survive a worker crash.

**What would make this wrong (Claude's suggestion, not yet confirmed by Lystiger):** Criterion 41 fails even with the limits in place, because the Dell has too little capacity for both. Reading moving to a cloud model (P80) would not change this layout, since the worker would still run the calls.

## ADR 2: The reader runs as a fresh process for each photo (2026-11-02)
**Context:** The model is chosen by the stage 1 test (Q10). A local model can take a long time to load and holds several GB while loaded. At most 6 sheets a day, plus repeat photos, leave it idle most of the time.

**Options:** A model server kept loaded, such as Ollama or `llama-server`. A fresh process per photo. The model loaded inside the worker.

**Decision (Lystiger):** A fresh process per photo. The worker starts a small reader program for each job, and it exits when done.

**Consequences:** All the reader's memory returns to the system between reads. The worker can kill a reader on timeout, and a crashed model can't damage the worker. Every read pays the load time inside the 15-minute limit.

**What would make this wrong:** Stage 1 shows load time takes too much of the 15 minutes. The `read(image_path)` contract stays the same, so moving to a model server later changes only the reader.

## ADR 3: One repo, one Python package, one folder per part (2026-11-02)
**Context:** The spec lists 7 parts that should be buildable in parallel against contracts. The website and worker share database code, rules and data shapes. One person maintains the system. Real sheets and labels must never enter the repo.

**Options:** One repo with one package and one folder per part. One repo with several installable packages. Separate repos per part.

**Decision (Lystiger):** One repo and one package, with folders such as `hasc/web`, `hasc/worker`, `hasc/reader`, `hasc/rules` and `hasc/excel`. An automated import check enforces the boundaries, for example `web` can't import `reader`.

**Consequences:** One version, one test run and one install. The boundaries depend on the import check staying in the test suite.

**What would make this wrong:** The reader's AI libraries conflict with the website's libraries. The reader then gets its own virtual environment, which stays cheap because ADR 2 already runs it as a separate process.

## ADR 4: Deploy by git tag and script, with a virtual environment and systemd (2026-11-02)
**Context:** The Dell runs Ubuntu with 8 GB RAM and two services. A new version may change the database. Lystiger alone has server access and does production deploys. A bad update must not lose sheets.

**Options:** Git tag, script, virtual environment and systemd. Docker Compose. Copying files from a laptop with `rsync`.

**Decision (Lystiger):** Lystiger connects over SSH and runs one deploy script with a version tag. The script backs up the database, checks out the tag, installs libraries, applies database changes, restarts both services, and confirms the website answers. Going back means running it with the old tag, and restoring that backup if the database changed.

**Consequences:** No extra software runs on the Dell, and the running version is always known. The Dell's Ubuntu setup is done by hand once, so its steps must be written down.

**What would make this wrong:** Rebuilding a dead Dell takes too long by hand. The written steps then become a script, tested during the restore test (criterion 42).

## ADR 5: Backups use `restic` through `rclone` (2026-11-02)
**Context:** Backups must be encrypted before leaving the Dell, and go to a dedicated free cloud account with limited space. They hold the database, history and smaller photo copies.

**Options:** `restic` through `rclone`. A full `tar` and `gpg` archive each night. An `rclone crypt` mirror.

**Decision (Lystiger):** `restic` through `rclone`, starting from a snapshot taken with SQLite's backup command. Check current docs for both tools at build time.

**Consequences:** Any past night can be restored, and only changes are uploaded. There are two tools to learn and a regular prune step.

**What would make this wrong:** The free account fills up even with changes-only uploads. Lystiger then decides what to do, since P52 forbids paying.

**Timing (Lystiger):** Lystiger pointed out that a night backup could clash with the night shift. The backup runs daily at 5am, a time held in settings. It waits for the current read to finish, the worker holds new jobs until it is done, and upload speed is capped to protect Wi-Fi. Uploads and the website keep working. `restic` prune and its integrity check run on Sunday. The first full upload is run by hand before go-live. A Sunday-only backup was rejected, because it could lose up to about a week of corrections and history.

**What would make the timing wrong:** Backups run so long that sheets miss the 15-minute limit. The trial shows this, because backup and read times are both recorded. The night shift runs 7pm to 7am (P95), so 5am is 2 hours clear of its final photos, though photos after a product run can still arrive and wait their turn.

## ADR 6: Tests run on GitHub Actions for every PR (2026-11-04)
**Context:** Build steps run as Hermes jobs, and a PR merges only once CI is green and an independent agent has verified it. The repository is private. Real sheets, labels, `known_names.json` and the OpenRouter key must never leave Lystiger's machines.

**Options:** GitHub Actions on GitHub's servers. A self-hosted runner on the Ryzen. No CI, with the Hermes verifier running the tests.

**Decision (Lystiger):** GitHub Actions runs the whole test suite on every PR and on `main`. Tests that need real sheets stay local and never run in CI.

**Consequences:** CI works when Lystiger's machines are off, and it gives the merge rule an independent check. CI has no secrets: every automated test uses the fake reader and made-up data, and a test needing real data is skipped when the local files are absent. The free tier allows 2,000 minutes a month for a private repository.

**What would make this wrong:** to be named by Lystiger.

## ADR 7: Server-rendered pages with htmx (2026-11-04)
**Context:** FastAPI renders the pages on the Dell (spec section 6). Operators upload from a phone or a laptop, and office members watch statuses change on the sheet list. Devices reach the Dell over company Wi-Fi only.

**Options:** Plain forms, with whole-page reloads and a timed refresh. Plain forms plus htmx, which swaps parts of a page with HTML from the server.

**Decision (Lystiger):** Plain forms plus htmx, so the upload status updates live (Received, Reading, Needs review).

**Consequences:** No separate frontend app, and the server stays the only place pages are built. Every page must still work as plain forms if htmx fails to load. The htmx file is served from the Dell, not a CDN, so pages don't depend on internet access.

**What would make this wrong:** to be named by Lystiger.

## ADR 8: Production on the Windows office PC; the Dell becomes the spare (2026-11-06)
**Context:** With the cloud reader (P104) the host only runs the website, the worker and SQLite for about 40 sheets a day. The office has a Windows PC (Intel i3-8100, 8 GB RAM, 1.13 TB) that can run around the clock; office staff also use it for BCSX, plans and typing sheets. A power cut stops the whole company, so the host only needs to come back on its own before the next shift.

**Options:** Production on the Dell (Ubuntu laptop) with the PC unused. Production on the PC with the Dell as staging. Production on the PC with the Dell as a cold spare.

**Decision (Lystiger):** Production runs on the office PC under Windows, as NSSM services under a dedicated `hasc` account, starting at boot. The data and config folders are encrypted to `hasc` with EFS. CI runs every PR on Linux and Windows. The Dell is a cold spare and the restore-test machine, not staging (Claude's recommendation: an Ubuntu staging box would not match a Windows production).

**Consequences:** More disk (about 20 years of photos at P72's estimate) and a machine built to run continuously. Windows Update must be paused and run in planned shutdowns, since the shifts cover 24 hours. Accepted risk: office staff share the PC; EFS stops them reading the data as themselves, but an administrator account could still copy the encrypted files, and losing the `hasc` profile without the exported EFS key loses the data. The README "Setting Up the Windows Production PC" section is the procedure.

**What would make this wrong:** Office use slows the PC enough to delay reads, staff restart or shut it down during shifts, or a Windows-only fault reaches production that CI did not catch.
