#!/usr/bin/env python3
"""Score reader output against the labelled sheets (spec criterion 37, brief P27).

Usage: score.py LABELS.xlsx OUTPUT_DIR [--split tuning|hidden]
OUTPUT_DIR holds one <tab>.out per sheet, in the pipe format of COLUMNS.
"""
import argparse
import re
import sys
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path

from openpyxl import load_workbook

COLUMNS = ["customer", "film_type", "roll_code", "thickness_mic", "width_mm", "length_mm",
           "gross_kg", "core_kg", "cover_kg", "net_kg"]
UNSCORED = {"machine", "subtotal_note", "diameter_a", "diameter_b", "thickness_avg",
            *(f"thickness_{i}" for i in range(1, 7))}  # P84, and subtotals are not fields
MIN_VALUES = 100  # Claude's choice 9 in the spec
TARGET = 0.95
HEADER_ROW, FIRST_ROW = 4, 7


def _text(value):
    return " ".join(unicodedata.normalize("NFC", str(value)).split()).casefold()


def _number(value):
    if not re.fullmatch(r"\d+([.,]\d+)?", value):
        return None
    try:
        return Decimal(value.replace(",", "."))
    except InvalidOperation:
        return None


def same(a, b):
    a, b = _text(a), _text(b)
    x, y = _number(a), _number(b)
    return x == y if x is not None and y is not None else a == b


def _blank(value):
    return value is None or not str(value).strip()


def parse_pipe(text):
    """Return (rows, malformed). A row with the wrong cell count is counted, never guessed at."""
    rows, malformed = [], 0
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    for line in text.splitlines():
        line = line.strip()
        if "|" not in line or re.fullmatch(r"[|\s:-]+", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells[0].casefold() == "customer":
            continue
        if len(cells) != len(COLUMNS):
            malformed += 1
            continue
        rows.append({k: (c or None) for k, c in zip(COLUMNS, cells)})
    return rows, malformed


def _agreement(label, pred):
    return sum(1 for k, v in label.items()
               if not _blank(v) and v != "?" and not _blank(pred.get(k)) and same(v, pred[k]))


def _align(labels, preds):
    """Best in-order pairing of rows, so one skipped row doesn't shift all the others."""
    n, m = len(labels), len(preds)
    best = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            best[i][j] = max(best[i + 1][j], best[i][j + 1],
                             best[i + 1][j + 1] + _agreement(labels[i], preds[j]))
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if best[i][j] == best[i + 1][j + 1] + _agreement(labels[i], preds[j]):
            pairs.append((labels[i], preds[j]))
            i, j = i + 1, j + 1
        elif best[i][j] == best[i + 1][j]:
            pairs.append((labels[i], {}))
            i += 1
        else:
            pairs.append(({}, preds[j]))
            j += 1
    pairs += [(r, {}) for r in labels[i:]] + [({}, r) for r in preds[j:]]
    return pairs


def score_sheet(labels, preds):
    """Return {field: [correct, total]}. A missed value and an invented value each count as wrong."""
    tally = {}
    for label, pred in _align(labels, preds):
        for key in sorted({*label, *pred}):
            want, got = label.get(key), pred.get(key)
            if want == "?" or (_blank(want) and _blank(got)):
                continue
            right = not _blank(want) and not _blank(got) and same(want, got)
            entry = tally.setdefault(key, [0, 0])
            entry[0] += right
            entry[1] += 1
    return tally


def load_labels(path, split):
    wb = load_workbook(path, data_only=True)
    labels = {}
    for row in wb["Sheets"].iter_rows(min_row=2, values_only=True):
        if row[0] is None or row[2] != split:
            continue
        tab = wb[row[0]]
        keys = {c: tab.cell(HEADER_ROW, c).value for c in range(1, tab.max_column + 1)}
        keys = {c: k for c, k in keys.items() if k and k != "subtotal_note"}
        rows = []
        for r in range(FIRST_ROW, tab.max_row + 1):
            found = {k: str(v).strip() for c, k in keys.items()
                     if not _blank(v := tab.cell(r, c).value)}
            if found:
                rows.append(found)
        labels[row[0]] = rows
    return labels


def run(labels_path, output_dir, split):
    labels = load_labels(labels_path, split)
    result = {"unlabelled": [], "missing_output": [], "malformed": 0, "sheets": {}, "fields": {}}
    for name, rows in labels.items():
        if not rows:
            result["unlabelled"].append(name)
            continue
        out = Path(output_dir) / f"{name}.out"
        if not out.exists():
            result["missing_output"].append(name)
            continue
        preds, malformed = parse_pipe(out.read_text(encoding="utf-8"))
        result["malformed"] += malformed
        tally = score_sheet(rows, preds)
        result["sheets"][name] = tally
        for key, (right, total) in tally.items():
            entry = result["fields"].setdefault(key, [0, 0])
            entry[0] += right
            entry[1] += total
    result["complete"] = not result["unlabelled"] and not result["missing_output"]
    result["scored_fields"] = sorted(k for k, (_, t) in result["fields"].items()
                                     if k not in UNSCORED and t >= MIN_VALUES)
    right = sum(result["fields"][k][0] for k in result["scored_fields"])
    total = sum(result["fields"][k][1] for k in result["scored_fields"])
    result["score"] = right / total if total else None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels")
    parser.add_argument("output_dir")
    parser.add_argument("--split", choices=["tuning", "hidden"], default="tuning")
    args = parser.parse_args()
    r = run(args.labels, args.output_dir, args.split)

    print(f"{'field':<16}{'right':>7}{'total':>7}{'%':>8}  counts toward score")
    for key, (right, total) in sorted(r["fields"].items()):
        print(f"{key:<16}{right:>7}{total:>7}{100 * right / total:>8.1f}  "
              f"{'yes' if key in r['scored_fields'] else 'no'}")
    print()
    for name, tally in r["sheets"].items():
        right, total = sum(v[0] for v in tally.values()), sum(v[1] for v in tally.values())
        print(f"{name:<16}{right:>7}{total:>7}{100 * right / total:>8.1f}")
    if r["malformed"]:
        print(f"\nRows dropped for a wrong cell count: {r['malformed']}")
    if r["unlabelled"]:
        print(f"\nNot labelled yet: {', '.join(r['unlabelled'])}")
    if r["missing_output"]:
        print(f"\nNo reader output: {', '.join(r['missing_output'])}")
    if r["score"] is None:
        print(f"\nNo field has {MIN_VALUES} labelled values yet, so there is no score.")
        return 1
    print(f"\nScore on {', '.join(r['scored_fields'])}: {100 * r['score']:.1f}%")
    if not r["complete"]:
        print("INCOMPLETE: this is not a gate result.")
        return 1
    if args.split == "hidden":
        passed = r["score"] >= TARGET
        print("GATE PASS" if passed else "GATE FAIL")
        return 0 if passed else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
