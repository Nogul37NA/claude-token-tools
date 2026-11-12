"""Hook entry: silent no-op on old Python so a missing dependency never disturbs a session."""
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    sys.stdout.write("{}")
    sys.exit(0)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctt.hooks import main  # noqa: E402

sys.exit(main())
