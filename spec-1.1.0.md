# HASC Production Records 1.1.0: accounts screen and office frame

Date: 2026-11-06. Decisions by Lystiger (P123–P145). Builds on `spec.md` and v1.0.2 (production on the Windows office PC, ADR 8). Where this file and `spec.md` differ, this file wins for 1.1.0. Items marked **UNDECIDED** need Lystiger before the build step that uses them.

## 1. Problem

v1.0.2 has an account API (`POST /api/admin/users`, `PATCH /api/admin/users/{id}`, `POST /api/admin/users/{id}/logout`) but no screen and no list. On the office PC the database is encrypted to the `hasc` service account, so `scripts/create_user.py` no longer works from an administrator window; adding a person today means PowerShell calls against the API. The admin cannot see who has an account, who is active, or who is online.

## 2. Goals

1. The admin adds, deactivates, reactivates, resets and changes the role of office accounts from a screen (P123).
2. The admin hands each new person their first password in person; the person then sets their own (P138).
3. One office frame that later modules (dashboard, purchase orders, production plan) can join (P124).
4. Office staff can upload a sheet from their own account (P128, P145).
5. The website runs on HTTPS on the office network (spec.md P67, P141).

## 3. Non-goals

- **Operator accounts.** Operators will not log in at all. The factory QR upload page, the trưởng ca attribution and the daily read cap (P134–P137) are **1.2.0**, specified separately (P139).
- Sending passwords by SMS or Zalo (replaced by P138; U1 is closed).
- Self-service sign-up or "forgot password". Only the admin creates and resets accounts.
- Forced periodic password changes (NIST SP 800-63B advises against them).
- Deleting accounts. Deactivation keeps the person's history (spec.md P47 stays).
- Sidebar entries for modules that do not exist yet (P124).

## 4. Requirements

### Roles

| Code | Shown on the accounts screen | Can be created from the screen |
|---|---|---|
| `employee` | Văn phòng (P127) | yes |
| `boss` | Ban giám đốc (P142) | yes |
| `admin` | Quản trị (P142) | owner only (see Owner) |
| `operator` | Xưởng (P142) | no: existing operator accounts keep working until deactivated, so the factory keeps uploading until 1.2.0 ships (P140) |

Pages other than the accounts screen show no role names ("show no roles", Lystiger). The role table in spec.md section 4 stays, with one change: **employee, boss and admin may also upload a photo**, recorded under their own account (P128, P145).

### Owner (P126)

- Exactly one account is the **owner** (Lystiger). Nobody else can deactivate, demote, reset or log it out.
- Only the owner can give or take away the admin role. Other admins manage employee, boss and operator accounts only: they cannot reset, log out, deactivate or change the role of any admin account, their own included.
- Nobody changes their own role, deactivates, resets or logs out themselves from the screen; people change their own password on the change-password page.
- **Where the owner comes from:** `scripts/create_user.py` gains `--owner`, which creates the account as an admin owner, or makes an existing admin the owner and sets a new temporary password for it. If another account is the owner, the flag moves (cleared there and set here in one transaction). The target's sessions end. The Windows installer creates its first `admin` with `--owner`. On an existing database the migration makes the oldest active admin the owner.
- If the owner loses their password, recovery is `create_user.py --owner` run as `hasc` on the PC (documented in the README), not the website.

### Accounts screen (admin only)

- A list of every account: username, role, active or deactivated, **online**, last seen, created.
- **Add:** username and role. The system makes a temporary password and shows it to the admin **once**, on that screen, to hand over in person (P138). It is never shown again; a lost one means a reset.
- **Reset password:** makes a new temporary password, shown once the same way, and ends that account's sessions.
- **Change role:** ends that account's sessions; the new role applies at their next login.
- **Deactivate / reactivate:** a deactivated account cannot log in and its sessions end. Reactivating keeps the same username, history and role (P127).
- **Log out:** ends that account's sessions (exists in v1.0.0).
- **Account history:** every add, reset, role change, deactivation, reactivation and log-out is recorded with who, what, old and new value, and when. Only admins see it (P130).
- Every action on another account asks for confirmation first, naming the account.

### Usernames (P127, P133)

- Case-insensitive and unique: `Hung` and `hung` are the same account, and login accepts either (the username lookup compares with `COLLATE NOCASE`).
- A username is never reused, even after deactivation. The way back is reactivating the old account.
- 3–32 characters from `a-z`, `0-9`, `.` and `_`; stored in lower case. Refusals are in Vietnamese.
- The rules apply to new accounts. Existing usernames are kept as they are, even if they break a rule.

