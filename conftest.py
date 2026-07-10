import sys
from pathlib import Path

_ROOT = Path(__file__).parent
for _d in ("deterministic-validator", "llm-reviewer"):
    _p = str(_ROOT / _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)
