"""
Installs the Vega 2.0 fix into C:\\CloudLab\\mybot.

1. Stops any Vega that is running (old and new versions, and the Vega 2.0 browser window).
2. Backs up every file it replaces into  mybot\\backup_before_fix_<date>.
3. Copies the fixed files in.
4. Removes your PIN from saved chats (you typed it into the chat box by mistake).
5. Installs small helpers: pystray + pillow (Vega's icon by the clock), comtypes (knows when music plays), bottle.
6. Starts Vega 2.0.

Nothing else in your folder is touched: diary, notes, Lab Log, reminders, contacts and settings stay as they are.
"""

import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

CODE_FILES = ["vega_app.py", "vega_core.py", "vega_chats.py", "vega_security.py", "vega_telegram.py",
              "vega_voice.py", "vega_control.py", "vega_lablog.py", "vega_lablog_window.py",
              "vega_tracks.py", "vega_webview.py", "vega_popup.py"]
LAUNCHERS = ["Start Vega.vbs", "Start Vega.bat", "Start Vega (show errors).bat", "Vega.bat", "Start old Vega window.bat",
             "INSTALL VOICE UNLOCK.bat", "setup_voice.py"]
WEB_FILES = ["index.html", "app.js", "style.css", "lablog.js", "settings.js"]


def say(text=""):
    print(text, flush=True)


def find_mybot():
    for folder in (os.path.dirname(HERE), r"C:\CloudLab\mybot", HERE):
        if os.path.exists(os.path.join(folder, "vega_core.py")) and os.path.exists(os.path.join(folder, "vega_app.py")):
            return folder
    return None


def stop_running_vega(mybot):
    if os.name != "nt":
        return
    env = dict(os.environ, VEGA_INSTALL_ROOT=os.path.abspath(mybot))
    ps = r"""
$root = [IO.Path]::GetFullPath($env:VEGA_INSTALL_ROOT).TrimEnd('\')
$scriptPattern = '(?i)(?<![\w.-])' + [regex]::Escape($root) + '\\(?:vega_app|vega_webview|assistant)\.py(?=["\s]|$)'
$profilePattern = '(?i)(?<![\w.-])' + [regex]::Escape((Join-Path $root 'app_profile')) + '(?=[\\\s"]|$)'
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and ($_.CommandLine -match $scriptPattern -or $_.CommandLine -match $profilePattern)
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    $_.Name
}
"""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                           env=env, capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW)
        stopped = [x for x in r.stdout.split() if x.strip()]
        say(f"   Stopped {len(stopped)} running Vega program(s)." if stopped else "   Vega wasn't running.")
    except Exception as e:
        say(f"   (Couldn't check for a running Vega: {e}. If Vega is open, quit her and run this again.)")


def port_in_use(port):
    import socket
    t = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        t.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        t.close()


def make_sure_vega_stopped():
    """Last time an old Vega kept running after the update (new page, old program). Make sure it's gone."""
    import time
    if not (port_in_use(47631) or port_in_use(47632)):
        return True
    for _ in range(12):
        time.sleep(0.5)
        if not (port_in_use(47631) or port_in_use(47632)):
            say("   The old Vega has quit.")
            return True
    if port_in_use(47631) or port_in_use(47632):
        say("   Vega's web ports are still in use. I left the process alone; close Vega or restart the laptop, then run this again.")
        return False
    else:
        say("   The old Vega has stopped.")
        return True


def backup(mybot, names):
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = os.path.join(mybot, f"backup_before_fix_{stamp}")
    suffix = 1
    while os.path.exists(folder):
        folder = os.path.join(mybot, f"backup_before_fix_{stamp}_{suffix}")
        suffix += 1
    os.makedirs(folder, exist_ok=True)
    for n in names + ["config.json", "memory.json", "chat_log.jsonl"]:
        src = os.path.join(mybot, n)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(folder, n))
    web = os.path.join(mybot, "web")
    if os.path.isdir(web) and not os.path.exists(os.path.join(folder, "web")):
        shutil.copytree(web, os.path.join(folder, "web"))
    return folder


