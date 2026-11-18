# HASC Production Records — roadmap

**Decisions:** Lystiger · **Written by:** Claude · **Status:** Scope closed 2026-11-05 (P119) · **Source:** `spec.md`, `decisions.md` ADR 1–7, `brief.md` P104–P119

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

| # | Step | State |
|---|---|---|
| S1 | Thin end-to-end slice: package, database, login, operator upload, worker, office list and detail, page Excel, confirm, CI (P114) | Done, PR #2 and #3 |
| S2 | Page Excel fixes (P118): roll codes in `Mã cuộn`, the handwritten subtotal no longer read as average thickness, carried values on every row in grey, the new legend and widths. Daily report (P117): one `Báo cáo sản xuất ngày` .xlsx per day in the paper's layout, Ca 1 and Ca 2 blocks, one line per machine + customer + type + product code with the summed net weight, the day totals, and blank cells for waste, pending goods and M2 for the office to fill. Photos saved under the data folder, not the working directory | Next |
| Go-live | Run on the Dell for the factory: the app and worker as services, phone access on the factory Wi-Fi, accounts | After S2; Lystiger deploys |

Later, only if a user asks (P119): Excel re-upload, hand-entry template, history, not-running marks, statistics, backups, alerts, HTTPS certificate, HEIC, reading the intake and trimming forms.

## Undecided, needed before the step that uses it

| Item | Needed by |
|---|---|
| Which machine each daily-report line belongs to, when one form A page holds T1 and T6 (Q46) | Answered by P150: the written `Máy` on form A rev 3, once rev 3 is in use |
| Where waste (`Phế phát sinh`) and the M2 bag line come from | S2 leaves them blank |
| Whether an internet outage holds jobs longer instead of failing them after 15 minutes | Go-live |
