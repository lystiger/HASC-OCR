# HASC Production Records — roadmap

**Decisions:** Lystiger · **Written by:** Claude · **Status:** Draft 2026-11-04, for Lystiger to check · **Source:** `spec.md`, `decisions.md` ADR 1–5, `brief.md` P104–P108

Two tracks run side by side. The build track makes the product, against the fake reader in the spec's acceptance criteria. The reader track finishes the stage 1 test. Going live needs both, plus Q32.

Each build step is one Hermes goal job (`POST localhost:8000/jobs/goal`). It is dry-run first, ends in a PR, and merges only once its tests pass, CI is green and an independent agent has verified it. Production deploys stay with Lystiger.

## Reader track (inline, not Hermes)

| # | Step | Who | Cost | Done when |
|---|---|---|---|---|
| R1 | Label 12 fresh October form A sheets as the hidden set, picked without looking at their contents | Lystiger; Claude picks the sheets | Time | 12 tabs filled, marked `hidden` |
| R2 | Read each sheet twice and flag values where the readings disagree | Claude | ~$0.15 | Wrong values on unflagged rows drop on the 20 tuning sheets |
| R3 | Turn back pages upright | Claude | ~$0.05 | Every October back photo comes out upright |
| R4 | One gate run on the fresh hidden set | Claude | ~$0.05 | Criterion 37 reported, pass or fail |
| R5 | Q32: contracts and data-protection rules | Lystiger | — | Written answer in `brief.md` |

## Build track (Hermes goal jobs, one PR each)

**S1, the first job (P114):** a thin end-to-end slice through B1–B5: CI, the package skeleton, the database, login, the operator upload screen in the HASC design, the worker with the fake reader, and the Excel download. Later jobs widen each part to its full criteria.

| # | Step | Spec criteria | Depends on |
|---|---|---|---|
| B1 | Skeleton: the `hasc/` package with one folder per part (ADR 3), the existing reader moved into `hasc/reader`, the import-boundary check, and CI running the tests on every PR | Import check | ADR 6 |
| B2 | Database schema and migrations, accounts, login, sessions and roles | 1–10 | B1 |
| B3 | Upload: photos, quality check, camera time, identical-photo skip, sheets and pages | 11–15, 47, 49, 56 | B2 |
| B4 | Worker: queue, retries, the reader behind its contract, carry-down, repeat photos, rules | 16–28, 50–53, 55 | B3 |
| B5 | Excel: build files, parse re-uploads, confirm button, version check, hand-entry template, joined shift file | 29–36, 48, 54 | B4 |
| B6 | Office screens: lookup, pick-up, history, not-running marks, overdue list, statistics | 44–46 | B5 |
| B7 | Operations: deploy script (ADR 4), backups (ADR 5), alerts, heartbeat | 42, 43 | B6 |
| B8 | Set-up on the Dell: certificate on every device, accounts, timed sessions | 38–41 | B7, R4 |
| B9 | Trial alongside Zalo (spec section 4) | Trial rules | B8, R5 |

B2–B6 follow the spec's rollout stages 2 and 3; B7 is stage 4, and B8–B9 are stage 5.

## Undecided, needed before the step that uses it

| Item | Needed by |
|---|---|
| Whether ADR 2 (a fresh reader process per photo) still fits a cloud reader | B4 |
| Whether an internet outage holds jobs longer instead of failing them after 15 minutes | B4 |
| Staging: whether a copy runs anywhere before the Dell | B7 |
