"""Reader: one sheet photo in, ReadResult out (spec section "Reader").

A cloud model writes the sheet as strict key=value lines. The reply is validated, numbers and
known names are normalised, and failures are retried and then passed to a fallback model.
Plain lines beat a JSON schema on the tuning set: fewer misplaced widths at half the cost.
"""
import base64
import json
import re
import socket
import time
import urllib.error
import urllib.request

FIELDS = ["machine", "customer", "film_type", "roll_code", "pellet_formula", "mixing_shift_date",
          "thickness_mic", "width_mm", "length_mm", "gross_kg", "core_kg", "cover_kg", "net_kg",
          *(f"thickness_{i}" for i in range(1, 7)), "thickness_avg", "diameter_a", "diameter_b",
          "production_time", "grammage", "subtotal_note"]
NUMBERS = {"gross_kg", "core_kg", "cover_kg", "net_kg", *(f"thickness_{i}" for i in range(1, 7)), "thickness_avg",
           "subtotal_note"}
PAGE = ["day", "month", "year", "shift", "names"]
TRIES = 3
KEY = re.compile(r"\s+(?=[a-z_0-9]+=)")
SUBTOTAL_FACTOR = 2  # a thickness_avg above this many times the row's thickest reading is a run subtotal

PROMPT = """This photo shows a handwritten Vietnamese production sheet. Write it as plain text lines and nothing else.
First line: layout=form_a for "Báo cáo chi tiết máy thổi" (columns Cả bì, Lõi, Bìa, Sau trừ bì), layout=back for the "Màng ống" side (columns Tổng KL, Lõi, KL đã trừ), layout=other for anything else. On the same line add day= month= year= shift= names= from Ngày, Tháng, Năm, Ca and the staff names in the "Tên Nhân Viên" box, as written; leave out any that is blank.
Then one line per filled table row, top to bottom, as key=value pairs separated by spaces. Leave out any key whose cell is blank.
Keys: Khách hàng=customer, Loại màng=film_type, Mã cuộn=roll_code, Dày/mic=thickness_mic, Khổ rộng=width_mm, Dài=length_mm, Cả bì or Tổng KL=gross_kg, Lõi=core_kg, Bìa=cover_kg, Sau trừ bì or KL đã trừ=net_kg.
These columns are usually blank; add them only when something is written: Máy=machine, Công thức hạt=pellet_formula, Ca/ngày trộn hạt=mixing_shift_date, Thông số độ dày 1-6=thickness_1..thickness_6, Trung bình=thickness_avg, Đường kính cuộn A/B=diameter_a/diameter_b, Thời gian sản xuất=production_time, Định lượng=grammage.
Write values as on paper, with a comma decimal like gross_kg=70,6. A ditto mark (〃, '' or a mark like 11) means the value above: write that value, never 11. A crossed-out value: write only the final one.
Write customer, film_type, thickness_mic, width_mm and length_mm only on the row where they are written.
A bracketed run subtotal written beside a run in the thickness area, like [363,2], is not a thickness: write it as subtotal_note=363,2 on that run's row, never as thickness_avg. Other handwritten subtotals beside the table are not cells: leave them out.
Put ~ before a value you are not sure of, like net_kg=~16,8. Write ? for a value that cannot be read.
Customers are written in shorthand. Write customer as the standard name from this list: {customers}.
Write film_type as the standard name from this list: {products}.
If a written name matches nothing on a list, write it as written.
Example row line: customer=AB film_type=GT roll_code=1 thickness_mic=50 width_mm=600 length_mm=1500 gross_kg=55,4 core_kg=1,8 cover_kg=0,4 net_kg=53,2"""


class ReaderError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


class TransportError(Exception):
    def __init__(self, message, timeout=False):
        super().__init__(message)
        self.timeout = timeout


class InvalidReply(Exception):
    pass


