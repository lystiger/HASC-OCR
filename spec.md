# HASC Production Records (Hồ sơ sản xuất HASC) — first version

**Decisions:** Lystiger · **Written by:** Claude · **Status:** Accepted 2026-11-02, revised 2026-11-04 for the cloud reader · **Source:** `brief.md` P1–P108, `decisions.md` ADR 1–5

## 1. Context and problem
After each product run, a shift leader photographs a handwritten machine report (form `BMMT01-2022/HASC`, "form A") and sends it to the office on Zalo. Office members read the photo and judge it. Sending takes about a minute, but Zalo cannot edit, archive or fact-check. Tracing what happened at a past time means finding a paper sheet, and nothing records who is responsible for a number (P20).

## 2. Goals
- Every expected sheet is in one place, and any number traces to its photo, uploader, time and every later change (P21).
- AI reads each sheet into a readable Excel file that people can correct (P9, P26, P38).
- Boss accounts can see who uploaded, re-uploaded or edited what, and when (P13).
- First-version targets (P24, P28, P39, P64):

| Measure | Target |
|---|---|
| Expected sheets in the system | 100% |
| Accuracy of values on the labelled test set | At least 95% (provisional) |
| Sheets needing no hand entry | At least 90% |
| Upload, from opening the website to confirmed receipt | At most 2 minutes |
| Excel ready after the photo is saved | Within 15 minutes |
| Finding a past sheet by machine, date and shift | Under 1 minute |

Later aims, not first-version criteria: 1-minute upload, 5-minute reading, 100% with no hand entry, and 30-second lookup.

## 3. Non-goals
Out of the first version (P12, P59, P74):
- Dashboards and charts.
- Other forms going live before they pass their own test. The 2 other sheet types are captured and tested in stage 1, and go live one at a time (P89).
- AI trained to detect flaws.
- A website review screen.
- Gmail notifications.
- Grafana, n8n, and Zalo or Discord alerts.
- A tunnel or any access from outside the Wi-Fi.
- Disk encryption.
- An English interface.
- A printing layout.
- Automatic deletion.
- Showing product targets to operators.
- Storing formulas in the settings file.

The first version reads sheets with a pay-as-you-go cloud model, Gemini 3.8 Flash through OpenRouter, because local models on the Dell scored far below 95% (P104, P108). Reading is the only paid part, at about $0.004 per sheet; everything else stays free (P52). No real sheet goes to the reader in the running system until Lystiger finishes the Q32 check.

A new form goes live after form A has met the P64 targets for 4 weeks, and 20 labelled sheets of that form (8 tuning, 12 hidden test) reach 95%. Trained AI needs about 500 sheets confirmed as good runs, plus suitable hardware. Lystiger can move earlier, on record (P75).

## 4. Requirements

### Functional

**Roles (P4–P7, P11, P13, P48, P61, P70, P78)**

| Action | Operator (2) | Employee | Boss | Admin (Lystiger) |
|---|---|---|---|---|
| Upload a photo, see the printed limits | Yes | No | No | No |
| See the status of own uploads | Yes | n/a | n/a | n/a |
| See the status of all sheets, and photos | No | Yes | Yes | Yes |
| Pick up, download, correct, re-upload, confirm, retry, hand-enter | No | Yes | Yes | No |
| Mark an upload invalid | No | Yes | Yes | No |
| Mark a machine or shift not running, with a reason | Yes | Yes | Yes | No |
| See sheet numbers | No | Yes | Yes | Yes, each opening logged |
| See edit history | No | Own edits | Everyone's | Own actions |
| Add or deactivate accounts, reset passwords, log out any account | No | No | No | Yes |
| See statistics | No | No | Overdue count only | Yes |

Employees and bosses together are the 6 office members. Nobody can delete sheets, photos or history through the website (P47). Removing a person deactivates the account and keeps their history.

**Login (P45, P49)**
- Username and password. The admin creates the account and a temporary password, and the user must change it on first login.
- Operator sessions last 30 days. Office sessions last 8 hours.
- The site uses HTTPS on the local network, with a certificate created on the Dell and trusted on each device (P67).
- The website is in Vietnamese only (P37).