def startup_shortcut(mybot):
    """If Vega starts with Windows, make that shortcut use the same launcher (and so the same Python)."""
    lnk = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup", "Vega.lnk")
    if os.name != "nt" or not os.path.exists(lnk):
        return
    env = dict(os.environ, VEGA_LNK=lnk, VEGA_SCRIPT=os.path.join(mybot, "Start Vega.vbs"), VEGA_DIR=mybot)
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:VEGA_LNK);"
          "$s.TargetPath=(Join-Path $env:SystemRoot 'System32\\wscript.exe');$s.Arguments='\"'+$env:VEGA_SCRIPT+'\"';"
          "$s.WorkingDirectory=$env:VEGA_DIR;$s.Description='Vega - personal assistant';$s.Save()")
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], env=env,
                       capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW)
    except Exception:
        pass


def desktop_shortcut(mybot):
    """A "Vega" icon on the desktop: double-click it to open Vega (or bring her window back)."""
    if os.name != "nt":
        return False
    env = dict(os.environ, VEGA_SCRIPT=os.path.join(mybot, "Start Vega.vbs"), VEGA_DIR=mybot)
    ps = ("$d=[Environment]::GetFolderPath('Desktop');"
          "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Vega.lnk'));"
          "$s.TargetPath=(Join-Path $env:SystemRoot 'System32\\wscript.exe');$s.Arguments='\"'+$env:VEGA_SCRIPT+'\"';"
          "$s.WorkingDirectory=$env:VEGA_DIR;$s.Description='Vega - personal assistant';$s.Save()")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], env=env,
                           capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False


CORE_PACKAGES = ["numpy", "requests", "sounddevice", "faster_whisper", "ollama"]
VOICE_PACKAGES = ["speechbrain", "torch", "torchaudio"]
PROBE = ("import importlib.util as u, json, sys; c=%r; v=%r; "
         "print(json.dumps({'ver': sys.version.split()[0], 'exe': sys.executable, "
         "'core': [p for p in c if u.find_spec(p) is None], 'voice': [p for p in v if u.find_spec(p) is None]}))")


def probe(py):
    """Which of Vega's packages does this Python have?"""
    try:
        r = subprocess.run([py, "-c", PROBE % (CORE_PACKAGES, VOICE_PACKAGES)], capture_output=True, text=True,
                           timeout=90, creationflags=NO_WINDOW)
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return None


def python_candidates(mybot):
    found = []
    try:
        with open(os.path.join(mybot, "python_path.txt"), encoding="utf-8") as f:
            found.append(f.readline().strip().strip('"'))
    except OSError:
        pass
    found.append(sys.executable)
    if os.name == "nt":
        for cmd in (["py", "-0p"], ["where", "python"]):
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW).stdout
                found += re.findall(r"([A-Za-z]:\\[^\r\n*]+?python(?:w)?\.exe)", out, re.I)
            except Exception:
                pass
    seen, out = set(), []
    for p in found:
        p = (p or "").strip()
        if p.lower().endswith("pythonw.exe"):
            p = p[:-11] + "python.exe"
        if p and os.path.exists(p) and "windowsapps" not in p.lower() and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


