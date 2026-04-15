import subprocess
import sys
from pathlib import Path

subprocess.run([sys.executable, str(Path(__file__).parent / "daily_briefing.py")])
