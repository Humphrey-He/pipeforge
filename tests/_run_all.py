"""Naive test runner (no pytest dependency): exec each test_*.py with __file__ set."""
import sys, pathlib
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
passed = failed = 0
for f in sorted(pathlib.Path(__file__).parent.glob("test_*.py")):
    glb = {"__file__": str(f)}
    exec(compile(f.read_text(encoding="utf-8"), str(f), "exec"), glb)
    for name, fn in sorted(glb.items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}"); passed += 1
            except Exception as e:
                print(f"FAIL {name}: {e}"); failed += 1
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)