### Temporary password and first login (P125, P133, P144)

- A temporary password keeps working until the user sets their own password, for at most 72 hours after it was made (P144). Closing the page by mistake is harmless.
- While an account still has a temporary password, every page except the change-password page and log-out redirects there (`303`), and every API call except changing the password and logging out returns `403` with the existing code `password_change_required`. `/login`, `/logout`, `/change-password` and `/static/*` are never redirected.
- Once the user has set a password, the temporary password no longer works.
- After 72 hours, login with it fails with "Mật khẩu tạm đã hết hạn. Hãy nhờ Quản trị đặt lại." in both the login page and `POST /api/login`.

### Sessions

Office sessions (employee, boss, admin) last 8 hours (spec.md P45, unchanged), and also end when the admin resets the password, logs the account out, changes its role or deactivates it.

### Online status (P131, P133)

An account is **online** when it made a request in the last 5 minutes. The screen shows "last seen" for the rest.

### Office frame (P124)

- One layout for employee, boss and admin pages: a header with the user's name and log-out, and a sidebar with today's items only: **Hồ sơ** (the sheet list), **Báo cáo ngày**, **Tải ảnh lên** (P128, P145: all three roles), and **Tài khoản** (admins only).
- Tải ảnh lên opens the existing upload screen inside the frame.
- On a phone the sidebar folds into a menu button.
- Operators keep their current phone screen, without the frame.

## 5. Acceptance criteria

1. The admin adds an employee. The account appears in the list as active, and a temporary password is shown once; reloading the page does not show it again.
2. The new user logs in with it and lands on the change-password page. Any other page redirects back there, any other API call returns `403` `password_change_required`, and logging in again with the temporary password before changing it still works.
3. After the user sets a password, the temporary password fails at login, and the user reaches the office frame.
4. A temporary password not used within 72 hours fails at login; a reset makes a new one that works.
5. A reset, log-out, role change or deactivation ends that account's open sessions: its next request goes to the login page.
6. An office session still expires after 8 hours (spec.md criterion 2 for office stays).
7. While a user is using the site, the accounts screen shows them online; 6 minutes after their last request it shows them offline, with that time as last seen.
8. A non-owner admin cannot give or remove the admin role, or reset, log out, deactivate or change the role of any admin account (the owner and themselves included): the API returns `403` and nothing changes. Nobody can deactivate or demote the owner, and nobody can change their own role or deactivate themselves.
9. `Hung` cannot be created when `hung` exists, active or deactivated, and `hung` can log in by typing `Hung`.
10. A username with a space, a Vietnamese letter, or fewer than 3 or more than 32 characters is refused with a Vietnamese message.
11. Each account action writes a history entry an admin can see; an employee or boss requesting the history gets `403`.
12. The role picker offers no `operator`, and the API refuses `operator` in both creating an account and changing a role; an existing operator account still logs in and uploads.
13. An employee, a boss and an admin can upload a photo from Tải ảnh lên, and the sheet records them as the uploader.
14. The sidebar shows Tài khoản to admins only; a non-admin requesting the accounts page or `GET /api/admin/users` gets `403`.
15. The accounts screen and the frame work at phone width (360 px) without sideways scrolling: a test checks the viewport meta tag and the folded menu's markup; the verifier checks the layout at 360 px.
16. A fresh database with `create_user.py --owner` has exactly one owner; running it again for another admin moves the flag; the migration on an existing database marks the oldest active admin; a second owner cannot be stored.
17. With HTTPS on, the website serves only TLS; a plain HTTP request to port 8080 gets a `308` redirect to the same path on `https://<host>/` and never a login form (spec.md criterion 10); session cookies are `Secure`. Tested in the suite through the redirect app and `create_app` with HTTPS on.
18. On the Windows bundle CI job, a fresh install creates `admin` as the owner, and a fresh install and an update both end with `https://127.0.0.1/login` answering with a certificate that chains to the exported authority, `http://127.0.0.1:8080/login` redirecting, and `http://127.0.0.1:8080/ca.crt` serving that authority. The README gives the steps to trust it on Windows, Android and iPhone.

## 6. Design

