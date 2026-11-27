"""Production plans (KHSX PDFs): which machine makes a customer's thickness x width.

A plan line starts with the machine (T1, T6, M2 ...). machine_for answers from the newest plan date that
lists the machine before a shift starts; see the Zalo intake spec, R6. This module reads files and
nothing else: it must not import hasc.web, hasc.reader or hasc.db.
"""
import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

VN = timezone(timedelta(hours=7))
CORE_WORDS = ("giấy", "nhựa", "thêm")
MACHINE = re.compile(r"[TM]\d")
RECEIVED = re.compile(r"\d{1,2}[.,]\d{1,2}")
SONG_SAO_CODE = ("CT", "TĐ-")
UPDATED = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})")
FILE_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?")
SONG_SAO = "Song Sao"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlanLine:
    machine: str
    customer: str | None
    thickness: int
    width: int


@dataclass(frozen=True)
class Plan:
    file: str
    time: datetime
    lines: tuple


@dataclass(frozen=True)
class Plans:
    plans: tuple
    aliases: dict


@dataclass(frozen=True)
class PlanMatch:
    machine: str
    file: str


def _fold(text):
    return " ".join(text.split()).casefold()


def _canon(aliases, customer):
    collapsed = " ".join(customer.split())
    return aliases.get(collapsed.casefold(), collapsed)


def _pdf_text(path):
    import pypdf

    reader = pypdf.PdfReader(path)
    text = "\n".join(page.extract_text(extraction_mode="layout") for page in reader.pages)
    return unicodedata.normalize("NFC", text)


def _plan_lines(name, text, aliases):
    song_sao = SONG_SAO.upper() in name.upper()
    lines = []
    for line in text.splitlines():
        tokens = line.split()
        if not tokens or not MACHINE.fullmatch(tokens[0]):
            continue
        if song_sao:
            # The product name, and sometimes other words, sit between the machine and the product code.
            code = next((i for i, word in enumerate(tokens[1:], 1) if word.startswith(SONG_SAO_CODE)), None)
            after = tokens[code + 1:code + 3] if code else []
            if len(after) == 2 and all(word.isdecimal() for word in after):
                lines.append(PlanLine(tokens[0], _canon(aliases, SONG_SAO), int(after[0]), int(after[1])))
            continue
        core = next((i for i, word in enumerate(tokens[:-2]) if word.lower() in CORE_WORDS
                     and tokens[i + 1].isdecimal() and tokens[i + 2].isdecimal()), None)
        if core is None:
            continue
        received = next((i for i, word in enumerate(tokens[1:], 1) if RECEIVED.fullmatch(word)), None)
        customer = " ".join(tokens[1:received]) if received and received > 1 else None
        lines.append(PlanLine(tokens[0], _canon(aliases, customer) if customer else None,
                              int(tokens[core + 1]), int(tokens[core + 2])))
    return tuple(lines)


def _plan_time(name, text, mtime_ns):
    found = UPDATED.search(text)
    if found:
        day, month, year, hour, minute = map(int, found.groups())
        return datetime(year, month, day, hour, minute, tzinfo=VN)
    found = FILE_DATE.search(name)
    if not found:
        raise ValueError("no update time in the text and no date in the file name")
    day, month = int(found[1]), int(found[2])
    modified = datetime.fromtimestamp(mtime_ns / 1e9, VN)
    if found[3]:
        year = int(found[3])
    else:
        year = modified.year
        if date(year, month, day) > modified.date() + timedelta(days=31):
            year -= 1
    return datetime.combine(date(year, month, day), time(23, 59), VN)


def _read_known_names():
    path = os.environ.get("HASC_KNOWN_NAMES")
    if not path and os.environ.get("HASC_WORKER_ENV"):
        # Same KEY=VALUE format as hasc.worker.config.read_env, which imports hasc.reader.
        for line in Path(os.environ["HASC_WORKER_ENV"]).read_text(encoding="utf-8").splitlines():
            key, _, value = line.strip().partition("=")
            if key.strip() == "HASC_KNOWN_NAMES":
                path = value.strip()
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def _aliases(names):
    aliases = {}
    for standard, variants in ((names or {}).get("customers") or {}).items():
        for written in (standard, *variants):
            aliases[_fold(written)] = standard
    return aliases