**Upload (P18, P22, P25, P33, P36, P71)**
- The shift leader uploads after each product run, plus a final photo at shift end. A first upload creates the sheet; later uploads add to it.
- They can use the upload button or the camera, from a phone or a laptop (P109). The system accepts JPEG and HEIC.
- They choose the machine, date and shift (P83). The screen pre-selects the shift from the clock and the machine this account chose last, and between midnight and 7am the date defaults to yesterday (P110). The upload button shows what it will send, like `Gửi: T1 · Ca 2 · 19/10 · Trang 1`. They also choose a page number, defaulting to page 1 (P92). Each paper has form A on one side and a back layout on the other, and a side with data is a page in either layout (P98, P99). The leader chooses the machine for each side (P101). The back has no printed date or shift, so the leader's choice is the only source. Machine, date, shift and page identify the sheet.
- The system always saves the photo before anything else, and always keeps the original.
- A quick check tests that the file is an image under 20 MB and above a minimum resolution, plus blur, darkness and whether a form-like table fills the frame. It warns and suggests a retake, and never blocks. After reading, a photo with no rows shows the leader a warning to check the other side (P99).

**Reading (P26, P27, P40, P53, P58, P77)**
- The AI reads every printed field on form A or the back layout, one photo at a time, in a queue. It works out the layout from the columns, not the title. On a back side, `Tổng KL` is gross, `Lõi` is core and `KL đã trừ` is net.
- Reads are stored as intended values: a ditto mark becomes the value above, a crossed-out value becomes the final one, and 13,6 becomes 13.6.
- Empty cells stay empty, except for carry-down: customer, film type, pellet formula, mixing shift/date, thickness, width and length carry from a run's first row to the rows below. Weights do not.
- The system retries automatically within 15 minutes, then marks the reading Failed. An office member can press retry or hand-enter using a blank template.
- Each photo is turned upright before reading (P107). **Undecided:** how back pages, which aren't always photographed the same way, are turned.
- The reader is replaceable. A replacement must pass the same labelled test before going live.

**Repeat photos (P22, P68)**
| Case | Result |
|---|---|
| New row | Added |
| Existing row, same reading | No change |
| Existing row, different reading | Current number stays, and the row is flagged for an office member |

If this adds rows or flags to a Reviewed sheet, the sheet returns to Needs review, with only those rows highlighted. Reviewed always means every current row has been checked.

**Flags and rule checks (P14, P30, P62, P63, P65)**
- A value is flagged if the AI reports doubt or it fails a rule. A rule never changes a number, and is skipped if its values are empty.
- Rules at launch: gross weight − (core + cover) = net weight, and the average of the six thicknesses = the written average. A back side has no cover column, so its rows skip the weight rule (P99).
- Limit and goal rules stay off until their values and meaning exist in the settings file (Q36, Q43). Formulas are not stored there. Starter limits, updated during development (P87, P90): thickness ±5 mic and width ±5 mm from the run's first row; net weight (`Sau trừ bì`) by a range set per product, with no check for a product that has no range yet. Values carried down from the first row always match it, so the thickness and width checks fire only where a lower row has its own written value.
- Lystiger believes "Thông số độ dày đo được", "Đường kính cuộn" and "Máy" are left blank in practice (P84). They are read if written and stay out of the 95% score, so in practice only the weight rule is expected to fire at launch.
- Office members check every value during the trial, then only flagged values once missed errors are at most 0.5% of values.

**Review and correction (P9, P31, P32, P34, P38, P42, P69)**
- Any office member can pick up a sheet. Others can take over, which is recorded.
- There is one correctable Excel file per page, named like `HASC_T1_2026-10-19_Ca2_Trang1.xlsx`. See the layout in section 6.
- Pages are reviewed separately. A read-only file named like `HASC_T1_2026-10-19_Ca2.xlsx` joins all of a shift's pages in page order (P92).
- Correcting means editing that file and re-uploading it. The system compares it with the current data and records each changed value.
- If the file needs no change, the office member presses `Đúng, xác nhận` on the sheet's detail page instead (P111). It confirms the version they downloaded, and is refused like an out-of-date file if the data changed since.
- The system rejects a re-upload made from an out-of-date version, and asks the person to download the latest.
- A sheet is overdue 24 hours after entering Needs review or In review, weekends included. Overdue sheets top the office list, and the count shows on boss and admin screens.

**Lookup (P21, P39)**
- The office sheet list opens on Needs review, In review and Failed sheets, overdue first. Each row shows machine, date, shift, page, status, the uploading shift leader and how long it has waited, and clicking it opens the sheet's detail page (P111).
- Office members find a sheet by machine, date and shift, see all of that shift's pages, and see each page's photos, status and current file.
- A photo is visible to the office as soon as it's saved.