- **Migration (job A):** one new plain-SQL file, no table rebuild (a rebuild of `users` would cascade-delete `sessions` and null `uploaded_by` and similar columns):
  - `ALTER TABLE users ADD COLUMN is_owner INTEGER NOT NULL DEFAULT 0`, `last_seen_at TEXT`, `temp_password_expires_at TEXT`.
  - `CREATE UNIQUE INDEX users_username_nocase ON users(username COLLATE NOCASE)`.
  - `CREATE UNIQUE INDEX users_one_owner ON users(is_owner) WHERE is_owner = 1`.
  - Mark the owner: the active admin with the lowest `id`.
  - Before applying this file (matched by its file name, before `executescript`, which is not atomic), the migration runner checks for usernames that differ only by case and stops with a message naming them, instead of a raw `IntegrityError`.
- **Temporary password:** `temp_password_expires_at` is set (now + 72 h) whenever a temporary password is made (add, reset, `create_user.py` without `--password` or with `--owner`), cleared when the user sets their own password, and checked by both login routes.
- **Ending sessions:** every action in "Accounts screen" that ends sessions deletes that user's rows in `sessions`, as `POST /api/admin/users/{id}/logout` does today.
- **Last seen:** the dependency that resolves the logged-in user writes `last_seen_at` at most once a minute per account, so last seen is accurate to one minute. A new `now()` helper becomes the single source of the current time for sessions, temporary-password expiry and last seen (replacing the direct `datetime.now` calls there), so tests replace it and criteria 4, 6 and 7 need no waiting.
- **Events:** account actions reuse `events`: `user_id` is the admin who acted, `action` is one of `account_add`, `account_reset`, `account_role`, `account_deactivate`, `account_reactivate`, `account_logout`, `field` is `user:<target id>`, and `old_value`/`new_value` hold: the new role on add (`new_value`), the old and new role on a role change, `1`/`0` on deactivate and reactivate, nothing on reset and log-out. `sheet_id` is null. The history page joins `users` to show both usernames. Passwords never appear in an event.
- **Routes:** pages `/admin/accounts` and `/admin/accounts/history`; API `GET /api/admin/users` (the list) and `GET /api/admin/users/events` (the history, newest first). `POST` and `PATCH` enforce the owner and username rules and refuse `operator`. `upload_photo`, `view_own_uploads` and `view_limits` extend to employee, boss and admin, so the upload screen works inside the frame.
- **Pages:** server-rendered with htmx, like the office pages; the frame is a shared base template.
- **HTTPS (job B):**
  - The installer makes a certificate authority (kept) and a certificate for the PC's name and current addresses, in `C:\HASC\config` as `ca.crt`, `ca.key`, `server.crt`, `server.key` (encrypted to `hasc`). An update makes a new PC certificate when the addresses changed. Job B may add `cryptography` to `requirements.txt` for this.
  - `hasc-web` runs uvicorn with TLS on port 443 (`--ssl-keyfile`, `--ssl-certfile`).
  - A third service, `hasc-redirect`, runs a small app (`hasc.web.redirect`) on port 8080. It answers `/ca.crt` with the file named by `HASC_CA_CERT` (so a phone can download it) and every other request with `308` to `https://<host without port>/<path>?<query>`. It runs as `hasc` with the same NSSM settings as the other two services.
  - `create_app` reads `HASC_HTTPS=1` from the environment to set `secure_cookies`; the redirect app replaces Starlette's `HTTPSRedirectMiddleware`, which would keep port 8080.
  - The installer's health check, printed addresses, shortcuts and firewall rule (443 and 8080) change to match, and its fresh-database path creates the first admin with `create_user.py --owner`.
  - `.github/workflows/windows-bundle.yml` logs in over HTTPS: it imports the exported authority into the runner's trusted roots, then calls `https://127.0.0.1/api/login`.

## 7. Alternatives considered

- **Sending the password by SMS or Zalo (rejected, P138):** no admin in the loop, but a paid outside service, a phone number per user, and a failure path. Office staff sit next to Lystiger.
- **New accounts with an empty password set at first login (rejected):** nothing to hand over, but anyone who learns the username before the person logs in takes the account.
- **One account with several roles and a switch (rejected, P128):** flexible, but every permission check has to know the active role.
- **QR upload in this release (deferred to 1.2.0):** needed before operator accounts can go, but it brings the trưởng ca roster, phone identity and the read cap, each with open questions.

## 8. Risks

- **The admin screen shows a temporary password** on a screen others might see. It is shown once, works only until the person sets their own, and expires in 72 hours.
- **Trusting the certificate on every device (P141, P143).** Each office PC and every phone, factory phones included, must trust the office PC's certificate once, at the 1.1.0 update; a device that skips it shows a security warning. If the PC's address changes, phones keep working only if the PC keeps its fixed address (DHCP reservation).
- **Two old usernames differing only by case** stop the migration; the admin renames one first.
- **The owner forgets their password:** recovery needs the `hasc` password and the PC.

