from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import os
from pathlib import Path
import sqlite3
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware

from hasc.db import (
    create_session,
    update_user_last_seen,
    create_user,
    delete_session,
    delete_user_sessions,
    get_connection,
    get_default_photos_dir,
    get_photo_by_id,
    get_user_by_id,
    get_user_by_username,
    init_db,
    set_user_active,
    update_user_password,
)
from hasc.excel import build_daily_report, daily_report_filename
from hasc.web.accounts import (
    create_account,
    list_account_events,
    list_accounts_with_status,
    logout_account,
    patch_account,
)
from hasc.web.auth import (
    clear_session_cookie,
    get_active_user,
    get_authenticated_user,
    get_current_user_optional,
    get_db,
    get_session_id_from_request,
    set_session_cookie,
)
from hasc.web.errors import AppError
from hasc.web.roles import require_role
from hasc.web import security
from hasc.web.ai_stats import budget_alert, vnd
from hasc.web.update_status import read_update_status
from hasc.web.security import (
    generate_session_id,
    generate_temporary_password,
    get_session_duration,
    hash_password,
    is_session_expired,
    new_password_problem,
    temp_password_expires_at,
    verify_password,
)


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChangeRequest(BaseModel):
    old: str
    new: str


class CreateUserRequest(BaseModel):
    username: str
    role: str


class PatchUserRequest(BaseModel):
    active: Optional[int] = None
    role: Optional[str] = None
    reset_password: Optional[bool] = None


VN_TZ = timezone(timedelta(hours=7))  # Vietnam has no daylight saving


def vn_time(value) -> str:
    """Stored UTC time as the factory reads it: 04/11/2026 22:31. A time without a zone is already local."""
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if moment.tzinfo:
        moment = moment.astimezone(VN_TZ)
    return moment.strftime("%d/%m/%Y %H:%M")


def vn_date(value) -> str:
    """2026-10-30 as 30/10/2026."""
    try:
        return date.fromisoformat(str(value)).strftime("%d/%m/%Y")
    except ValueError:
        return str(value or "")


