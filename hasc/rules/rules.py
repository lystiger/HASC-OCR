"""Row rules (spec criteria 25-27). A rule never changes a number; None means skipped."""

TOLERANCE = 0.05


def _number(row, field):
    value = row["cells"].get(field, {}).get("value")
    return value if isinstance(value, float | int) else None


def _present(row, field):
    return row["cells"].get(field, {}).get("value") is not None


def weight_rule(row, layout="form_a"):
    """gross - (core + cover) = net. A blank cover counts as 0; the back layout has no cover and skips."""
    if layout == "back":
        return None
    gross, core, net = (_number(row, f) for f in ("gross_kg", "core_kg", "net_kg"))
    cover = _number(row, "cover_kg") if _present(row, "cover_kg") else 0
    if None in (gross, core, net, cover):
        return None
    return abs(gross - core - cover - net) < TOLERANCE


def thickness_rule(row):
    """The average of the six thicknesses = the written average."""
    values = [_number(row, f"thickness_{i}") for i in range(1, 7)]
    average = _number(row, "thickness_avg")
    if None in values or average is None:
        return None
    return abs(sum(values) / 6 - average) <= TOLERANCE


def failures(row, layout="form_a"):
    """List rule failure names ('weight', 'thickness') for a row."""
    found = []
    if weight_rule(row, layout) is False:
        found.append("weight")
    if thickness_rule(row) is False:
        found.append("thickness")
    return found


_failures = failures


def flag_row(row, layout="form_a"):
    """Return all flags for a row: rule failures plus doubtful cells."""
    return failures(row, layout) + [
        f"doubt:{field}" for field, cell in row.get("cells", {}).items() if cell.get("doubt")
    ]