**History and statistics (P13, P48)**
- The system appends an event for each upload, re-upload, confirm, value change, pick-up, takeover, retry, invalid mark, not-running mark, admin sheet opening and account change. There is no way to edit or remove an event through the website.
- The admin's main screen shows sheets per day by status; expected sheets not yet received; failed readings and retries; upload and AI reading times; and values flagged and corrected.

**Sheet statuses (P41)**

| Status | API value | Vietnamese label (proposed) | Means |
|---|---|---|---|
| Received | `received` | Đã nhận | Photo saved, waiting for the reader |
| Reading | `reading` | Đang đọc | AI working |
| Needs review | `needs_review` | Cần kiểm tra | Excel file ready, nobody has it |
| In review | `in_review` | Đang kiểm tra | An office member picked it up |
| Reviewed | `reviewed` | Đã kiểm tra | Corrected file uploaded or confirmed, all current rows checked |
| Failed | `failed` | Lỗi | Needs manual retry or hand entry |

### Non-functional

**Hosting (P50, P52, P55, P56, P58)**
- Website, database, photos and the reading worker run on the Dell Inspiron 5521 (i5-3337U, 8 GB RAM, 600 GB disk, onboard GPU, Ubuntu). The worker sends each photo to the cloud reader (P104).
- Phones and computers reach it over company Wi-Fi only.
- The reader needs internet. Every request asks OpenRouter for providers that neither store nor train on the data (`data_collection: deny`). The API key lives only on the Dell, outside the repository and logs. A fallback model on the same terms takes over when the main one fails (P104).
- Without internet, photos still save and jobs wait, then end Failed after 15 minutes like any other reader failure. **Undecided:** whether an outage should hold jobs longer instead of failing them.
- All 9 people can use the website at the same time.

**Data (P44, P46, P47, P54, P60, P72)**
- Photos, numbers and history are kept with no automatic deletion. Lystiger reviews this yearly.
- No real sheet photo goes to the cloud reader in the running system until Lystiger finishes the Q32 check. Labelled October sheets were sent for the stage 1 test only.
- Backups are encrypted and go to a dedicated free cloud storage account. They hold the database, history and a smaller copy of each photo. Original photos stay only on the Dell.
- The encryption key is kept away from the laptop. A backup counts only after a restore is tested.
- Accepted risk: the Dell's disk is not encrypted (P60).

**Running it (P57, P58)**
- Lystiger operates the system and is the only person with direct server access.
- The Dell emails Lystiger about failed backups, stuck queues, repeated AI failures or a nearly full disk.
- A heartbeat carrying no sheet data alerts Lystiger if the laptop stops.

**Trial (P23, P30, P65, P66, P70)**
- Zalo and the system run side by side for 1 week.
- Zalo stops when, during a trial week, all expected sheets arrived, at least 90% needed no hand entry, and accuracy from that week's corrections is at least 95%.
- If not met, the trial extends a week at a time, up to 4 weeks. Lystiger then decides whether to fix, change or stop.
- Expected sheets are each machine in the settings list × each shift, minus shifts marked not running. For form A the list is T1, T3 and T6, up to 6 sheets a day. T2 doesn't use form A (P95). A shift counts as received once any page arrives, and extra pages don't raise the expected count (P92).

## 5. Acceptance criteria
Automated criteria use a fake reader that returns set values, unless they name the labelled test. Criteria 37–43 are measured on real sheets, the Dell or the trial.

**Accounts and access**
1. A new account must change its temporary password before doing anything else.
2. An operator session is still valid after 29 days and expired after 31. An office session is valid after 7 hours and expired after 9.
3. The admin's "log out" ends that account's sessions on its next request.
4. A deactivated account cannot log in, and its past events remain.
5. Each cell in the section 4 role table is enforced by the API: a forbidden action returns `403` and changes nothing.
6. An operator cannot retrieve any sheet's numbers or Excel file through any endpoint.
7. An admin opening a sheet creates an event that a boss can see and an employee cannot.
8. An employee's history shows only their own edits, and a boss's shows everyone's.
9. No endpoint deletes a sheet, photo or event.
10. A plain HTTP request does not reach a login form or carry a password.

**Upload**
11. An uploaded JPEG and an uploaded HEIC each create a photo record, with the original file byte-identical on disk.
12. A second upload for the same machine, date, shift and page joins the same sheet. A different shift or page creates a new sheet.
13. A blurry, dark or non-form image is saved and returns a warning. The upload is not blocked.
14. A non-image or a file over 20 MB is refused with a clear Vietnamese message and no sheet changes.
15. The upload response returns before reading starts, and the photo is visible to an office member immediately.