def choose_python(mybot):
    """Use the Python that has Vega's voice packages (voice unlock needs speechbrain + torch).
    If none has them, install them into the Python Vega uses now."""
    cands = python_candidates(mybot)
    info = [(p, probe(p)) for p in cands]
    info = [(p, i) for p, i in info if i]
    if not info:
        say("   Couldn't check Python. Leaving it as it is.")
        return sys.executable
    current, cur = info[0]
    say(f"   Vega uses Python {cur['ver']}. Missing: {', '.join(cur['core'] + cur['voice']) or 'nothing'}")
    if not cur["voice"] and not cur["core"]:
        return current
    best = sorted(info, key=lambda x: (len(x[1]["voice"]) > 0, len(x[1]["core"]), len(x[1]["voice"])))[0]
    if best[0] != current and not best[1]["voice"] and len(best[1]["core"]) <= len(cur["core"]):
        with open(os.path.join(mybot, "python_path.txt"), "w", encoding="utf-8") as f:
            f.write(best[0] + "\n")
        say(f"   Found Python {best[1]['ver']} that has everything for voice unlock: Vega will use it now.")
        return best[0]
    if cur["voice"]:
        say("   Installing voice unlock (speechbrain, torch, torchaudio). This is a big download (about 1 GB)")
        say("   and can take 5 to 15 minutes. Please wait...")
        try:
            r = subprocess.run([current, "-m", "pip", "install", "-q", "speechbrain", "torch", "torchaudio"],
                               capture_output=True, text=True, timeout=3600, creationflags=NO_WINDOW)
            left = (probe(current) or {}).get("voice", cur["voice"])
            if r.returncode == 0 and not left:
                say("   Voice unlock is installed.")
            else:
                say("   Voice unlock couldn't be installed (" + ((r.stderr or "").strip().splitlines() or ["unknown error"])[-1][:160]
                    + "). The PIN still works.")
        except Exception as e:
            say(f"   Voice unlock couldn't be installed ({e}). The PIN still works.")
    if cur["core"]:
        say("   Installing missing parts: " + ", ".join(cur["core"]).replace("_", "-"))
        try:
            subprocess.run([current, "-m", "pip", "install", "-q"] + [c.replace("_", "-") for c in cur["core"]],
                           capture_output=True, text=True, timeout=1800, creationflags=NO_WINDOW)
        except Exception:
            pass
    return current


