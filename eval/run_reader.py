#!/usr/bin/env python3
"""Run the reader on a folder of photos and report how many wrong values were flagged.

Usage: run_reader.py LABELS.xlsx PHOTO_DIR OUT_DIR [--split tuning] [--model ...] [--fallback ...]
Writes <photo>.out (key=value lines for score.py) and <photo>.json (the full result with flags).
"""
import argparse
import json
import sys
import time
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import score  # noqa: E402
from hasc.reader.reader import CloudReader, ReaderError, openrouter_transport  # noqa: E402
from hasc.reader.photo import upright  # noqa: E402
from hasc.reader.reading import read_sheet  # noqa: E402


def key(env_path=None):
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    env_file = env_path or os.environ.get("HASC_WORKER_ENV")
    if env_file and Path(env_file).exists():
        for line in Path(env_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("OPENROUTER_API_KEY missing from environment or env file")


def text(cell):
    value = cell["value"]
    if value is None:
        return "?"
    return f"{value:g}" if isinstance(value, float) else value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels")
    parser.add_argument("photos")
    parser.add_argument("out")
    parser.add_argument("--split", default="tuning")
    parser.add_argument("--model", default="google/gemini-3.8-flash")
    parser.add_argument("--fallback", default="qwen/qwen3.8-27b")
    parser.add_argument("--names", default=None, help="path to known_names.json")
    parser.add_argument("--env", default=None, help="path to env file")
    args = parser.parse_args()
    names_path = args.names or os.environ.get("HASC_KNOWN_NAMES")
    if names_path and Path(names_path).exists():
        names = json.loads(Path(names_path).read_text(encoding="utf-8"))
    else:
        names = {"customers": {}, "products": {}}
    reader = CloudReader(openrouter_transport(key(args.env)), names, args.model, args.fallback)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    labels = score.load_labels(args.labels, args.split)
    caught = missed = 0
    for photo in sorted(Path(args.photos).glob("*.jpg")):
        start = time.time()
        try:
            result = read_sheet(reader, upright(photo.read_bytes()))
        except ReaderError as error:
            print(f"{photo.stem} FAILED {error.kind}: {error}")
            continue
        rows = result["rows"]
        (out / f"{photo.stem}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / f"{photo.stem}.out").write_text("\n".join(
            " ".join(f"{k}={text(c)}" for k, c in row["cells"].items() if k in score.COLUMNS) for row in rows),
            encoding="utf-8")
        flagged = sum(1 for row in rows if row["flags"])
        if photo.stem in labels:
            preds = [{k: text(c) for k, c in row["cells"].items()} | {"_flags": row["flags"]} for row in rows]
            for label, pred in score._align(score.carry_down(labels[photo.stem]), score.carry_down(preds)):
                for field in score.COLUMNS:
                    want, got = label.get(field), pred.get(field)
                    if want == "?" or (score._blank(want) and score._blank(got)):
                        continue
                    if score._blank(want) or score._blank(got) or not score.same(want, got):
                        if pred.get("_flags"):
                            caught += 1
                        else:
                            missed += 1
        usage = result["usage"]
        print(f"{photo.stem} {time.time() - start:.0f}s rows={len(rows)} flagged={flagged} "
              f"rereads={result['rereads']} tries={usage['tries']} reader={result['reader_name']} cost=${usage['cost']}")
    print(f"wrong values on a flagged row: {caught}; wrong values on an unflagged row: {missed}")


if __name__ == "__main__":
    main()