**Reading and repeat photos**
16. A ditto mark, a crossed-out value and `13,6` are stored as the value above, the final value and `13.6`, given the reader's matching output.
17. Blank carry-down columns take the first row's values in the database, and weights do not.
18. A blank that isn't a carry-down column stays empty, and is never filled by guessing.
19. A new row is added, and an identical reading changes nothing and doesn't raise the version.
20. A differing reading keeps the current number, including a human correction, and flags the row.
21. A new row or flag on a Reviewed sheet returns it to Needs review, with only those rows highlighted in the next file.
22. A reader that fails twice then succeeds ends in Needs review. One that keeps failing ends in Failed within 15 minutes, with each attempt recorded.
23. A manual retry and a hand-entered template each work on a Failed sheet, and are recorded in history.
24. A wrong upload marked invalid stays stored. Rows that came only from that photo leave the current data, and the action is recorded.

**Rules**
25. A row where gross − (core + cover) ≠ net gets a red flag, and no number changes.
26. A row where the average of the six thicknesses differs from the written average gets a red flag.
27. A rule with an empty input is skipped and produces no flag.
28. A limit or goal rule with no settings value doesn't run. Adding the value turns it on without a code change, and each check records the values used.

**Excel and correction**
29. The file matches the section 6 layout, and its name matches `HASC_<machine>_<YYYY-MM-DD>_Ca<shift>_Trang<page>.xlsx`.
30. Numbers are stored as Excel numbers, and `SUM` over the net weight column gives the correct total.
31. Yellow, red and grey cells match AI doubt, rule failure and unreadable, and a key explains each.
32. Headings stay visible while scrolling, and column widths show full values.
33. Correcting one value and re-uploading changes exactly that value, and records who, when, old and new.
34. Correcting a run's first row updates the inherited values below it.
35. A re-upload from an older version is refused and changes nothing, including after a new photo changed the data.
36. A file with changed structure or a missing metadata tab is refused with a clear message. Cell formulas in re-uploaded files are not executed or stored as formulas.

**Measured on real data or the Dell**
37. **Stage 1 gate:** on a fresh 12-sheet hidden test set (the first one became tuning data, P106), the chosen reader reaches at least 95% of values, excluding values labelled unreadable, and fields with fewer than 100 labelled non-empty values are reported separately (see section 8).
38. On the Dell, every one of those 12 sheets is read through the cloud reader in under 15 minutes, and the run's cost is recorded.
39. A timed upload by each shift leader takes at most 2 minutes, from opening the website to confirmation.
40. A person finds a named past sheet by machine, date and shift in under 1 minute.
41. Nine logged-in users working at once get no errors while a reading runs.
42. A backup restored onto a clean machine gives the same sheets, numbers, history and smaller photos, and a copy in cloud storage can't be read without the key.
43. Stopping the Dell raises the heartbeat alert, and forcing a failed backup, stuck queue, repeated AI failure, nearly full disk and low OpenRouter credit each emails Lystiger.
54. Confirming a sheet at its current version makes it Reviewed and records who confirmed it. Confirming an older version is refused with `409 stale_version` and changes nothing.
53. Every reader request carries `data_collection: deny`, and the API key appears in no file in the repository, no log line and no error message.

**Trial and statistics**
44. Expected-sheet counts are correct for a shift with a sheet, one without, and one marked not running. A not-running mark without a reason is refused.
45. A sheet shows as overdue exactly 24 hours after entering Needs review or In review, including across a weekend.
46. The statistics in section 4 match a known set of test events.

**Pages (P92)**
47. Page 1 and page 2 of one shift have separate statuses, versions and correction files. Correcting one doesn't change the other's data or version.
48. The joined shift file lists every page's rows in page order, and re-uploading it is refused with no change.
49. A shift with only page 2 counts as received. Two pages of one shift count as one expected sheet.

**Two-sided paper (P98, P99)**
50. A reader result with layout `back` stores `Tổng KL`, `Lõi` and `KL đã trừ` as gross, core and net, is not refused for its title, and skips the weight rule.
51. A photo read as having no rows is kept and warns the leader to check the other side.
52. Blank product details at the top of a page take the last run's values from the previous page. If that page hasn't arrived, they stay empty and flagged, then fill when it does. Values written on the page are never replaced.

## 6. Design

**Parts**, which can be built in parallel against the contracts below:

