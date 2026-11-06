# HASC Production Records 1.1.0: accounts screen and office frame

Date: 2026-11-06. Decisions by Lystiger (P123–P141). Builds on `spec.md` and v1.0.2 (production on the Windows office PC, ADR 8). Where this file and `spec.md` differ, this file wins for 1.1.0. Items marked **UNDECIDED** need Lystiger before the build step that uses them; items marked *(Claude's recommendation, to confirm)* are proposals Lystiger has not yet agreed.

## 1. Problem

v1.0.2 has an account API (`POST /api/admin/users`, `PATCH /api/admin/users/{id}`, `POST /api/admin/users/{id}/logout`) but no screen and no list. On the office PC the database is encrypted to the `hasc` service account, so `scripts/create_user.py` no longer works from an administrator window; adding a person today means PowerShell calls against the API. The admin cannot see who has an account, who is active, or who is online.

## 2. Goals

1. The admin adds, deactivates, reactivates, resets and changes the role of office accounts from a screen (P123).
2. The admin hands each new person their first password in person; the person then sets their own (P138).
5. The website runs on HTTPS on the office network (spec.md P67, P141).
3. One office frame that later modules (dashboard, purchase orders, production plan) can join (P124).
4. Office staff can upload a sheet from their own account (P128).

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
| `boss` | **UNDECIDED U6** | yes |
| `admin` | **UNDECIDED U6** | owner only (see Owner) |
| `operator` | **UNDECIDED U6** | no: existing operator accounts keep working until deactivated, so the factory keeps uploading until 1.2.0 ships (P140) |

Pages other than the accounts screen show no role names ("show no roles", Lystiger). The role table in spec.md section 4 stays, with one change: **employee and boss may also upload a photo**, recorded under their own account (P128).

### Owner (P126)

- Exactly one account is the **owner** (Lystiger). Nobody else can deactivate, demote, reset or log it out.
- Only the owner can give or take away the admin role. Other admins manage employee, boss and operator accounts only.
- If the owner loses their password, recovery is a script run as `hasc` on the PC (`scripts/create_user.py --owner`, documented in the README), not the website.

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

- Case-insensitive and unique: `Hung` and `hung` are the same account.
- A username is never reused, even after deactivation. The way back is reactivating the old account.
- 3–32 characters from `a-z`, `0-9`, `.` and `_`; stored in lower case. Refusals are in Vietnamese.

### Temporary password and first login (P125, P133)

- A temporary password works for one login. After it the user is sent to the change-password page and can open nothing else until they set their own password.
- Once the user has set a password, the temporary password no longer works.
- An unused temporary password stops working 72 hours after it was made.

### Sessions

Office sessions (employee, boss, admin) last 8 hours (spec.md P45, unchanged), and also end when the admin resets the password, logs the account out, changes its role or deactivates it.

### Online status (P131, P133)

An account is **online** when it made a request in the last 5 minutes. The screen shows "last seen" for the rest.

### Office frame (P124)

- One layout for employee, boss and admin pages: a header with the user's name and log-out, and a sidebar with today's items only: **Hồ sơ** (the sheet list), **Báo cáo ngày**, **Tải ảnh lên** (P128), and **Tài khoản** (admins only).
- On a phone the sidebar folds into a menu button.
- Operators keep their current phone screen, without the frame.

## 5. Acceptance criteria

1. The admin adds an employee. The account appears in the list as active, and a temporary password is shown once; reloading the page does not show it again.
2. The new user logs in with it and lands on the change-password page. Any other page sends them back there, and any other API call returns `403`.
3. After the user sets a password, the temporary password fails at login, and the user reaches the office frame.
4. A temporary password not used within 72 hours fails at login; a reset makes a new one that works.
5. A reset, log-out, role change or deactivation ends that account's open sessions: its next request goes to the login page.
6. An office session still expires after 8 hours (spec.md criterion 2 for office stays).
7. While a user is using the site, the accounts screen shows them online; 6 minutes after their last request it shows them offline, with that time as last seen.
8. A non-owner admin cannot give or remove the admin role, or deactivate, reset or log out the owner: the API returns `403` and nothing changes. Nobody can deactivate or demote the owner.
9. `Hung` cannot be created when `hung` exists, active or deactivated.
10. A username with a space, a Vietnamese letter, or fewer than 3 or more than 32 characters is refused with a Vietnamese message.
11. Each account action writes a history entry an admin can see; an employee or boss requesting the history gets `403`.
12. The role picker offers no `operator`; an existing operator account still logs in and uploads.
13. An employee and a boss can upload a photo, and the sheet records them as the uploader.
14. The sidebar shows Tài khoản to admins only; a non-admin requesting the accounts page or `GET /api/admin/users` gets `403`.
15. The accounts screen and the frame work at phone width (360 px) without sideways scrolling.
16. Every page and API call is served over HTTPS with a certificate the installer makes for the office PC; a plain HTTP request is redirected and never reaches a login form (spec.md criterion 10). Session cookies are `Secure`.
17. A fresh `install.cmd` and an update both end with HTTPS working, and the installer gives the certificate file to install on office PCs and phones, with steps for Windows, Android and iPhone.

## 6. Design

- **Migration:** `users` gains `is_owner INTEGER NOT NULL DEFAULT 0` (exactly one row set), `last_seen_at TEXT`, `temp_password_expires_at TEXT`; `username` uniqueness becomes case-insensitive (`COLLATE NOCASE`). The migration fails with a clear message if two existing usernames differ only by case. It marks the oldest active admin as owner.
- **Ending sessions:** every action in "Accounts screen" that ends sessions deletes that user's rows in `sessions`, as `POST /api/admin/users/{id}/logout` does today.
- **Last seen:** updated at most once a minute per account, to avoid a database write on every request.
- **Events:** account actions reuse the `events` table with `action` values `account_add`, `account_reset`, `account_role`, `account_deactivate`, `account_reactivate`, `account_logout`; `sheet_id` is null and `detail` names the target account and the old and new values. Passwords never appear in an event.
- **API:** adds `GET /api/admin/users` (the list) and `GET /api/admin/users/events` (the history); `PATCH` enforces the owner rules; `POST` refuses `operator` and enforces the username rules.
- **Pages:** server-rendered with htmx, like the office pages; the frame is a shared base template.
- **HTTPS:** the installer makes a small certificate authority and a certificate for the PC's name and addresses (kept in `C:\HASC\config`, encrypted to `hasc`), and exports only the authority's public certificate for devices to trust. uvicorn serves TLS on port 443 and a plain listener on 8080 only redirects to it; the firewall rule opens both. `create_app` takes `enforce_https` and `secure_cookies` from the environment.

## 7. Alternatives considered

- **Sending the password by SMS or Zalo (rejected, P138):** no admin in the loop, but a paid outside service, a phone number per user, and a failure path. Office staff sit next to Lystiger.
- **New accounts with an empty password set at first login (rejected):** nothing to hand over, but anyone who learns the username before the person logs in takes the account.
- **One account with several roles and a switch (rejected, P128):** flexible, but every permission check has to know the active role.
- **QR upload in this release (deferred to 1.2.0):** needed before operator accounts can go, but it brings the trưởng ca roster, phone identity and the read cap, each with open questions.

## 8. Risks

- **The admin screen shows a temporary password** on a screen others might see. It is shown once, works once, and expires in 72 hours.
- **Trusting the certificate on every device (P141).** Each office PC and phone must trust the office PC's certificate once; a device that skips it shows a security warning. Factory phones meet it in 1.2.0 with the QR page.
- **Two old usernames differing only by case** stop the migration; the admin renames one first.
- **The owner forgets their password:** recovery needs the `hasc` password and the PC.

## 9. Test plan

Unit and API tests with made-up data only: one test per acceptance criterion, the owner rules for every account action, the migration on a database with mixed-case usernames, and session ending for each listed action. CI runs them on Linux and Windows, and the Windows bundle job installs the build and logs in.

## 10. Rollout

1. Settle U6.
2. Build on a branch as one Hermes job; merge after CI and an independent verifier.
3. Tag `v1.1.0`; CI attaches the Windows bundle.
4. Lystiger runs `install.cmd` from the new bundle on the office PC (an update: data kept) and checks the owner flag is on their account.

## Decisions (Lystiger, 2026-11-06)

| # | Decision |
|---|---|
| P123 | 1.1.0 covers add, deactivate, reactivate, reset password and change role from a screen. |
| P124 | Office and admin pages get an ERP-style frame for later modules. Entries appear only when their module ships. |
| P125 | A temporary password works for one login, then the user must set their own. *(Its sending part is replaced by P138.)* |
| P126 | The first admin (Lystiger) is the owner: cannot be deactivated or demoted, and is the only one who can give or remove the admin role. |
| P127 | `employee` is shown as "Văn phòng". Usernames are case-insensitive; a deactivated username returns only by reactivating that account. |
| P128 | Employee and boss can also upload (option D3), under their own account. |
| P129 | *Superseded by P134: there are no operator sessions to keep.* Office sessions stay at 8 hours. |
| P130 | Account changes are recorded; only admins see them. |
| P131 | The accounts screen shows which accounts are online. |
| P132 | HTTPS (spec.md P67) ships with this release. *Its reason (long operator sessions) is gone with P134; see U11.* |
| P133 | An unused temporary password expires after 72 hours; "online" is a request in the last 5 minutes; usernames are 3–32 characters from `a-z`, `0-9`, `.`, `_`. |
| P134 | Operators get no accounts. A QR code posted in the factory opens the upload page with no login (1.2.0). |
| P135 | An upload from the QR page counts as that shift's trưởng ca, and records the phone it came from (1.2.0). |
| P136 | A system-wide cap on AI reads a day, with an alert to Lystiger; about 40 sheets a day are expected (1.2.0; the number is U8). |
| P137 | No per-device lock for wrong uploads until junk uploads actually happen. |
| P138 | Lystiger hands office passwords over in person. The system shows the temporary password to the admin once. Replaces sending by SMS or Zalo (U1 closed). |
| P139 | 1.1.0 is the accounts screen and the office frame only; the QR upload page, trưởng ca attribution and the read cap are 1.2.0. |
| P141 | HTTPS ships in 1.1.0 (closes U11). |
| P140 | The role picker no longer offers `operator`; existing operator accounts keep working until deactivated. |

## UNDECIDED

| # | Question | Why it matters | Needed by |
|---|---|---|---|
| U6 | Vietnamese names for `boss`, `admin` and `operator` on the accounts screen. You said "show no roles": this spec reads that as "no role names anywhere except the accounts screen", where the role picker needs labels. | The role picker and the list show them. | Build |
| U8 | The daily read cap: 100 a day? What happens at the cap: refuse uploads, or queue them unread until the next day? | 1.2.0 design. | 1.2.0 |
| U9 | Where the trưởng ca of each shift comes from: a fixed person per shift that the admin sets, or a roster per date? | 1.2.0 attribution. | 1.2.0 |
| U10 | Does the QR link carry a secret the admin can change, so a link that leaks outside the factory can be cut off? | 1.2.0 security. | 1.2.0 |
| U7 | Store the cost of each AI read (`usage.cost`, returned today but dropped)? | Cheap to add; makes the cap and a monthly bill visible. | 1.2.0 |
