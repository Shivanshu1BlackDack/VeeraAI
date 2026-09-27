#!/usr/bin/env python3
"""Windows-friendly wrapper for dataset/generate_dataset.py."""

import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    gen = root / "dataset" / "generate_dataset.py"
    cmd = [
        sys.executable,
        str(gen),
        "--seed-dir",
        str(root / "dataset"),
        "--out",
        str(root / "expanded"),
    ]
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