def create_app(
    db_path: str | Path | None = None,
    enforce_https: bool = False,
    secure_cookies: bool = False,
    photos_dir: str | Path | None = None,
) -> FastAPI:
    """FastAPI application factory.
    - Runs migrations at start-up.
    - Mounts static files and templates.
    - Configures session authentication and role enforcement.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: Run database migrations
        init_db(app.state.db_path).close()
        yield
        # Shutdown logic if needed

    app = FastAPI(
        title="HASC Production Records",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    effective_db_path = db_path if db_path is not None else os.environ.get("HASC_DB_PATH")
    app.state.db_path = effective_db_path
    app.state.photos_dir = get_default_photos_dir(photos_dir=photos_dir, db_path=effective_db_path)
    # On the office PC the website runs on HTTPS (HASC_HTTPS=1, set by the installer), so session
    # cookies are only ever sent over TLS.
    app.state.secure_cookies = secure_cookies or os.environ.get("HASC_HTTPS") == "1"
    app.state.enforce_https = enforce_https

    if enforce_https:
        app.add_middleware(HTTPSRedirectMiddleware)

    # Mount static files
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Mount templates
    templates_dir = Path(__file__).resolve().parent / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))
    templates.env.filters["vntime"] = vn_time
    # Phones keep the stylesheet and script cached; a new version in their URL makes them fetch changed ones.
    assets = [static_dir / "css" / "app.css", static_dir / "js" / "ui.js"]
    digest = hashlib.sha256(b"".join(a.read_bytes() for a in assets if a.is_file()))
    templates.env.globals["css_version"] = digest.hexdigest()[:10]
    templates.env.filters["vndate"] = vn_date
    # Admin pages: the installed version and, when the daily check found one, a newer release.
    templates.env.globals["update_status"] = read_update_status
    templates.env.globals["ai_budget"] = budget_alert
    templates.env.filters["vnd"] = vnd
    templates.env.globals["app_version"] = os.environ.get("HASC_VERSION", "")
    app.state.templates = templates

    def go_to(request: Request, url: str) -> Response:
        """Send the browser to another page. An htmx request (a list polling every few seconds) would
        follow a redirect and put the target page inside the list, so it gets HX-Redirect instead."""
        if request.headers.get("HX-Request") == "true":
            return Response(status_code=200, headers={"HX-Redirect": url})
        return RedirectResponse(url=url, status_code=303)

    # Exception handlers conforming to spec error shape:
    # {"error": {"code": "<code>", "message": "<Vietnamese text>"}}
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        if not request.url.path.startswith("/api/"):
            if exc.code == "password_change_required":
                return go_to(request, "/change-password")
            if exc.status_code == 401:
                return go_to(request, "/login")
        return exc.to_response()

    DEFAULT_HTTP_ERRORS = {
        400: ("bad_request", "Yêu cầu không hợp lệ"),
        401: ("unauthorized", "Chưa đăng nhập"),
        403: ("forbidden", "Không có quyền truy cập"),
        404: ("not_found", "Không tìm thấy"),
        405: ("method_not_allowed", "Phương thức không được hỗ trợ"),
        409: ("conflict", "Xung đột dữ liệu"),
        422: ("validation_error", "Dữ liệu không hợp lệ"),
        500: ("internal_error", "Lỗi máy chủ nội bộ"),
    }
    ENGLISH_HTTP_DEFAULTS = {
        "Not Found",
        "Unauthorized",
        "Forbidden",
        "Method Not Allowed",
        "Internal Server Error",
        "Bad Request",
        "Conflict",
        "Unprocessable Entity",
    }

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        # For browser navigation without API prefix, redirect 401 to /login
        if not request.url.path.startswith("/api/"):
            if exc.status_code == 401:
                return go_to(request, "/login")

        default_code, default_msg = DEFAULT_HTTP_ERRORS.get(
            exc.status_code, ("http_error", "Lỗi hệ thống")
        )
        code = default_code
        detail = exc.detail
        if not detail or str(detail) in ENGLISH_HTTP_DEFAULTS:
            message = default_msg
        else:
            message = str(detail)

        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": message}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "validation_error", "message": "Dữ liệu không hợp lệ"}},
        )

    # -------------------------------------------------------------------------
    # HTML Routes
    # -------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def home(
        request: Request,
        user: Optional[dict] = Depends(get_current_user_optional),
        conn: sqlite3.Connection = Depends(get_db),
    ):
        if not user:
            return RedirectResponse(url="/login", status_code=303)
        if user.get("must_change_password"):
            return RedirectResponse(url="/change-password", status_code=303)
        if user.get("role") == "operator":
            from hasc.web.operator import render_operator_screen
            return render_operator_screen(request, user, conn)
        if user.get("role") in ("employee", "boss", "admin"):
            from hasc.web.office import render_office_screen
            return render_office_screen(request, user, conn)

        return templates.TemplateResponse(
            request=request,
            name="base.html",
            context={
                "user": user,
            },
        )

    @app.get("/login", response_class=HTMLResponse)
    def login_page(
        request: Request,
        user: Optional[dict] = Depends(get_current_user_optional),
    ):
        if user and not user.get("must_change_password"):
            return RedirectResponse(url="/", status_code=303)

        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"username": "", "error": None},
        )

    @app.post("/login", response_class=HTMLResponse)
    def login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        conn: sqlite3.Connection = Depends(get_db),
    ):
        user = get_user_by_username(conn, username)
        if not user or not verify_password(password, user["password_hash"]):
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"username": username, "error": "Tên đăng nhập hoặc mật khẩu không chính xác."},
                status_code=401,
            )

        if user["active"] != 1:
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"username": username, "error": "Tài khoản đã bị vô hiệu hóa."},
                status_code=403,
            )

        if user["temp_password_expires_at"] and is_session_expired(user["temp_password_expires_at"]):
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"username": username, "error": "Mật khẩu tạm đã hết hạn. Hãy nhờ Quản trị đặt lại."},
                status_code=401,
            )

        # Create session
        session_id = generate_session_id()
        duration = get_session_duration(user["role"])
        expires_at = (security.now() + duration).isoformat()
        create_session(conn, session_id, user["id"], expires_at)
        # A login is use: the account can no longer be deleted, even after the session is purged.
        update_user_last_seen(conn, user["id"], security.now().isoformat())

        target_url = "/change-password" if user["must_change_password"] else "/"
        response = RedirectResponse(url=target_url, status_code=303)
        set_session_cookie(response, session_id, user["role"], secure=app.state.secure_cookies)
        return response

    @app.get("/change-password", response_class=HTMLResponse)
    def change_password_page(
        request: Request,
        user: dict = Depends(get_authenticated_user),
    ):
        return templates.TemplateResponse(
            request=request,
            name="change_password.html",
            context={"user": user, "error": None},
        )

    @app.post("/change-password", response_class=HTMLResponse)
    def change_password_submit(
        request: Request,
        old_password: str = Form(...),
        new_password: str = Form(...),
        confirm_password: str = Form(...),
        user: dict = Depends(get_authenticated_user),
        conn: sqlite3.Connection = Depends(get_db),
    ):
        if new_password != confirm_password:
            return templates.TemplateResponse(
                request=request,
                name="change_password.html",
                context={"user": user, "error": "Mật khẩu mới và xác nhận mật khẩu không khớp."},
                status_code=400,
            )

        if not verify_password(old_password, user["password_hash"]):
            return templates.TemplateResponse(
                request=request,
                name="change_password.html",
                context={"user": user, "error": "Mật khẩu hiện tại không chính xác."},
                status_code=400,
            )

        problem = new_password_problem(new_password, user["password_hash"])
        if problem:
            return templates.TemplateResponse(
                request=request,
                name="change_password.html",
                context={"user": user, "error": problem},
                status_code=400,
            )

        # Update password; any other place this account is logged in is logged out.
        new_hash = hash_password(new_password)
        update_user_password(conn, user["id"], new_hash, must_change_password=0)
        delete_user_sessions(conn, user["id"], keep=get_session_id_from_request(request))

        return RedirectResponse(url="/", status_code=303)

    @app.get("/logout")
    def logout_get(
        request: Request,
        conn: sqlite3.Connection = Depends(get_db),
    ):
        session_id = get_session_id_from_request(request)
        if session_id:
            delete_session(conn, session_id)
        response = templates.TemplateResponse(
            request=request,
            name="logout.html",
            context={},
        )
        clear_session_cookie(response)
        return response

    @app.post("/logout")
    def logout_post(
        request: Request,
        conn: sqlite3.Connection = Depends(get_db),
    ):
        session_id = get_session_id_from_request(request)
        if session_id:
            delete_session(conn, session_id)
        response = RedirectResponse(url="/login", status_code=303)
        clear_session_cookie(response)
        return response

    # -------------------------------------------------------------------------
    # API Routes: Authentication & Accounts
    # -------------------------------------------------------------------------

    @app.post("/api/login")
    def api_login(
        payload: LoginRequest,
        conn: sqlite3.Connection = Depends(get_db),
    ):
        user = get_user_by_username(conn, payload.username)
        if not user or not verify_password(payload.password, user["password_hash"]):
            raise AppError(
                code="invalid_credentials",
                message="Tên đăng nhập hoặc mật khẩu không đúng",
                status_code=401,
            )

        if user["active"] != 1:
            raise AppError(
                code="account_deactivated",
                message="Tài khoản đã bị vô hiệu hóa",
                status_code=403,
            )

        if user["temp_password_expires_at"] and is_session_expired(user["temp_password_expires_at"]):
            raise AppError(
                code="temp_password_expired",
                message="Mật khẩu tạm đã hết hạn. Hãy nhờ Quản trị đặt lại.",
                status_code=401,
            )

        session_id = generate_session_id()
        duration = get_session_duration(user["role"])
        expires_at = (security.now() + duration).isoformat()
        create_session(conn, session_id, user["id"], expires_at)
        # A login is use: the account can no longer be deleted, even after the session is purged.
        update_user_last_seen(conn, user["id"], security.now().isoformat())

        response = JSONResponse(
            status_code=200,
            content={
                "role": user["role"],
                "must_change_password": bool(user["must_change_password"]),
            },
        )
        set_session_cookie(response, session_id, user["role"], secure=app.state.secure_cookies)
        return response

    @app.post("/api/password", status_code=204)
    def api_change_password(
        request: Request,
        payload: PasswordChangeRequest,
        user: dict = Depends(get_authenticated_user),
        conn: sqlite3.Connection = Depends(get_db),
    ):
        if not verify_password(payload.old, user["password_hash"]):
            raise AppError(
                code="invalid_password",
                message="Mật khẩu cũ không chính xác",
                status_code=400,
            )
        problem = new_password_problem(payload.new, user["password_hash"])
        if problem:
            raise AppError(code="weak_password", message=problem, status_code=400)

        new_hash = hash_password(payload.new)
        update_user_password(conn, user["id"], new_hash, must_change_password=0)
        delete_user_sessions(conn, user["id"], keep=get_session_id_from_request(request))
        return Response(status_code=204)

    @app.post("/api/logout", status_code=204)
    def api_logout(
        request: Request,
        conn: sqlite3.Connection = Depends(get_db),
    ):
        session_id = get_session_id_from_request(request)
        if session_id:
            delete_session(conn, session_id)
        response = Response(status_code=204)
        clear_session_cookie(response)
        return response

    # -------------------------------------------------------------------------
    # API Routes: Admin User Management
    # -------------------------------------------------------------------------

    @app.get("/api/admin/users")
    def api_admin_list_users(
        admin: dict = Depends(require_role("admin")),
        conn: sqlite3.Connection = Depends(get_db),
    ):
        return list_accounts_with_status(conn)

    @app.get("/api/admin/users/events")
    def api_admin_list_user_events(
        admin: dict = Depends(require_role("admin")),
        conn: sqlite3.Connection = Depends(get_db),
    ):
        return list_account_events(conn)

    @app.post("/api/admin/users", status_code=201)
    def api_admin_create_user(
        payload: CreateUserRequest,
        admin: dict = Depends(require_role("admin")),
        conn: sqlite3.Connection = Depends(get_db),
    ):
        user_id, temp_password = create_account(
            conn=conn,
            actor=admin,
            username=payload.username,
            role=payload.role,
        )
        return JSONResponse(
            status_code=201,
            content={"id": user_id, "temporary_password": temp_password},
        )

    @app.patch("/api/admin/users/{user_id}")
    def api_admin_patch_user(
        user_id: int,
        payload: PatchUserRequest,
        admin: dict = Depends(require_role("admin")),
        conn: sqlite3.Connection = Depends(get_db),
    ):
        resp_data, temp_password = patch_account(
            conn=conn,
            actor=admin,
            target_user_id=user_id,
            active=payload.active,
            role=payload.role,
            reset_password=payload.reset_password,
        )
        if temp_password:
            resp_data["temporary_password"] = temp_password
        return resp_data

    @app.post("/api/admin/users/{user_id}/logout", status_code=204)
    def api_admin_logout_user(
        user_id: int,
        admin: dict = Depends(require_role("admin")),
        conn: sqlite3.Connection = Depends(get_db),
    ):
        logout_account(conn=conn, actor=admin, target_user_id=user_id)
        return Response(status_code=204)

    # -------------------------------------------------------------------------
    # Operator, Office & Account Pages Routes
    # -------------------------------------------------------------------------
    from hasc.web.operator import router as operator_router
    from hasc.web.office import router as office_router
    from hasc.web.account_pages import router as account_pages_router
    from hasc.web.self_account import router as self_account_router
    from hasc.web.corrections import router as corrections_router
    from hasc.web.ai_stats import router as ai_router
    from hasc.web.zalo_inbox import router as zalo_router

    app.include_router(operator_router)
    app.include_router(office_router)
    app.include_router(account_pages_router)
    app.include_router(self_account_router)
    app.include_router(corrections_router)
    app.include_router(ai_router)
    app.include_router(zalo_router)

    # -------------------------------------------------------------------------
    # API Routes: Section 4 Role Table Endpoints
    # Endpoints after /api/limits are not built in S1: they check the role, then answer 501.
    # -------------------------------------------------------------------------

    @app.get("/api/limits")
    def api_limits(
        user: dict = Depends(require_role("view_limits")),
    ):
        return {"thickness_max_mic": 8, "diameter_max_mm": 3}

    @app.get("/api/shifts/excel")
    def api_shifts_excel(
        user: dict = Depends(require_role("review_sheet")),
    ):
        raise AppError(code="not_implemented", message="Chức năng này chưa có trong bản S1", status_code=501)

    @app.get("/api/sheets/{sheet_id}/template")
    def api_sheet_template(
        sheet_id: int,
        user: dict = Depends(require_role("review_sheet")),
    ):
        raise AppError(code="not_implemented", message="Chức năng này chưa có trong bản S1", status_code=501)

    @app.post("/api/photos/{photo_id}/retry", status_code=202)
    def api_photo_retry(
        photo_id: int,
        user: dict = Depends(require_role("review_sheet")),
    ):
        raise AppError(code="not_implemented", message="Chức năng này chưa có trong bản S1", status_code=501)

    @app.post("/api/photos/{photo_id}/invalid")
    def api_photo_invalid(
        photo_id: int,
        user: dict = Depends(require_role("mark_invalid")),
    ):
        raise AppError(code="not_implemented", message="Chức năng này chưa có trong bản S1", status_code=501)

    @app.get("/api/photos/{photo_id}/download")
    @app.get("/photos/{photo_id}/download")
    def api_photo_download(
        photo_id: int,
        request: Request,
        user: dict = Depends(require_role("review_sheet")),  # role table row 4: download is office only
        conn: sqlite3.Connection = Depends(get_db),
    ):
        photo = get_photo_by_id(conn, photo_id)
        if not photo:
            raise AppError("not_found", "Không tìm thấy ảnh", 404)

        file_path = Path(photo["path"])
        if not file_path.is_file():
            photos_dir = getattr(request.app.state, "photos_dir", None)
            if not photos_dir:
                photos_dir = get_default_photos_dir(db_path=getattr(request.app.state, "db_path", None))
            if photos_dir:
                candidate = Path(photos_dir) / file_path.name
                if candidate.is_file():
                    file_path = candidate

        if not file_path.is_file():
            raise AppError("not_found", "Tệp ảnh không tồn tại trên đĩa", 404)

        return FileResponse(
            path=str(file_path),
            filename=file_path.name,
            media_type="application/octet-stream",
        )

    @app.post("/api/not-running", status_code=201)
    def api_not_running(
        user: dict = Depends(require_role("mark_not_running")),
    ):
        raise AppError(code="not_implemented", message="Chức năng này chưa có trong bản S1", status_code=501)

    @app.get("/api/history")
    def api_history(
        user: dict = Depends(require_role("view_history")),
    ):
        raise AppError(code="not_implemented", message="Chức năng này chưa có trong bản S1", status_code=501)

    @app.get("/api/admin/stats")
    def api_admin_stats(
        user: dict = Depends(require_role("view_stats")),
    ):
        raise AppError(code="not_implemented", message="Chức năng này chưa có trong bản S1", status_code=501)

    @app.get("/api/days/{production_date}/report")
    def api_daily_report(
        production_date: str,
        user: dict = Depends(require_role("employee", "boss", "admin")),
        conn: sqlite3.Connection = Depends(get_db),
    ):
        try:
            if len(production_date) != 10:
                raise ValueError
            parsed_date = datetime.strptime(production_date, "%Y-%m-%d").date()
        except ValueError:
            raise AppError(
                code="invalid_date",
                message="Ngày sản xuất không hợp lệ (định dạng YYYY-MM-DD)",
                status_code=422,
            )

        wb = build_daily_report(conn, parsed_date)
        buf = io.BytesIO()
        wb.save(buf)
        excel_bytes = buf.getvalue()
        filename = daily_report_filename(parsed_date)

        return Response(
            content=excel_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    return app