| Part | Owns | Depends on |
|---|---|---|
| Web and API | Login, roles, upload, lookup, statuses, history views, admin screens | Database, Excel, queue |
| Reader | Turning one photo into `ReadResult` behind a replaceable interface | Nothing in the website |
| Reading worker | Queue, retries, merging into rows, flags | Reader, rules |
| Rules | Checking rows using the settings file | Settings file |
| Excel | Building files and parsing re-uploads | Data shapes |
| Evaluation | Scoring a reader against labelled sheets | Reader |
| Operations | Certificate, backups, alerts, heartbeat, restart after power cut | Nothing in the website |

**Stack (flagged below):** Python, FastAPI with server-rendered pages, SQLite, photos as files on disk, `openpyxl` for Excel, and systemd so everything restarts after a power cut.

**Data model**

```text
users(id, username, password_hash, role[operator|employee|boss|admin],
      active, must_change_password, created_at)
sheets(id, form, machine, production_date, shift, page_no, status, version,
       picked_up_by, status_since,
       UNIQUE(form, machine, production_date, shift, page_no))
photos(id, sheet_id, path, small_copy_path, sha256, uploaded_by, uploaded_at,
       upload_ms, quality_warnings, invalid, invalid_by)
read_jobs(id, photo_id, status, attempts, last_error, reader_name,
          queued_at, started_at, finished_at)
page_values(sheet_id, field, value, source_photo_id, flag)
rows(id, sheet_id, row_key, position, reviewed, invalid)
cells(row_id, field, value, inherited, source[ai|person|hand],
      source_photo_id, flag[none|doubt|rule|unreadable|disputed], note)
not_running(machine, production_date, shift, reason, marked_by, marked_at)
events(id, at, user_id, action, sheet_id, row_id, field, old_value, new_value,
       detail)
```

A row's `row_key` is its roll code, and its position on the page if the code can't be read. `version` goes up only when a sheet's data or flags change.

**Reader contract.** Everything else depends only on this, so a local model can be replaced by a cloud model without changing the other parts (P53, P80).

```python
read(image_path: str) -> ReadResult
```
```json
{
  "form": "BMMT01-2022/HASC",
  "layout": "form_a",
  "page": {"day": {"value": "19", "doubt": false}},
  "rows": [
    {
      "cells": {
        "roll_code": {"value": "1", "doubt": false},
        "gross_kg": {"value": 15.8, "doubt": false},
        "core_kg": {"value": 2.2, "doubt": true},
        "net_kg": {"value": 13.6, "doubt": false},
        "width_mm": {"value": null, "doubt": false},
        "note_field": {"value": null, "doubt": false, "unreadable": true}
      }
    }
  ],
  "reader_name": "model-name-and-version"
}
```

`null` means an empty cell. A failure raises `ReaderError(kind, message)`, with `kind` either `timeout`, `not_form_a` or `other`. `layout` is `form_a` or `back`, and `not_form_a` means the photo is neither. Carry-down happens in the worker, not the reader.

**Fields.** These English keys are stable across the API, reader, rules and Excel:

`machine`, `customer`, `film_type`, `roll_code`, `pellet_formula`, `mixing_shift_date`, `thickness_mic`, `width_mm`, `length_mm`, `gross_kg`, `core_kg`, `cover_kg`, `net_kg`, `thickness_1`…`thickness_6`, `thickness_avg`, `diameter_a`, `diameter_b`. The back layout also has `production_time` and `grammage`. Its printed trimming and OK columns are not read as fields, because crews use that space for handwritten subtotals (P101).

**API.** All endpoints need a session. All errors look like `{"error": {"code": "stale_version", "message": "<Vietnamese text>"}}`.

