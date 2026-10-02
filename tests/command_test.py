"""
STEP 2: Command Test - tests Vega command handling directly (no window)
Run from C:\CloudLab\mybot:  python tests\command_test.py
"""
import sys, os, json, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("=" * 60)
print("VEGA COMMAND TEST - Direct Python, no window")
print("=" * 60)

try:
    from vega_core import Vega
    print("[OK] vega_core imported")
except Exception as e:
    print(f"[FAIL] Cannot import vega_core: {e}")
    sys.exit(1)

try:
    vega = Vega()
    print("[OK] Vega() created")
except Exception as e:
    print(f"[FAIL] Cannot create Vega(): {e}")
    sys.exit(1)

TEST_COMMANDS = [
    "open brave",
    "open whatsapp",
    "open youtube",
    "open lab log",
    "lock my laptop",
    "lock the screen",
    "brave open cheyyi",
    "close all apps except brave",
]

print()
print(f"{'COMMAND':<35} {'RECOGNISED':<12} {'ACTION'}")
print("-" * 80)

import subprocess as _subprocess, os as _os
_real_popen = _subprocess.Popen
_real_startfile = getattr(_os, "startfile", None)
_calls_all = {}

for cmd in TEST_COMMANDS:
    _calls = []

    class FakeProc:
        returncode = 0
        def wait(self): pass
        def communicate(self): return b"", b""

    def _fake_popen(args, **kw):
        _calls.append(("Popen", str(args)[:60]))
        return FakeProc()

    def _fake_startfile(path, *a, **kw):
        _calls.append(("startfile", str(path)[:60]))

    _subprocess.Popen = _fake_popen
    if _real_startfile:
        _os.startfile = _fake_startfile

    try:
        done_ev = threading.Event()
        out_box = [None]
        def worker():
            try:
                out_box[0] = vega.handle(cmd, source="test")
            except Exception as ex:
                out_box[0] = {"error": str(ex)}
            done_ev.set()
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        done_ev.wait(timeout=10)
        recognised = len(_calls) > 0
        action = _calls[0][1] if _calls else "(sent to AI / no exec)"
    except Exception as e:
        recognised = False
        action = f"ERROR: {e}"
    finally:
        _subprocess.Popen = _real_popen
        if _real_startfile:
            _os.startfile = _real_startfile

    status = "YES" if recognised else "NO"
    print(f"  {cmd:<33} {status:<12} {action}")

print()
print("=== CHECKING CODE IN vega_core.py ===")
with open("vega_core.py", encoding="utf-8", errors="replace") as f:
    core = f.read()

checks = {
    "LockWorkStation (lock command)": "LockWorkStation",
    "apps_index.json (app launcher)": "apps_index",
    "subprocess.Popen (app opening)": "subprocess.Popen",
    "startfile (file opening)": "startfile",
    "brave (app name)": "brave",
}
for label, term in checks.items():
    found = term in core
    print(f"  {'FOUND' if found else 'MISSING':6} - {label}")

print()
print("DONE.")