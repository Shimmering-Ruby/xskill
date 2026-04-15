"""pytest 入口：把 src/ 加进 sys.path，避免必须 pip install -e . 才能跑测试"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