| Method and path | Who | Request | Response |
|---|---|---|---|
| `POST /api/login` | Anyone | `{username, password}` | `{role, must_change_password}` |
| `POST /api/password` | Self | `{old, new}` | `204` |
| `POST /api/photos` | Operator | Multipart: `file`, `machine`, `production_date`, `shift`, `page_no`, `opened_at` | `201 {photo_id, sheet_id, status, warnings: []}` |
| `GET /api/uploads/mine` | Operator | | `[{sheet_id, machine, production_date, shift, page_no, status, uploaded_at}]` |
| `GET /api/limits` | Operator | | `{thickness_max_mic: 8, diameter_max_mm: 3}` |
| `GET /api/sheets?machine=&date=&shift=&status=&overdue=` | Office, admin | | `[{id, machine, production_date, shift, page_no, status, version, picked_up_by, overdue}]` |
| `GET /api/shifts/excel?machine=&date=&shift=` | Office | | Read-only joined `.xlsx` file |
| `GET /api/sheets/{id}` | Office, admin | | Sheet, photos, rows and cells |
| `POST /api/sheets/{id}/pickup` | Office | `{takeover: bool}` | `{status, picked_up_by}`, or `409 already_picked_up` |
| `GET /api/sheets/{id}/excel` | Office | | The `.xlsx` file |
| `POST /api/sheets/{id}/excel` | Office | Multipart: `file` | `{version, status, changed: n}`, or `409 stale_version`, or `422 bad_layout` |
| `POST /api/sheets/{id}/confirm` | Office | `{version}` | `{version, status}`, or `409 stale_version` |
| `GET /api/sheets/{id}/template` | Office | | Blank `.xlsx` for hand entry |
| `POST /api/photos/{id}/retry` | Office | | `202 {job_status}` |
| `POST /api/photos/{id}/invalid` | Office | `{reason}` | `{sheet_id, version}` |
| `POST /api/not-running` | Operator, office | `{machine, production_date, shift, reason}` | `201` |
| `GET /api/history?sheet_id=&user_id=` | Boss: all; employee: own | | `[{at, user, action, row, field, old, new}]` |
| `GET /api/admin/stats?from=&to=` | Admin | | `{sheets_by_status, missing_expected, failed_reads, retries, upload_ms, read_ms, flagged, corrected, overdue}` |
| `POST /api/admin/users` | Admin | `{username, role}` | `201 {id, temporary_password}` |
| `PATCH /api/admin/users/{id}` | Admin | `{active?, role?, reset_password?}` | User |
| `POST /api/admin/users/{id}/logout` | Admin | | `204` |
| `PUT /api/admin/review-mode` | Admin | `{mode: "full"\|"flagged"}` | `{mode}` |

**Job statuses** (`read_jobs.status`): `queued`, `running`, `retry_wait`, `done`, `failed`.

**Sheet status changes**

| From | Event | To |
|---|---|---|
| none | First photo saved | `received` |
| `received` | Job starts | `reading` |
| `reading` | Job done | `needs_review` |
| `reading` | Job failed after retries | `failed` |
| `failed` | Manual retry | `reading` |
| `failed` | Hand-entered template uploaded | `reviewed` |
| `needs_review` | Pick-up | `in_review` |
| `in_review` | Valid re-upload or confirm | `reviewed` |
| `reviewed` | New photo adds rows or flags | `needs_review` |

A new photo during `needs_review` or `in_review` keeps that status. If it changes data, the version goes up, so an older file gets rejected.

**Excel layout (for Lystiger to accept).** This is a sample, since no example file exists. The headings are Claude's reading of the form, so check them against the paper.

```text
Tab "Báo cáo"
Row 1   BÁO CÁO CHI TIẾT MÁY THỔI T1,T6,T2,T3    Mã số: BMMT01-2022/HASC
Row 2   Ngày: 19   Tháng: 10   Năm: 2026   Ca: 2   Máy: T1   Trang: 1
Row 3   Tên nhân viên trong ca: ...
Row 4   Khóa cột: yellow = AI chưa chắc | red = sai quy tắc | grey = không đọc được
Row 5-6 Headings (frozen):
        Máy | Khách hàng | Loại màng | Mã cuộn | Công thức hạt | Ca/ngày trộn hạt |
        Dày/mic | Khổ rộng/mm | Dài/mm | Khối lượng Kg: Cả bì, Lõi, Bìa, Sau trừ bì |
        Độ dày đo được: 1..6, Trung bình | Đường kính cuộn: Đầu thu 1(A), Đầu thu 2(B) |
        Cần kiểm tra
Row 7+  One row per roll. Inherited product details stay blank below a run's first row.

Tab "Ảnh"       The sheet's valid photos, newest first.
Tab "_meta"     Hidden: sheet_id, version, row keys and a layout version.
```

`Cần kiểm tra` is an added last column, which marks new or disputed rows and holds the differing reading. A back page's file uses that layout's own headings. The joined shift file uses the shared columns, followed by the back-only ones.

**Rule comparison.** Weights are compared at the precision written, usually one decimal. A written average passes if it's within half of the last written digit of the calculated one.

**Reader.** `hasc/reader` calls Gemini 3.8 Flash through OpenRouter, asks for plain `key=value` lines, re-reads once with more thinking where a row fails a rule, and falls back to a second model after 3 failed tries (P104). The prompt lists the standard names from `known_names.json`, which stays on the Dell (P105).

**Operations.** A daily job at 5am backs up, encrypts and uploads with `restic` through `rclone`, taking turns with the reader (ADR 5). Prune and an integrity check run on Sunday. Reading alerts: a job queued over 15 minutes, 3 failures in a row, a disk over 80% full, or OpenRouter credit below a set amount (**undecided**, suggested $1). The heartbeat is sent every 5 minutes, and the outside service alerts after 15 minutes without one.

