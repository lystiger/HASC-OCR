"""Real reader for the worker, configured from an env file kept outside the repository.

HASC_WORKER_ENV names the file. It holds OPENROUTER_API_KEY, HASC_READER_MODEL, HASC_KNOWN_NAMES (the
JSON file of customer and product names) and optionally HASC_READER_FALLBACK. The key is never printed.
"""
import json
import os
import sys
from pathlib import Path

from hasc.reader.reader import CloudReader, openrouter_transport
from hasc.reader.reading import read_sheet

ENV_VAR = "HASC_WORKER_ENV"


class CloudPhotoReader:
    """The worker's reader: read(image_path) -> ReadResult, using the cloud reader and its re-read rule."""

    def __init__(self, cloud):
        self.cloud = cloud

    def turn(self, image):
        return self.cloud.turn(image)

    def read(self, image_path):
        return read_sheet(self.cloud, Path(image_path).read_bytes())


def read_env(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            values[name.strip()] = value.strip()
    return values


def cloud_reader_from_env(env_path=None):
    env_path = env_path or os.environ.get(ENV_VAR)
    if not env_path:
        sys.exit(f"{ENV_VAR} is not set")
    values = read_env(env_path)
    for name in ("OPENROUTER_API_KEY", "HASC_READER_MODEL", "HASC_KNOWN_NAMES"):
        if not values.get(name):
            sys.exit(f"{name} missing from {env_path}")
    names = json.loads(Path(values["HASC_KNOWN_NAMES"]).read_text(encoding="utf-8"))
    cloud = CloudReader(openrouter_transport(values["OPENROUTER_API_KEY"]), names,
                        values["HASC_READER_MODEL"], values.get("HASC_READER_FALLBACK") or None)
    return CloudPhotoReader(cloud)
