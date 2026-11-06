# HASC Production Records 1.0.1: accounts screen and office frame

Date: 2026-11-06. Decisions by Lystiger (P123–P131). Builds on `spec.md` (v1.0.0); where this file and `spec.md` differ, this file wins for 1.0.1. Items marked **UNDECIDED** need Lystiger before the build step that uses them.

## 1. Problem

v1.0.0 has the account API (`POST /api/admin/users`, `PATCH /api/admin/users/{id}`, `POST /api/admin/users/{id}/logout`) but no screen. Adding a person means running `scripts/create_user.py` on the server and passing the temporary password on by hand. Some office staff also upload sheets on some shifts, and today that needs a second, operator account.

## 2. Goals

1. The admin adds, deactivates, reactivates, resets and changes the role of accounts from a screen (P123).
2. The temporary password reaches the new user's phone without the admin reading it out (P125).
3. Office staff can upload a sheet from their own account (P128).
4. Office and admin pages share one navigation frame that later modules can join (P124).
5. The website runs on HTTPS on the local network, as spec.md P67 already decided (P132).

## 3. Non-goals

- Dashboard, purchase orders, production plan or any other module. The frame gets a sidebar entry only when its module ships (P124).
- Self-service sign-up or "forgot password". Only the admin creates and resets accounts.
- Forced periodic password changes (NIST SP 800-63B advises against them).
- Deleting accounts. Deactivation keeps the person's history (spec.md P47 stays).

## 4. Requirements

### Roles and names (P127)

| Code | Shown as | Notes |
|---|---|---|
| `operator` | **UNDECIDED U6** | unchanged |
| `employee` | Văn phòng | unchanged rights, new display name |
| `boss` | **UNDECIDED U6** | unchanged |
| `admin` | **UNDECIDED U6** | see the owner rule |

The role table in spec.md section 4 stays, with one change: **employee and boss may also upload a photo** (the operator upload screen, reached from the frame). The upload is recorded under their own account (P128).

### Owner (P126)

- Exactly one account is the **owner** (Lystiger). It cannot be deactivated, demoted, reset or logged out by anyone else.
- Only the owner can give the admin role or take it away. Other admins can add and manage operator, employee and boss accounts only.
- If the owner loses their password, recovery is `scripts/create_user.py` on the server (a new `--owner` reset path), not the website.

### Accounts screen (admin only)

- A list of every account: username, role, phone, active or deactivated, **online** (see below), last seen, created.
- **Add:** username, role, phone number. The system creates the account with a temporary password and sends it to the phone (P125). The admin never sees the password unless sending fails (see UNDECIDED U1).
- **Reset password:** makes a new temporary password, sends it, and ends that account's sessions.
- **Change role:** takes effect on the account's next request; its sessions end (P129).
- **Deactivate / reactivate:** a deactivated account cannot log in and its sessions end. Reactivating keeps the same username, history and role (P127).
- **Log out:** ends that account's sessions (exists in v1.0.0).
- **Account history:** every add, reset, role change, deactivation, reactivation and log-out is an event with who, what, old and new value, and when. Only admins see it (P130).

### Usernames (P127)

- Case-insensitive and unique: `Hung` and `hung` are the same account.
- A username is never given to a new account, even after deactivation. The way back is reactivating the old one.
- 3–32 characters from `a-z`, `0-9`, `.` and `_`; letters are stored in lower case (P133).

### Temporary password and first login (P125)

- A temporary password works for one login only. After that login the user is sent to the change-password page and can open nothing else until they set their own password.
- Once the user has set a password, the temporary password no longer works.
- An unused temporary password stops working 72 hours after it was sent (P133).

### Sessions (P129)

| Role | Session ends when |
|---|---|
| `operator` | their password changes, the admin resets it, logs them out, changes their role or deactivates them. No time limit. |
| `employee`, `boss`, `admin` | after 8 hours (spec.md P45, unchanged), or any of the operator events. |

### Online status (P131)

- An account is **online** when it made a request in the last 5 minutes (P133). The screen shows "last seen" for the rest.

### Office frame (P124)

- One layout for employee, boss and admin pages: a header with the user and log-out, and a sidebar with today's items only: **Hồ sơ** (the sheet list), **Báo cáo ngày**, **Tải ảnh lên** (P128), and **Tài khoản** (admin only).
- Operators keep their current phone screen, without the frame.

## 5. Acceptance criteria

1. The admin adds an operator with a phone number. The account appears in the list as active, and a text with the username and a temporary password reaches that phone.
2. The new user logs in with the temporary password and lands on the change-password page. Any other page or API call returns them to that page (or `403` for the API).
3. After the user sets a password, the temporary password fails at login, and the user reaches their workstation.
4. An operator who changed their password is still logged in 100 days later on the same device, and logged out on their next request after their password changes, the admin resets it, logs them out, changes their role or deactivates them.
5. An office session still expires after 8 hours (spec.md criterion 2 for office stays).
6. While a user is using the site, the accounts screen shows them online; 6 minutes after their last request it shows them offline with that time as last seen.
7. A non-owner admin cannot give or remove the admin role, or deactivate, reset or log out the owner: the API returns `403` and nothing changes. Nobody can deactivate or demote the owner.
8. `Hung` cannot be created when `hung` exists, active or deactivated.
9. Each account action in section 4 writes an event an admin can see; an employee or boss requesting the account history gets `403`.
10. An employee and a boss can upload a photo, and the sheet records them as the uploader.
11. The sidebar shows Tài khoản to admins only; a non-admin requesting the accounts page gets `403`.
12. A temporary password not used within 72 hours fails at login; a reset sends a new one.
13. A username with a space, a Vietnamese letter, or fewer than 3 or more than 32 characters is refused with a Vietnamese message.
14. Every page and API call is served over HTTPS with the Dell's certificate; a plain HTTP request is redirected and never reaches a login form (spec.md criterion 10).

