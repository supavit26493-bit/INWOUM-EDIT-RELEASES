from pathlib import Path
import sys

for name in ('stdin', 'stdout', 'stderr'):
    stream = getattr(sys, name, None)
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf8', errors='backslashreplace' if name == 'stderr' else 'strict')

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'core'))
RULES = ROOT / 'rules'
