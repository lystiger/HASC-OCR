"""Read one sheet, check its rows, re-read once with more thinking where a rule fails, then flag.

A re-read only replaces a row when the new reading passes the rule and the row count matches;
otherwise the first reading stays and the row is flagged for a person.
"""
from hasc_ocr import rules
from hasc_ocr.reader import ReaderError


def _failures(row, layout):
    found = []
    if rules.weight_rule(row, layout) is False:
        found.append("weight")
    if rules.thickness_rule(row) is False:
        found.append("thickness")
    return found


def read_sheet(reader, image):
    result = reader.read(image, effort="low")
    layout, rows = result["layout"], result["rows"]
    failing = [i for i, row in enumerate(rows) if _failures(row, layout)]
    result["rereads"] = 0
    if failing:
        result["rereads"] = 1
        try:
            second = reader.read(image, effort="high")
        except ReaderError:
            second = None
        if second and len(second["rows"]) == len(rows):
            for i in failing:
                if not _failures(second["rows"][i], layout):
                    rows[i] = second["rows"][i]
    for row in rows:
        row["flags"] = _failures(row, layout) + [
            f"doubt:{field}" for field, cell in row["cells"].items() if cell.get("doubt")]
    return result
