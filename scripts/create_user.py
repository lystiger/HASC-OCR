#!/usr/bin/env python3
"""CLI utility to create initial admin and user accounts with temporary passwords."""
import os
import sys

# Ensure repository root is on sys.path
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from hasc.web.cli import main

if __name__ == "__main__":
    sys.exit(main())
