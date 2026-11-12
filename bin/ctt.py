"""Entry point that works with `py -I` / `python3 -I` (isolated mode does not put the repo on sys.path)."""
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit(f"claude-token-tools cần Python ≥ 3.11 (đang chạy {sys.version.split()[0]}). "
             "Cài từ https://www.python.org/downloads/ hoặc `winget install Python.Python.3.12`, rồi chạy lại.")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctt.cli import main  # noqa: E402

sys.exit(main())
