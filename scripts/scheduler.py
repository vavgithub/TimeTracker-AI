"""
Run main.py on an interval (e.g. every 30 minutes) for silent admin reporting.

Usage:
  python scripts/scheduler.py
  python scripts/scheduler.py --minutes 30 --write-out

Requires: pip install schedule
"""
import argparse
import subprocess
import sys
from pathlib import Path

try:
    import schedule
    import time
except ImportError:
    print("Install schedule: pip install schedule", file=sys.stderr)
    sys.exit(1)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"


def _run(write_out: bool):
    cmd = [sys.executable, str(_BACKEND / "main.py")]
    if write_out:
        cmd.append("--write-out")
    subprocess.run(cmd, cwd=str(_BACKEND), check=False)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--minutes", type=int, default=30, help="Interval between runs")
    p.add_argument("--write-out", action="store_true", help="Pass --write-out to main.py")
    p.add_argument("--once", action="store_true", help="Run once and exit")
    args = p.parse_args()

    if args.once:
        _run(args.write_out)
        return

    schedule.every(args.minutes).minutes.do(lambda: _run(args.write_out))
    _run(args.write_out)
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
