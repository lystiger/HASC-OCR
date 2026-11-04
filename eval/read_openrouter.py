#!/usr/bin/env python3
"""Read sheet photos with a cloud model through OpenRouter, one <photo>.out per photo for score.py.

Usage: read_openrouter.py PROMPT.txt PHOTO_DIR OUT_DIR [--model google/gemini-3.8-flash]
The key is read from OPENROUTER_API_KEY or HASC_WORKER_ENV and never printed.
Requests deny providers that collect data for training.
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

URL = "https://openrouter.ai/api/v1/chat/completions"


def api_key(env_path=None):
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    env_file = env_path or os.environ.get("HASC_WORKER_ENV")
    if env_file and Path(env_file).exists():
        for line in Path(env_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("OPENROUTER_API_KEY missing from environment or HASC_WORKER_ENV")


def read(photo, prompt, model, key, effort):
    image = base64.b64encode(photo.read_bytes()).decode()
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 12000,
        "reasoning": {"effort": effort},
        "provider": {"data_collection": "deny"},
        "usage": {"include": True},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}},
        ]}],
    }
    request = urllib.request.Request(URL, json.dumps(body).encode(), {
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt")
    parser.add_argument("photos")
    parser.add_argument("out")
    parser.add_argument("--model", default="google/gemini-3.8-flash")
    parser.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    parser.add_argument("--env", default=None, help="path to env file")
    args = parser.parse_args()
    prompt, key, out = Path(args.prompt).read_text(encoding="utf-8"), api_key(args.env), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    total = 0.0
    for photo in sorted(Path(args.photos).glob("*.jpg")):
        start = time.time()
        try:
            reply = read(photo, prompt, args.model, key, args.effort)
        except urllib.error.HTTPError as error:
            print(f"{photo.stem} HTTP {error.code}: {error.read().decode()[:300]}")
            continue
        (out / f"{photo.stem}.out").write_text(reply["choices"][0]["message"]["content"] or "", encoding="utf-8")
        (out / f"{photo.stem}.json").write_text(json.dumps(reply.get("usage", {}), indent=1), encoding="utf-8")
        usage = reply.get("usage", {})
        total += usage.get("cost") or 0
        print(f"{photo.stem} {time.time() - start:.0f}s in={usage.get('prompt_tokens')} "
              f"out={usage.get('completion_tokens')} cost=${usage.get('cost')} "
              f"finish={reply['choices'][0].get('finish_reason')}")
    print(f"total cost ${total:.4f}")


if __name__ == "__main__":
    main()
