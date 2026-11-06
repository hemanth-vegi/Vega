"""
Sets up Vega's voice unlock: installs speechbrain + torch + torchaudio for the Python that runs Vega,
checks that they really load, and if that Python can't get them, tries your other Python.
Everything pip prints is shown, so you can see the progress (it's a big download).
"""

import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VOICE = ["torch", "torchaudio", "speechbrain"]
# everything else Vega uses (for when we have to move Vega to another Python)
VEGA = ["numpy", "requests", "sounddevice", "faster-whisper", "ollama", "bottle", "pystray", "pillow", "comtypes",
        "psutil", "pypdf", "python-docx", "edge-tts", "pyttsx3", "SpeechRecognition", "deep-translator", "av"]
TEST = ("import torch, torchaudio\n"
        "from speechbrain.inference.speaker import EncoderClassifier\n"
        "print('VOICE_OK', torch.__version__)\n")


def say(text=""):
    print(text, flush=True)


def find_mybot():
    for folder in (os.path.dirname(HERE), HERE, r"C:\CloudLab\mybot"):  # vega_fix sits inside mybot
        if os.path.exists(os.path.join(folder, "vega_core.py")) and os.path.exists(os.path.join(folder, "vega_webview.py")):
            return folder
    return HERE


def vega_python(mybot):
    try:
        with open(os.path.join(mybot, "python_path.txt"), encoding="utf-8") as f:
            p = f.readline().strip().strip('"')
            if p and os.path.exists(p):
                return p
    except OSError:
        pass
    return sys.executable


def version(py):
    try:
        return subprocess.run([py, "-c", "import sys; print(sys.version.split()[0])"], capture_output=True,
                              text=True, timeout=60).stdout.strip()
    except Exception:
        return "?"


def voice_works(py):
    say("   Checking that the voice parts load (takes a few seconds)...")
    try:
        r = subprocess.run([py, "-c", TEST], capture_output=True, text=True, timeout=600)
    except Exception as e:
        say(f"   Check failed: {e}")
        return False
    if "VOICE_OK" in r.stdout:
        say("   " + r.stdout.strip().splitlines()[-1].replace("VOICE_OK", "Voice parts work. torch"))
        return True
    err = (r.stderr or "").strip().splitlines()
    say("   Not working yet: " + (err[-1] if err else "unknown error")[:300])
    return False


def pip(py, packages, extra=()):
    say(f"\n   pip install {' '.join(packages)}\n")
    return subprocess.call([py, "-m", "pip", "install", "--upgrade", *extra, *packages]) == 0


def other_pythons(current):
    out = ""
    try:
        out = subprocess.run(["py", "-0p"], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        pass
    found = []
    for ver, path in re.findall(r"-V:(3\.\d+)\S*\s+\*?\s*([A-Za-z]:\\[^\r\n]+?python\.exe)", out):
        if os.path.exists(path) and os.path.normcase(path) != os.path.normcase(current):
            found.append((ver, path))
    order = {"3.12": 0, "3.11": 1, "3.13": 2, "3.10": 3}
    return sorted(found, key=lambda x: order.get(x[0], 9))


def restart_vega(mybot):
    import urllib.request
    try:
        req = urllib.request.Request("http://127.0.0.1:47632/api/quit", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3).close()
        time.sleep(3)
    except Exception:
        pass
    vbs = os.path.join(mybot, "Start Vega.vbs")
    if os.name == "nt" and os.path.exists(vbs):
        subprocess.Popen(["wscript", vbs], cwd=mybot)
        say("   Vega is starting again.")


def main():
    mybot = find_mybot()
    py = vega_python(mybot)
    say("=" * 64)
    say(" Setting up Vega's voice unlock")
    say("=" * 64)
    say(f"Vega's folder: {mybot}")
    say(f"Vega's Python: {py}  (version {version(py)})\n")

    say("1. Is voice unlock already working?")
    if voice_works(py):
        say("\nAll good: nothing to install.")
        restart_vega(mybot)
        return finish(True)

    say("\n2. Installing the voice parts (about 300 MB to 1 GB, 5 to 15 minutes)...")
    pip(py, VOICE)
    if voice_works(py):
        restart_vega(mybot)
        return finish(True)

    say("\n3. Trying versions that work well together...")
    pip(py, ["torch<2.9", "torchaudio<2.9", "speechbrain"])
    if voice_works(py):
        restart_vega(mybot)
        return finish(True)

    others = other_pythons(py)
    for ver, other in others:
        say(f"\n4. Your Python {version(py)} can't run the voice parts. Trying your Python {ver}: {other}")
        say("   Installing everything Vega needs there (this takes a while)...")
        pip(other, VEGA + VOICE)
        if voice_works(other):
            with open(os.path.join(mybot, "python_path.txt"), "w", encoding="utf-8") as f:
                f.write(other + "\n")
            say(f"   Vega will use Python {ver} from now on.")
            restart_vega(mybot)
            return finish(True)
    if not others:
        say("\n4. No other Python found on this laptop.")
    return finish(False)


def finish(ok):
    say("")
    if ok:
        say("Done! Open Vega: the lock screen now shows 'Unlock with my voice'.")
        say("If it says there's no voiceprint yet: unlock with your PIN, then Settings ->")
        say("PIN and lock -> 'Set up my voice' and read the 4 sentences.")
    else:
        say("Voice unlock could not be set up on this laptop yet. Your PIN still works.")
        say("Take a screenshot of this whole window (scroll up for the red/yellow lines) and send it to Claude.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        say(f"\nSomething went wrong: {e}\nSend Claude a screenshot of this window.")