## 6. Design

- **Migration:** `users` gains `phone TEXT`, `is_owner INTEGER NOT NULL DEFAULT 0` (exactly one row set), `last_seen_at TEXT`, `password_changed_at TEXT`, and `temp_password_used INTEGER NOT NULL DEFAULT 0`; `username` uniqueness becomes case-insensitive (`COLLATE NOCASE`). The migration fails if two existing usernames differ only by case.
- **Owner:** the migration marks the oldest admin as owner; `scripts/create_user.py --owner` sets or resets it from the server.
- **Sessions:** `sessions.expires_at` becomes nullable for operators. Session checks also compare `sessions.created_at` with `users.password_changed_at` and the latest role change, so one update ends every session of that account.
- **Last seen:** updated at most once a minute per account, to avoid a write on every request.
- **Sending the password:** one `send_temp_password(phone, username, password)` function behind an interface, so the provider can change (U1). Tests use a fake sender.
- **Events:** account actions reuse the `events` table with `action` values `account_add`, `account_reset`, `account_role`, `account_deactivate`, `account_reactivate`, `account_logout`; `sheet_id` is null and `detail` names the target account.
- **API:** adds `GET /api/admin/users` and `GET /api/admin/users/events`; `PATCH` gains `phone` and enforces the owner rules.

## 7. Alternatives considered

- **One account with several roles and a switch (rejected, P128):** flexible, but every permission check has to know the active role.
- **Two accounts per person (rejected, P128):** simple and a clean audit, but staff dislike it.
- **Showing the temporary password to the admin and sending it by hand (rejected, P125):** free and no new dependency, but Lystiger prefers the system to send it.
- **Sidebar entries for future modules now (rejected, P124):** would ship pages nobody uses and fix a layout before those modules exist.

## 8. Risks

- **Installing the certificate on every device (P67).** Each phone and office computer must trust the Dell's certificate once, and a phone that skips it shows a security warning. The README server setup (plain HTTP today) is updated in the same release.
- **A lost operator phone** stays logged in until the admin logs the account out or resets it. The accounts screen and "last seen" make that a one-step action.
- **SMS or Zalo sending fails or costs more than expected.** See U1.
- **Two old usernames differing only by case** stop the migration; the admin renames one first.

## 9. Test plan

Unit and API tests with the fake reader and a fake sender, made-up data only: one test per acceptance criterion 1–11, plus the owner rules for every account action, the migration on a database with mixed-case usernames, and session ending for each listed event.

## 10. Rollout

1. Settle U1 and U6.
2. Build on a branch as one Hermes job; merge after CI and an independent verifier.
3. Deploy to the Dell (Lystiger), run the migration, and check the owner flag is on Lystiger's account.
4. Tag `v1.0.1`.

## Decisions (Lystiger, 2026-11-06)

| # | Decision |
|---|---|
| P123 | 1.0.1 covers add, deactivate, reactivate, reset password and change role from a screen. |
| P124 | Office and admin pages get an ERP-style frame, so later modules (dashboard, PO, production plan) can join it. Entries appear only when their module ships (Claude's recommendation, to confirm). |
| P125 | The system sends the temporary password to the user's phone. It works for one login, then the user must set their own password. |
| P126 | The first admin (Lystiger) is the owner: cannot be deactivated or demoted, and is the only one who can give or remove the admin role. |
| P127 | `employee` is shown as "Văn phòng". Usernames are case-insensitive; a deactivated username returns only by reactivating that account. |
| P128 | Employee and boss can also upload (option D3), under their own account. |
| P129 | Operator devices stay logged in until the password changes (or a reset, log-out, role change or deactivation). Office sessions stay at 8 hours. |
| P130 | Account changes are recorded; only admins see them. |
| P131 | The accounts screen shows which accounts are online. |
| P132 | HTTPS (spec.md P67) ships with 1.0.1, because operator sessions no longer expire. |
| P133 | An unused temporary password expires after 72 hours; "online" is a request in the last 5 minutes; usernames are 3–32 characters from `a-z`, `0-9`, `.`, `_`. |

## UNDECIDED

| # | Question | Why it matters | Needed by |
|---|---|---|---|
| U1 | SMS or Zalo, which provider, and what happens when sending fails (show the password to the admin once, or retry)? | Cost per message, a stored phone number per user, and a new outside service that can fail. Zalo needs a Zalo Official Account; SMS needs a gateway with a sender name. | Build |
| U6 | Vietnamese names for operator, boss and admin on the screen (the UI shows no role names today). | The accounts screen and the role picker show them. | Build |
