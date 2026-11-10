"""Read one sheet, check its rows, re-read once with more thinking where a rule fails, then flag.

A re-read only replaces a row when the new reading passes the rule and the row count matches;
otherwise the first reading stays and the row is flagged for a person.
"""
from hasc.reader.reader import ReaderError
from hasc.rules import rules


def read_sheet(reader, image):
    result = reader.read(image, effort="low")
    layout, rows = result["layout"], result["rows"]
    failing = [i for i, row in enumerate(rows) if rules.failures(row, layout)]
    result["rereads"] = 0
    if failing:
        result["rereads"] = 1
        try:
            second = reader.read(image, effort="high")
        except ReaderError as error:
            second = None
            second_usage = error.usage
        else:
            second_usage = second.get("usage", {})
        if second_usage:
            usage = result.setdefault("usage", {})
            for key in ("cost", "prompt_tokens", "completion_tokens", "tries"):
                usage[key] = usage.get(key, 0) + second_usage.get(key, 0)
        if second and len(second["rows"]) == len(rows):
            for i in failing:
                if not rules.failures(second["rows"][i], layout):
                    rows[i] = second["rows"][i]
    for row in rows:
        row["flags"] = rules.failures(row, layout) + [
            f"doubt:{field}" for field, cell in row["cells"].items() if cell.get("doubt")]
    return result
