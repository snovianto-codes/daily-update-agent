import subprocess
import sys
from pathlib import Path

subprocess.run([sys.executable, str(Path(__file__).parent / "price_watcher.py"), "--stocks-only"])
