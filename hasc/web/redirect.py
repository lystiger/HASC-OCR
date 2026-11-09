"""Plain-HTTP app for port 8080 when the website runs on HTTPS (spec-1.1.0 section 6, criterion 17).

Every request gets a 308 to the same path and query on https://<host>/, so an old bookmark or a
phone typing the address keeps working. The one exception is /ca.crt: the certificate authority
from HASC_CA_CERT, which a phone downloads once to trust the website. No database, no sessions,
no login form.
"""
import os
import re
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Route

# A host name or IPv4 address, or a bracketed IPv6 address, with an optional port.
_HOST = re.compile(r"^(?P<host>[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::\d+)?$")


def _ca_cert(request: Request):
    path = os.environ.get("HASC_CA_CERT", "")
    if not path or not Path(path).is_file():
        return PlainTextResponse("Not found", status_code=404)
    return FileResponse(path, media_type="application/x-x509-ca-cert", filename="hasc-ca.crt")


def _to_https(request: Request):
    match = _HOST.match(request.headers.get("host", ""))
    if not match:
        return PlainTextResponse("Bad request", status_code=400)
    url = f"https://{match.group('host')}{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"
    return RedirectResponse(url, status_code=308)


app = Starlette(
    routes=[
        Route("/ca.crt", _ca_cert, methods=["GET", "HEAD"]),
        Route("/{path:path}", _to_https, methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]),
    ]
)