## 7. Alternatives considered
| Alternative | Why rejected |
|---|---|
| A local model on the Dell | Scored far below 95% in 4 test runs, mostly by putting values under the wrong columns (P104) |
| Moving the AI to the Ryzen workstation | Replaced by the cloud reader (P80, P104) |
| Gemini through Antigravity on a personal account | Its terms don't rule out training on personal-account data, and it's a coding agent with quotas, not a reader (P108) |
| A paid or separate cloud server | No money in the first version (P52) |
| Website review screen | Larger build; trial evidence can justify it later (P32) |
| Dashboards in the first version | Charts on unmeasured data look reliable while being wrong, and form A lacks some inputs (P10) |
| AI writing the edit history | History must be exact (P13) |
| New photo replaces existing numbers | A misread could silently undo a correction (P22) |
| Boss approval of corrections | All 6 keep working rights, and corrections would wait (P11, P13) |
| Public tunnel | Later; the first version stays on Wi-Fi (P52) |
| Full-disk encryption | Deferred until after rollout, accepting theft risk (P60) |

## 8. Risks
| Risk | Handling |
|---|---|
| A cloud model's terms, cost or data rules don't fit | Check Q32 and the provider's current terms before any real photo is sent. Free plans must not be assumed to bar training on the data |
| The model's score varies between runs (92.7–98.2% on the same 12 sheets, P107) | Reading twice and flagging disagreements is the next reader step. Full checks in the trial measure what slips through |
| The model is retired, changes or rises in price | The fallback model keeps reading. A replacement must pass the labelled test |
| Spending runs away through retries or a bug | The OpenRouter key has a spending limit, and a low-credit alert emails Lystiger |
| Internet outage | Photos still save; jobs fail after 15 minutes and are retried by hand |
| 20 labelled sheets give weak evidence, with 12 in the test set | Treat a pass as encouraging, not proof. The trial's corrections provide more evidence |
| Measured thickness and diameter are left blank (P84) | The average and printed-limit rules rarely or never fire, and the records hold little measured quality data, which limits the QA/QC goal until the factory fills them in |
| AI doubt misses wrong values | Full checks during the trial measure this before moving to flagged-only (P65) |
| A misread first row spreads to the rows below | First-row carry-down cells are flagged if doubtful, and one correction fixes all (P77) |
| Laptop stolen | Accepted (P60): all sheets, formulas and staff names are readable. Keep it locked up if possible |
| Disk fails | Encrypted backups restore all numbers and smaller photos. Original photos are lost (P72) |
| Power cut or dead laptop at night | Automatic restart and heartbeat alert. Shift leaders can upload later from their gallery |
| Free services change terms (backup, heartbeat, email) | Confirm before stage 4. A lost service is a decision for Lystiger, because P52 forbids paying |
| Reviewers lose edits to a rejected out-of-date file | Accepted (P42). Pick-up reduces how often it happens |
| Someone marks a shift not running to hide a missing sheet | A reason is required, and bosses see it in history (P70) |
| Flagged-only review hides later errors | Corrections are watched for a rise (section "Claude's choices", item 8) |
| Direct server access bypasses the history | Only Lystiger has it (P58), and it's not covered by website logs (P48) |
| A contract or rule restricts the data | Lystiger checks Q32. Encrypted backups should be included |
| A leader photographs only one side, so rows on the other side are missed | The no-rows warning catches an empty side. A shift counts as received once any page arrives, so a missed side with rows on both is still invisible |
| Which machine makes which back-side product is unknown | The leader picks the machine at upload, and the new collection will show the pattern (P101) |

**Open items that don't block accepting this spec**

| # | Item | Owner | Blocks |
|---|---|---|---|
| Q36 | Product values, what the limits mean, and when a difference flags | Lystiger, from the factory | Limit and goal rules only |
| Q32 | Contracts and data-protection rules | Lystiger | Sending real sheets to the cloud reader in the running system; should also cover backups |
| Q10 | Which model | Closed 2026-11-04: Gemini 3.8 Flash through OpenRouter, pending criterion 37 on a fresh hidden set | Go-live, not the build |
## 9. Test plan
| Layer | How | Criteria |
|---|---|---|
| API and roles | Automated tests through the HTTP API with a temporary database, covering each role against each endpoint | 1–10, 44–46 |
| Upload | Automated, with JPEG, HEIC, blurry, dark, non-form, non-image and oversize fixtures | 11–15 |
| Pages | Automated, with two pages of one shift and a fake reader returning either layout | 47–52 |
| Worker and repeat photos | Automated, with a fake reader that returns set results, fails or times out | 16–24 |
| Rules | Unit tests with passing, failing and empty inputs | 25–28 |
| Excel | Build files, edit them in code, re-upload, and inspect them with `openpyxl`, plus one manual check in real Excel | 29–36 |
| Evaluation | A script compares `ReadResult` with labelled JSON and reports accuracy by field, using the P27 rules | 37, 38 |
| Real use | Timed sessions on the Dell with real phones and office computers | 39–41 |
| Operations | Restore test on a clean machine, and alert tests with forced failures | 42, 43 |