## 9. Test plan

Unit and API tests with made-up data only: one test per acceptance criterion, the owner rules for every account action, the migration on a database with mixed-case usernames, and session ending for each listed action. Job B adds suite tests for the redirect app (`308` with path and query, `/ca.crt`) and `Secure` cookies with `HASC_HTTPS=1`. CI runs the suite on Linux and Windows, and the Windows bundle job installs the build, checks HTTPS and the owner (criterion 18) and logs in over HTTPS.

## 10. Rollout

1. **Job A** (Hermes): accounts screen, owner, history, online status, frame and username rules (acceptance criteria 1–16). PR, CI, independent verifier, merge.
2. **Job B** (Hermes): HTTPS in the app and the Windows installer (criteria 17–18). Same gates.
3. Tag `v1.1.0`; CI attaches the Windows bundle.
4. Lystiger runs **HASC update** on the office PC (data kept) and checks the owner flag is on their account.
5. Lystiger installs the certificate on each office PC and factory phone (open `http://<pc>:8080/ca.crt`), following the README (P143).

## Decisions (Lystiger, 2026-11-06)

| # | Decision |
|---|---|
| P123 | 1.1.0 covers add, deactivate, reactivate, reset password and change role from a screen. |
| P124 | Office and admin pages get an ERP-style frame for later modules. Entries appear only when their module ships. |
| P125 | A temporary password forces the user to set their own. *(Sending replaced by P138; "one login" replaced by P144.)* |
| P126 | The first admin (Lystiger) is the owner: cannot be deactivated or demoted, and is the only one who can give or remove the admin role. |
| P127 | `employee` is shown as "Văn phòng". Usernames are case-insensitive; a deactivated username returns only by reactivating that account. |
| P128 | Employee and boss can also upload (option D3), under their own account. |
| P129 | *Superseded by P134: there are no operator sessions to keep.* Office sessions stay at 8 hours. |
| P130 | Account changes are recorded; only admins see them. |
| P131 | The accounts screen shows which accounts are online. |
| P132 | *Superseded by P141.* |
| P133 | An unused temporary password expires after 72 hours; "online" is a request in the last 5 minutes; usernames are 3–32 characters from `a-z`, `0-9`, `.`, `_`. |
| P134 | Operators get no accounts. A QR code posted in the factory opens the upload page with no login (1.2.0). |
| P135 | An upload from the QR page counts as that shift's trưởng ca, and records the phone it came from (1.2.0). |
| P136 | A system-wide cap on AI reads a day, with an alert to Lystiger; about 40 sheets a day are expected (1.2.0; the number is U8). |
| P137 | No per-device lock for wrong uploads until junk uploads actually happen. |
| P138 | Lystiger hands office passwords over in person. The system shows the temporary password to the admin once. Replaces sending by SMS or Zalo (U1 closed). |
| P139 | 1.1.0 is the accounts screen and the office frame only; the QR upload page, trưởng ca attribution and the read cap are 1.2.0. |
| P141 | HTTPS ships in 1.1.0 (closes U11). |
| P143 | Factory phones still on operator accounts trust the certificate at the 1.1.0 update, like office PCs; everything is HTTPS. |
| P144 | A temporary password works until the user sets their own, for at most 72 hours (replaces "one login" in P125). |
| P145 | Admin accounts also get Tải ảnh lên, with employee and boss. |
| P142 | Role labels on the accounts screen: `employee` Văn phòng, `boss` Ban giám đốc, `admin` Quản trị, `operator` Xưởng (closes U6). |
| P140 | The role picker no longer offers `operator`; existing operator accounts keep working until deactivated. |

## UNDECIDED

| # | Question | Why it matters | Needed by |
|---|---|---|---|
| U8 | The daily read cap: 100 a day? What happens at the cap: refuse uploads, or queue them unread until the next day? | 1.2.0 design. | 1.2.0 |
| U9 | Where the trưởng ca of each shift comes from: a fixed person per shift that the admin sets, or a roster per date? | 1.2.0 attribution. | 1.2.0 |
| U10 | Does the QR link carry a secret the admin can change, so a link that leaks outside the factory can be cut off? | 1.2.0 security. | 1.2.0 |
| U7 | Store the cost of each AI read (`usage.cost`, returned today but dropped)? | Cheap to add; makes the cap and a monthly bill visible. | 1.2.0 |
