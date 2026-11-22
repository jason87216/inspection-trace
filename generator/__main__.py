"""CLI: python -m generator --scale small --seed 42 --out out/small"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from generator.config import load_config
from generator.pipeline import run


def main() -> None:
    p = argparse.ArgumentParser(description="PCB/substrate dirty data generator")
    p.add_argument("--scale", choices=["small", "medium", "large"], default="small")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", type=Path, help="Empty or new output directory (default: out/<scale>)")
    p.add_argument("--scenario", type=Path, default=None)
    p.add_argument("--dirty", type=Path, default=None)
    args = p.parse_args()

    cfg = load_config(args.scenario, args.dirty)
    try:
        summary = run(cfg, args.scale, args.seed, args.out or Path("out") / args.scale)
    except FileExistsError as exc:
        p.error(str(exc))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