# plan_dir -> {(file name, size, mtime_ns): text, or None when the file could not be read}
_cache: dict = {}


def _texts(plan_dir):
    old = _cache.get(plan_dir, {})
    fresh = {}
    for path in sorted(plan_dir.iterdir()) if plan_dir.is_dir() else ():
        if path.suffix.lower() != ".pdf" or not path.is_file():
            continue
        stat = path.stat()
        key = (path.name, stat.st_size, stat.st_mtime_ns)
        if key in old:
            fresh[key] = old[key]
            continue
        try:
            fresh[key] = _pdf_text(path)
        except Exception as error:
            log.warning("plan %s skipped, cannot read: %s", path.name, error)
            fresh[key] = None
    _cache[plan_dir] = fresh
    return fresh


def load_plans(plan_dir, names=None):
    aliases = _aliases(_read_known_names() if names is None else names)
    texts = _texts(Path(plan_dir)) if plan_dir else {}
    plans = []
    for (name, _, mtime_ns), text in texts.items():
        if text is None:
            continue
        try:
            plans.append(Plan(name, _plan_time(name, text, mtime_ns), _plan_lines(name, text, aliases)))
        except Exception as error:
            log.warning("plan %s skipped, cannot parse: %s", name, error)
    return Plans(tuple(plans), aliases)


def plan_match(plans, shift_start, customer, thickness, width):
    """The machine and plan file for a product, or None when no machine or several machines tie."""
    if not customer or thickness is None or width is None:
        return None
    wanted = _canon(plans.aliases, customer).casefold()
    eligible = [plan for plan in plans.plans if plan.time < shift_start]
    best = None
    for machine in sorted({line.machine for plan in eligible for line in plan.lines}):
        listing = [plan for plan in eligible if any(line.machine == machine for line in plan.lines)]
        newest = max(plan.time.astimezone(VN).date() for plan in listing)
        files = sorted(plan.file for plan in listing if plan.time.astimezone(VN).date() == newest
                       for line in plan.lines if line.machine == machine and line.thickness == thickness
                       and line.width == width and (line.customer or "").casefold() == wanted)
        if not files:
            continue
        if best and best[0] == newest:
            best = (newest, None, None)
        elif not best or newest > best[0]:
            best = (newest, machine, files[0])
    return PlanMatch(best[1], best[2]) if best and best[1] else None


def machine_for(plans, shift_start, customer, thickness, width):
    match = plan_match(plans, shift_start, customer, thickness, width)
    return match.machine if match else None


PLAN_NOTE = "theo kế hoạch"
WRITTEN_MACHINE = re.compile(r"[\s.\-]")


def _cell_text(cells, field):
    cell = cells.get(field) or {}
    value = None if cell.get("unreadable") else cell.get("value")
    return "" if value is None else str(value).strip()


def _whole(text):
    try:
        number = float(text.replace(",", "."))
    except ValueError:
        return None
    return int(number) if number.is_integer() else None


def assign_row_machines(rows, plans, shift_start):
    """Set each row's machine cell for a Zalo sheet (spec R6), in place.

    A written Máy cell stays, and a written machine is carried to the rows below it. Otherwise the plan's machine
    is stored with doubt and the note "theo kế hoạch: <file>"; a plan machine is never carried to other rows.
    No match leaves the cell blank with doubt.
    """
    carried = None
    for row in rows:
        cells = row["cells"]
        written = _cell_text(cells, "machine")
        if written:
            normal = WRITTEN_MACHINE.sub("", written).upper()
            carried = normal if MACHINE.fullmatch(normal) else carried
            continue
        if carried:
            cells["machine"] = {"value": carried, "inherited": True, "doubt": False}
            continue
        match = plan_match(plans, shift_start, _cell_text(cells, "customer") or None,
                           _whole(_cell_text(cells, "thickness_mic")), _whole(_cell_text(cells, "width_mm")))
        if match:
            cells["machine"] = {"value": match.machine, "doubt": True, "note": f"{PLAN_NOTE}: {match.file}"}
        else:
            cells["machine"] = {"value": None, "doubt": True}