def openrouter_transport(key, url="https://openrouter.ai/api/v1/chat/completions", timeout=120):
    def post(body):
        request = urllib.request.Request(url, json.dumps(body).encode(), {
            "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except (socket.timeout, TimeoutError) as error:
            raise TransportError(str(error), timeout=True)
        except urllib.error.HTTPError as error:
            raise TransportError(f"HTTP {error.code}: {error.read().decode(errors='replace')[:300]}")
        except (urllib.error.URLError, ConnectionError) as error:
            raise TransportError(str(error))
    return post


class CloudReader:
    def __init__(self, transport, names, model, fallback=None, sleep=time.sleep):
        self.transport, self.model, self.fallback, self.sleep = transport, model, fallback, sleep
        self.aliases = {}
        for group, field in (("customers", "customer"), ("products", "film_type")):
            for standard, variants in names[group].items():
                for written in (standard, *variants):
                    self.aliases[(field, written.casefold())] = standard
        listing = lambda group: "; ".join(
            s + (f" (often written {', '.join(v)})" if v else "") for s, v in names[group].items())
        self.prompt = PROMPT.format(customers=listing("customers"), products=listing("products"))

    def read(self, image, effort="low"):
        try:
            return self._read_with(self.model, image, effort)
        except ReaderError as error:
            if error.kind == "not_form_a" or not self.fallback:
                raise
        result = self._read_with(self.fallback, image, effort)
        for cell in [*result["page"].values(), *(c for r in result["rows"] for c in r["cells"].values())]:
            cell["doubt"] = True
        return result

    def _read_with(self, model, image, effort):
        last = None
        for attempt in range(TRIES):
            if attempt:
                self.sleep(2 ** attempt)
            try:
                reply = self.transport(self._body(model, image, effort))
                result = self._parse(reply)
            except (TransportError, InvalidReply) as error:
                last = error
                continue
            if result["layout"] == "other":
                raise ReaderError("not_form_a", "photo is neither form A nor its back side")
            result["reader_name"] = model
            result["usage"] = {"cost": (reply.get("usage") or {}).get("cost"), "tries": attempt + 1}
            return result
        kind = "timeout" if getattr(last, "timeout", False) else "other"
        raise ReaderError(kind, f"{model}: {last}")

    def _body(self, model, image, effort):
        data = base64.b64encode(image).decode()
        return {
            "model": model, "temperature": 0, "max_tokens": 12000, "reasoning": {"effort": effort},
            "provider": {"data_collection": "deny"}, "usage": {"include": True},
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": self.prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}]}],
        }

    def _parse(self, reply):
        """The first key=value line holds layout and page; every later one is a row."""
        try:
            content = reply["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as error:
            raise InvalidReply(f"reply has no content: {error}")
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S)
        lines = [line.strip() for line in content.splitlines() if re.match(r"\s*[a-z_0-9]+=", line)]
        if not lines:
            raise InvalidReply("no key=value lines")
        head = dict(pair.split("=", 1) for pair in KEY.split(lines[0]))
        layout = head.pop("layout", None)
        if layout not in ("form_a", "back", "other") or not set(head) <= set(PAGE):
            raise InvalidReply(f"bad first line {lines[0][:120]!r}")
        rows = []
        for line in lines[1:]:
            pairs = [pair.split("=", 1) for pair in KEY.split(line)]
            if any(field not in FIELDS for field, _ in pairs):
                raise InvalidReply(f"unknown key in {line[:120]!r}")
            cells = {}
            for field, value in pairs:
                value = value.strip()
                doubt = value.startswith("~")
                value = value.lstrip("~").strip()
                if value:
                    cells[field] = self._cell(field, value, doubt)
            if cells:
                _move_stray_average(cells)
                rows.append({"cells": cells})
        return {"layout": layout, "rows": rows,
                "page": {k: {"value": (head.get(k) or "").strip() or None, "doubt": False} for k in PAGE}}

    def _cell(self, field, value, doubt):
        if value == "?":
            return {"value": None, "doubt": True, "unreadable": True}
        if field in NUMBERS:
            if re.fullmatch(r"\d+([.,]\d+)?", value):
                return {"value": float(value.replace(",", ".")), "doubt": doubt}
            return {"value": value, "doubt": True}
        if field in ("customer", "film_type"):
            standard = self.aliases.get((field, " ".join(value.split()).casefold()))
            return {"value": standard or value, "doubt": doubt or standard is None}
        return {"value": value, "doubt": doubt}


def _move_stray_average(cells):
    """A run subtotal read as thickness_avg sits far above the row's thicknesses: move it to subtotal_note."""
    average = cells.get("thickness_avg")
    if not average or not isinstance(average["value"], float):
        return
    measured = [cells[f"thickness_{i}"]["value"] for i in range(1, 7) if f"thickness_{i}" in cells]
    readings = [v for v in [cells.get("thickness_mic", {}).get("value"), *measured] if isinstance(v, float)]
    # An average needs measured thicknesses to average; without any, the number is a run subtotal.
    if not measured or (readings and average["value"] > SUBTOTAL_FACTOR * max(readings)):
        average["doubt"] = True
        cells["subtotal_note"] = cells.pop("thickness_avg")