Labelled sheets stay local and are never committed to a repository, and the 12 test sheets are not used for tuning. The first 12 became tuning data after their errors were studied (P106), so a fresh 12 are labelled for the gate. Tests must not use real sheet data unless they run locally.

## 10. Rollout
1. **Model test (P55, P73, P104, P106):** local models failed, and Gemini 3.8 Flash through OpenRouter scores about 95% on 20 labelled form A sheets with upright photos. Still to do: a fresh 12-sheet hidden test, then criteria 37–38. The build runs alongside it; going live waits for it and for Q32. The other 2 sheet types report their results later.
2. **End-to-end slice:** login, upload, AI read, Excel, download.
3. **Review:** re-upload, version check, history, rules, not-running marks, overdue list.
4. **Operations:** backups with a restore test, alerts, heartbeat, statistics.
5. **Set-up and trial:** install the certificate on all devices and create accounts. Run all listed machines alongside Zalo under the section 4 trial rules. Lystiger decides when Zalo stops and when review moves to flagged-only.

**Going back:** Zalo keeps running throughout the trial, so stopping the system loses no sheets. After Zalo stops, the fallback is to resume Zalo and paper, and enter missed sheets by template when the system returns. The system touches nothing outside the Dell and its backup account.

## Claude's choices, accepted by Lystiger 2026-11-02
1. **Stack:** Python, FastAPI with server-rendered pages, SQLite, photos as files, `openpyxl` and systemd. They're free and light enough for the Dell. Check current library docs when building.
2. **Identifying rows:** a row is matched by its roll code, or by its position if the code can't be read.
3. **Production date:** a night shift crossing midnight belongs to the date it started.
4. **Reading a disputed row:** it shows as yellow, with the differing reading in the added `Cần kiểm tra` column, which is the one addition to the paper's columns.
5. **Your own edits in carry-down cells:** typing a value into a blank inherited cell overrides it for that row only, and it's recorded.
6. **Invalid uploads:** rows that came only from an invalid photo leave the current data but stay stored.
7. **Thresholds:** 3 reading attempts within the 15 minutes; alerts for a job queued over 15 minutes, 3 failures in a row, or a disk over 80% full; and a heartbeat every 5 minutes. The backup schedule is now Lystiger's decision (ADR 5).
8. **Return to full checks (P65):** after moving to flagged-only, corrections to unflagged values in a week above 0.5% of values, or double the previous 4-week average, alert the admin. Lystiger then sets the review mode back. People aren't prompted to check unflagged values, so this can undercount.
9. **Enough real examples (P78):** a field needs at least 100 labelled non-empty values to count toward the 95%. Other fields are reported separately.
10. **Rule precision:** weights are compared as written, and an average passes within half of its last written digit.
11. **Photos in Excel:** the `Ảnh` tab holds the sheet's valid photos, newest first. They may be reduced in size to keep the file workable, with originals on the Dell.
12. **Excel safety:** only `.xlsx` is accepted for re-upload, and formulas in re-uploaded files are not stored or run.
13. **Vietnamese labels:** the status labels in section 4 and the column headings in section 6 are my wording, so please check them.
14. **Failed sheets after hand entry:** a hand-entered template goes straight to Reviewed, because a person typed it from the photo.
15. **Joined Excel is read-only:** because each page has its own review (P92), corrections go through the per-page files. The joined shift file is for reading and downloading, and the system refuses it on re-upload.
16. **Printed title is ignored:** the reader works out the layout from the columns, so a back side is not refused as `not_form_a` for its title (P99).
17. **Carry-down across pages:** if a page's first rows have blank product details, they carry from the last run on the previous page. If that page hasn't arrived, they stay empty and flagged until it does. Values written on the page itself always win.
18. **Empty-side warning:** a photo read as having no rows is saved, and the leader is warned to check the other side.