def scrub_pin(mybot):
    """Delete chat messages that are exactly your PIN (it was typed into the chat box and saved)."""
    sys.path.insert(0, mybot)
    os.chdir(mybot)
    try:
        import vega_security as sec
        if not sec.has_pin():
            return 0
    except Exception as e:
        say(f"   (Skipped the PIN clean-up: {e})")
        return 0
    stamp = re.compile(r"^\[[^\]]{5,40}\]\s*")

    def is_pin(text):
        t = stamp.sub("", str(text or "")).strip()
        return bool(re.fullmatch(r"[A-Za-z0-9]{4,12}", t)) and sec.check_pin(t)

    removed = 0
    # memory.json: [{"role", "content"}]  (also drop Vega's reply straight after the PIN)
    path = os.path.join(mybot, "memory.json")
    try:
        with open(path, encoding="utf-8") as f:
            mem = json.load(f)
        keep, skip_reply = [], False
        for m in mem:
            if skip_reply and m.get("role") == "assistant":
                skip_reply = False
                removed += 1
                continue
            skip_reply = False
            if m.get("role") == "user" and is_pin(m.get("content")):
                removed += 1
                skip_reply = True
                continue
            keep.append(m)
        if len(keep) != len(mem):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(keep, f, ensure_ascii=False, indent=1)
    except (OSError, ValueError):
        pass
    # chat_log.jsonl: one {"t", "who", "text"} per line
    path = os.path.join(mybot, "chat_log.jsonl")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        keep = []
        for line in lines:
            try:
                if is_pin(json.loads(line).get("text")):
                    removed += 1
                    continue
            except ValueError:
                pass
            keep.append(line)
        if len(keep) != len(lines):
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(keep) + ("\n" if keep else ""))
    except OSError:
        pass
    # chats/<id>.json: {"messages": [{"who", "text"}]}
    for path in glob.glob(os.path.join(mybot, "chats", "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                chat = json.load(f)
            msgs = chat.get("messages", [])
            keep = [m for m in msgs if not is_pin(m.get("text"))]
            if len(keep) != len(msgs):
                removed += len(msgs) - len(keep)
                chat["messages"] = keep
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(chat, f, ensure_ascii=False, indent=0)
        except (OSError, ValueError, AttributeError):
            pass
    return removed


def main():
    say("=" * 60)
    say(" Installing the Vega fix")
    say("=" * 60)
    mybot = find_mybot()
    if not mybot:
        say("I couldn't find your mybot folder (C:\\CloudLab\\mybot).")
        say("Put the vega_fix folder INSIDE C:\\CloudLab\\mybot and run INSTALL FIX.bat again.")
        return
    missing = [n for n in CODE_FILES + LAUNCHERS if not os.path.exists(os.path.join(HERE, n))]
    missing += ["web\\" + n for n in WEB_FILES if not os.path.exists(os.path.join(HERE, "web", n))]
    if missing:
        say("Some files are missing from the vega_fix folder: " + ", ".join(missing))
        say("Unzip the whole vega_fix.zip again, then run INSTALL FIX.bat.")
        return
    say(f"Vega folder: {mybot}\n")

    say("1. Stopping Vega...")
    stop_running_vega(mybot)
    if not make_sure_vega_stopped():
        say("The fix was not installed. Close Vega and run INSTALL FIX.bat again.")
        return

    say("2. Backing up your current files...")
    folder = backup(mybot, CODE_FILES + LAUNCHERS)
    say(f"   Saved in {os.path.basename(folder)}")

    say("3. Copying the fixed files...")
    for n in CODE_FILES + LAUNCHERS:
        shutil.copy2(os.path.join(HERE, n), os.path.join(mybot, n))
    os.makedirs(os.path.join(mybot, "web"), exist_ok=True)
    for n in WEB_FILES:
        shutil.copy2(os.path.join(HERE, "web", n), os.path.join(mybot, "web", n))
    shutil.rmtree(os.path.join(mybot, "__pycache__"), ignore_errors=True)  # old compiled copies
    say(f"   Copied {len(CODE_FILES) + len(LAUNCHERS) + len(WEB_FILES)} files.")

    say("4. Removing your PIN from saved chats...")
    n = scrub_pin(mybot)
    say(f"   Removed {n} message(s)." if n else "   Nothing to remove.")

    say("5. Checking voice unlock and Vega's Python...")
    vega_py = choose_python(mybot)

    say("   Installing small helpers (tray icon, music check)... this can take a minute")
    try:
        r = subprocess.run([vega_py, "-m", "pip", "install", "-q", "bottle", "pystray", "pillow", "comtypes"],
                           capture_output=True, text=True, timeout=600, creationflags=NO_WINDOW)
        say("   Done." if r.returncode == 0 else "   Some helpers didn't install (no internet?). Vega still works;\n"
            "   without them there's no icon by the clock, so reopen her with the Start Vega shortcut.")
    except Exception:
        say("   Skipped. Vega still works.")

    say("6. Making a Vega icon on your desktop...")
    say("   Done: double-click 'Vega' on your desktop to open her." if desktop_shortcut(mybot) else "   Skipped.")
    startup_shortcut(mybot)

    say("7. Starting Vega 2.0...")
    if os.name == "nt":
        subprocess.Popen(["wscript", os.path.join(mybot, "Start Vega.vbs")], cwd=mybot)
    say("")
    say("All done! Vega 2.0 opens in a few seconds and asks for your PIN.")
    say("Closing her window keeps her running. To get her back: double-click 'Vega' on your desktop,")
    say("click the blue V by the clock (look under the ^ arrow), or press Ctrl + Alt + Space for the Quick Bar.")
    say("If she doesn't open, double-click 'Start Vega (show errors).bat' in the mybot folder")
    say("and send Claude a screenshot of the window.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        say(f"\nSomething went wrong: {e}\nSend Claude a screenshot of this window.")
