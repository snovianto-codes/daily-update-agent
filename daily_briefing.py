"""
Daily Update Agent — entry point.

Called by OpenClaw cron at 7:30am SGT daily.
Delegates to the Gemini agentic loop in agent.py.

Cron: 0 7 * * * Asia/Singapore — see skills/daily-update/cron.json

IMPORTANT: OpenClaw must be started manually before cron jobs run:
  openclaw gateway start
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

LOG_FILE = Path(__file__).parent / "briefing.log"
ERR_FILE = Path(__file__).parent / "briefing_error.log"


def _log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


if __name__ == "__main__":
    _log("=== Daily briefing started ===")
    try:
        from agent import run_briefing
        result = run_briefing()
        _log("=== Briefing complete ===")
        if result:
            _log(f"Final response: {result[:200]}")
    except Exception:
        err = traceback.format_exc()
        _log(f"ERROR: {err[:500]}")
        with open(ERR_FILE, "a") as f:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts}]\n{err}\n")
        sys.exit(1)
