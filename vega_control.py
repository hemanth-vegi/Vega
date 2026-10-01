"""
Vega's laptop controls and language helpers.

Laptop:  volume, media keys, brightness, Wi-Fi / Bluetooth, screenshots, finding and opening files,
         folders, closing apps, sleep / restart / shut down, phone calls through Phone Link.
         Anything that could lose work (closing apps, power) asks "Are you sure?" first.
Language: common Telugu / Tenglish phrases turned into commands, plus words Hemanth teaches her
         ("when I say mama I mean Ravi"), saved in my_words.json on this laptop.
"""

import difflib
import os
import re
import subprocess
import sys
import time
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

IS_WINDOWS = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

YES = re.compile(r"^(yes|yeah|yep|yup|ya|haa+n?|ha|han|avunu|avnu|ok|okay|okk|sure|do it|go ahead|confirm|cheyyi|chey|"
                 r"yes please|yes do it|of course|correct|right|"
                 r"i am telling you to close|i want you to close|close cheyyi|close it|close them|do what i said|"
                 r"i told you to close|close the tabs|close them all)\b", re.I)
# shutting down / restarting said in a normal sentence ("please shutdown the system now")
POWER_SAID = {
    "shutdown": re.compile(r"\b(?:shut ?down|shutdown|power off|switch off|turn off)\s+(?:the\s+|my\s+|this\s+|your\s+)?"
                           r"(?:laptop|computer|pc|system|machine)\b|\b(?:shut ?down|shutdown)(?:\s+(?:it|now|please))*$"),
    "restart": re.compile(r"\b(?:restart|reboot)\s+(?:the\s+|my\s+|this\s+|your\s+)?"
                          r"(?:laptop|computer|pc|system|machine)\b|"
                          r"\b(?:restart|reboot)(?:\s+(?:it|now|please))*$"),
}
NO = re.compile(r"^(no|nope|nah|na|vaddu|vadhu|cancel|stop|don'?t|do not|leave it|wait|not now)\b")

PROTECTED = {"explorer.exe", "svchost.exe", "csrss.exe", "winlogon.exe", "wininit.exe", "services.exe", "lsass.exe",
             "system", "smss.exe", "dwm.exe", "python.exe", "pythonw.exe", "ollama.exe", "ollama app.exe",
             "ollama_llama_server.exe", "powershell.exe", "conhost.exe", "taskmgr.exe", "sihost.exe",
             "antigravity.exe", "antigravity-ide.exe", "code.exe", "cmd.exe", "windowsterminal.exe",
             "applicationframehost.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe",
             "searchhost.exe", "textinputhost.exe", "systemsettings.exe"}

def is_media_playing():
    """Returns True if media (YouTube, Spotify, video player) is actively outputting sound to speakers."""
    if not IS_WINDOWS:
        return False
    try:
        import comtypes
        from comtypes import GUID, COMMETHOD, HRESULT, IUnknown
        import ctypes
        from ctypes import POINTER, c_float, c_uint

        CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
        IID_IMMDeviceEnumerator = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
        IID_IAudioMeterInformation = GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")

        class IAudioMeterInformation(IUnknown):
            _iid_ = IID_IAudioMeterInformation
            _methods_ = [
                COMMETHOD([], HRESULT, "GetPeakValue",
                          (['out'], POINTER(c_float), 'pfPeak')),
            ]

        class IMMDevice(IUnknown):
            _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
            _methods_ = [
                COMMETHOD([], HRESULT, "Activate",
                          (['in'], POINTER(GUID), 'iid'),
                          (['in'], c_uint, 'dwClsCtx'),
                          (['in'], ctypes.c_void_p, 'pActivationParams'),
                          (['out'], POINTER(POINTER(IUnknown)), 'ppInterface')),
            ]

        class IMMDeviceEnumerator(IUnknown):
            _iid_ = IID_IMMDeviceEnumerator
            _methods_ = [
                COMMETHOD([], HRESULT, "EnumAudioEndpoints"),
                COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint",
                          (['in'], c_uint, 'dataFlow'),
                          (['in'], c_uint, 'role'),
                          (['out'], POINTER(POINTER(IMMDevice)), 'ppDevice')),
            ]

        enumerator = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator,
            interface=IMMDeviceEnumerator,
            clsctx=comtypes.CLSCTX_INPROC_SERVER
        )
        pDevice = enumerator.GetDefaultAudioEndpoint(0, 1) # 0=eRender, 1=eMultimedia
        pMeter = pDevice.Activate(ctypes.byref(IID_IAudioMeterInformation), comtypes.CLSCTX_INPROC_SERVER, None)
        meter = pMeter.QueryInterface(IAudioMeterInformation)
        peak = meter.GetPeakValue()
        return peak > 0.008
    except Exception:
        return False

def gentle_close(proc_name_or_pid):
    """Gently close an application window (like clicking X) so it can prompt to save. Never force-kill."""
    if not IS_WINDOWS:
        return
    try:
        if isinstance(proc_name_or_pid, int):
            cmd = f"(Get-Process -Id {proc_name_or_pid} -ErrorAction SilentlyContinue).CloseMainWindow()"
            subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, creationflags=NO_WINDOW)
            subprocess.run(["taskkill", "/PID", str(proc_name_or_pid)], capture_output=True, creationflags=NO_WINDOW)
        else:
            name = str(proc_name_or_pid).lower()
            stem = name[:-4] if name.endswith(".exe") else name
            cmd = f"(Get-Process -Name '{stem}' -ErrorAction SilentlyContinue).CloseMainWindow()"
            subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, creationflags=NO_WINDOW)
            subprocess.run(["taskkill", "/IM", f"{stem}.exe"], capture_output=True, creationflags=NO_WINDOW)
    except Exception:
        pass

APP_PROCESSES = {
    "chrome": ["chrome.exe"], "google chrome": ["chrome.exe"], "brave": ["brave.exe"], "brave browser": ["brave.exe"],
    "edge": ["msedge.exe"], "microsoft edge": ["msedge.exe"], "firefox": ["firefox.exe"], "opera": ["opera.exe"],
    "whatsapp": ["WhatsApp.exe", "WhatsApp.Root.exe"], "telegram": ["Telegram.exe"], "spotify": ["Spotify.exe"],
    "vs code": ["Code.exe"], "vscode": ["Code.exe"], "visual studio code": ["Code.exe"], "code": ["Code.exe"],
    "word": ["WINWORD.EXE"], "excel": ["EXCEL.EXE"], "powerpoint": ["POWERPNT.EXE"], "notepad": ["notepad.exe"],
    "virtualbox": ["VirtualBox.exe"], "vlc": ["vlc.exe"], "discord": ["Discord.exe"], "steam": ["steam.exe"],
    "zoom": ["Zoom.exe"], "teams": ["ms-teams.exe", "Teams.exe"], "outlook": ["OUTLOOK.EXE", "olk.exe"],
    "calculator": ["CalculatorApp.exe"], "paint": ["mspaint.exe"], "apple music": ["AppleMusic.exe"],
    "phone link": ["PhoneExperienceHost.exe"], "instagram": ["Instagram.exe"],
}

# ---------------------------------------------------------------- Telugu / Tenglish -> command
_END = r"(?:\s+(?:ra|raa|re|andi|bro|da|please|pls|plz|ippudu|now))*$"
_DO = r"(?:\s*(?:cheyyi|cheyu|chey|cheyyandi|cheyandi|cheyyava|cheyyi ra|chesi pettu|chesey|cheseyi|pettu|kottu))?"
TENGLISH_RULES = [
    # volume and sound
    (r"^(?:sound|volume|voice|sownd)\s*(?:penchu|peddadi|ekkuva|increase|perugu|pencu|penchandi|up)" + _DO, "volume up"),
    (r"^(?:sound|volume|voice|sownd)\s*(?:tagginchu|thagginchu|takkuva|thakkuva|reduce|tagginchandi|down)" + _DO, "volume down"),
    (r"^(?:sound|volume|voice)\s*(?:aapu|aapey|aapu cheyyi|off|mute|band)" + _DO, "mute"),
    (r"^(?:brightness|light|screen)\s*(?:penchu|ekkuva|increase|up)" + _DO, "brightness up"),
    (r"^(?:brightness|light|screen)\s*(?:tagginchu|thagginchu|takkuva|thakkuva|reduce|down)" + _DO, "brightness down"),
    (r"^(?:song|paata|pata|music|video)\s*(?:aapu|aapey|aapandi|pause|stop)" + _DO, "pause"),
    (r"^(?:next|taruvati|tharuvathi)\s*(?:song|paata|pata)" + _DO, "next song"),
    # wifi, bluetooth, screenshot
    (r"^(wifi|wi-fi|bluetooth|net|internet)\s*(on|off)" + _DO, r"turn \2 \1"),
    (r"^screen ?shot\s*(?:teeyi|teesuko|tiyyi|teeyu|teesko|kottu|cheyyi|chey|teeyandi)?", "take a screenshot"),
    # power
    (r"^(?:laptop|system|computer|pc)\s*(?:off|shutdown|shut down|band|aapu|aapey)" + _DO, "shut down my laptop"),
    (r"^(?:laptop|system|computer|pc)?\s*restart" + _DO, "restart my laptop"),
    (r"^(?:laptop|system|computer|pc)\s*(?:sleep|nidra)\s*(?:lo)?" + _DO, "put my laptop to sleep"),
    # alarms: "repu podduna 6 ki alarm pettu" -> "wake me up at 6 am tomorrow"
    (r"^(repu\s+|tomorrow\s+)?(podduna\s+|poddunna\s+|morning\s+|sayantram\s+|saayantram\s+|evening\s+|ratri\s+|raatri\s+|night\s+)?"
     r"(\d{1,2}(?::\d{2})?)\s*(?:ki|ku|gantalaki|gantaki|ganta ki)?\s*(?:alarm|alaram|alarm clock)\s*(?:pettu|set|pettandi)" + _DO,
     lambda m: "wake me up at " + m.group(3) +
     (" am" if (m.group(2) or "").strip() in ("podduna", "poddunna", "morning") else
      " pm" if (m.group(2) or "").strip() else "") + (" tomorrow" if m.group(1) else "")),
    # time, weather, tasks
    (r"^(?:time|samayam|tym)\s*(?:entha|enti|yentha|cheppu|chepu)", "what time is it"),
    (r"^(?:repu|tomorrow)\s*(?:weather|vatavaranam)\s*(?:ela undi|enti|cheppu|chupinchu)?", "weather tomorrow"),
    (r"^(?:weather|vatavaranam)\s*(?:ela undi|enti|cheppu|chupinchu|ela undhi)", "weather"),
    (r"^(?:ivala|ivvala|eeroju|ee roju|today)\s*(?:task|pani|work)\s*(?:enti|emiti|cheppu|chupinchu)?", "what's my task today"),
    (r"^(?:na|naa|naaku|my)?\s*tasks?\s*(?:enti|emiti|cheppu|chupinchu)", "my tasks"),
    (r"^(?:news|varthalu|vaartalu)\s*(?:cheppu|chupinchu|enti)", "news"),
    # "X ki call cheyyi", "X open cheyyi", "X moosey", "X vinipinchu", "X vetuku": a Telugu word must be there,
    # so normal English sentences ("let's play", "the shop is open") are left alone
    (r"^(.+?)\s*(?:ki|ku|tho)?\s+(?:whatsapp\s+)?(?:call|phone|kal)\s*(?:cheyyi|chey|cheyu|kottu|cheyyandi|chesi pettu)$", r"call \1"),
    (r"^(.+?)\s+(?:(?:open|opn|oppen)\s*(?:cheyyi|cheyu|chey|cheyyandi|cheyandi|chesi pettu|chesey|cheseyi|kottu)|teruvu|terichu|teravu)$", r"open \1"),
    (r"^(.+?)\s+tappa\s+(?:migilina\s+)?anni\s+(?:close\s*(?:cheyyi|cheyu|chey|cheyyandi|cheyandi|chesey|cheseyi)|moosey|mooseyi|moosei|musey|museyi|bandh)" + _DO, r"close all except \1"),
    (r"^(?:migilina\s+)?anni\s+(?:close\s*(?:cheyyi|cheyu|chey|cheyyandi|cheyandi|chesey|cheseyi)|moosey|mooseyi|moosei|musey|museyi)\s+(.+?)\s+tappa" + _DO, r"close all except \1"),
    (r"^(.+?)\s+(?:close\s*(?:cheyyi|cheyu|chey|cheyyandi|cheyandi|chesey|cheseyi)|moosey|mooseyi|moosei|musey|museyi|kattu|kattey|aapu|aapey)"
     r"(?:\s*(?:cheyyi|chey))?$", r"close \1"),
    (r"^(.+?)\s+(?:play\s*(?:cheyyi|cheyu|chey|cheyyandi)|vinipinchu|pettu|veyyi|vey)$", r"play \1"),
    (r"^(.+?)\s+(?:(?:search|google)\s*(?:cheyyi|cheyu|chey|cheyyandi|kottu)|vetuku|vethuku|vetakandi|vetuku ra)$", r"search google for \1"),
    (r"^(.+?)\s+(?:gurinchi|gurunchi)\s*(?:cheppu|chepu|explain)" + _DO, r"tell me about \1"),
]
TENGLISH_WORDS = [(r"\brepu\b", "tomorrow"), (r"\b(?:ivala|ivvala|eeroju|ee roju)\b", "today"), (r"\bninna\b", "yesterday"),
                  (r"\b(?:podduna|poddunna)\b", "morning"), (r"\b(?:sayantram|saayantram)\b", "evening"),
                  (r"\b(?:ratri|raatri)\b", "night"), (r"\b(?:nimishalu|nimishaalu|nimushalu)\b", "minutes"),
                  (r"\b(?:gantalu|gantalaki|ganta)\b", "hours")]


def tenglish_to_command(t):
    """Turn Telugu-English mixed commands into Vega's English ones. Returns the new text (or the same text)."""
    s = re.sub(_END, "", t.strip()).strip()
    s = re.sub(r"^(?:konchem|oka saari|okasari|please|ra)\s+", "", s)
    for pattern, repl in TENGLISH_RULES:
        m = re.match(pattern + r"\s*$", s)
        if m:
            out = repl(m) if callable(repl) else (m.expand(repl) if "\\" in repl else repl)
            for w, e in TENGLISH_WORDS:
                out = re.sub(w, e, out)
            return re.sub(r"\s+", " ", out).strip()
    out = s
    for w, e in TENGLISH_WORDS:
        out = re.sub(w, e, out)
    return out


# ---------------------------------------------------------------- Windows helpers
def _ps(script, wait=20):
    """Run a PowerShell script hidden and return (ok, output)."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                           capture_output=True, text=True, timeout=wait, creationflags=NO_WINDOW)
        return r.returncode == 0, (r.stdout or r.stderr or "").strip()
    except Exception as e:
        return False, str(e)


def _key(vk, times=1):
    import ctypes
    for _ in range(times):
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
        time.sleep(0.01)


VK_VOL_MUTE, VK_VOL_DOWN, VK_VOL_UP = 0xAD, 0xAE, 0xAF
VK_NEXT, VK_PREV, VK_STOP, VK_PLAY = 0xB0, 0xB1, 0xB2, 0xB3

RADIO_SCRIPT = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | ? { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
Function Await($t, $type) { $n = $asTask.MakeGenericMethod($type).Invoke($null, @($t)); $n.Wait(-1) | Out-Null; $n.Result }
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioState,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$r = $radios | ? { $_.Kind -eq '__KIND__' } | Select-Object -First 1
if (-not $r) { Write-Output 'none'; exit 0 }
$res = Await ($r.SetStateAsync('__STATE__')) ([Windows.Devices.Radios.RadioAccessStatus])
Write-Output $res
"""

SCREENSHOT_SCRIPT = r"""
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type -TypeDefinition 'using System.Runtime.InteropServices; public class Dpi { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }'
[Dpi]::SetProcessDPIAware() | Out-Null
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)
$bmp.Save($env:VEGA_SHOT, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output 'ok'
"""


class ControlMixin:
    """Laptop control skills. Mixed into the Vega class in vega_core.py."""

    # ---------------- "Are you sure?"
    def ask_confirm(self, question, action, spoken_question=None, again=None):
        """Ask 'Are you sure?'. again: a regex; saying the same order again also counts as yes."""
        self.pending_action = (action, time.time() + 90)
        self.pending_source = getattr(self, "_source", None)
        self.pending_again = again
        self.pending_question = question
        self.pending_reasked = False
        from vega_core import reply
        return reply(f"{question}\nSay “yes” to go ahead, or “no” to cancel.", spoken_question or f"{question} Say yes or no.",
                     confirm=True)

    def check_confirm(self, t):
        """If Vega asked 'Are you sure?', handle the yes/no answer. Returns a reply or None."""
        from vega_core import reply
        pending = getattr(self, "pending_action", None)
        if not pending:
            # Check for follow-up confirmations or re-run requests ("do it", "I am telling you to close", "close cheyyi")
            if re.search(r"\b(?:do it|i am telling you to close|close cheyyi|i want you to close|close the tabs|close them|do what i said|close it)\b", t, re.I):
                last = getattr(self, "last_action", None)
                if last and time.time() - last[1] < 120:
                    action_fn = last[0]
                    try:
                        return action_fn()
                    except Exception as e:
                        return reply(f"Sorry, that didn't work: {e}")
            return None

        action, until = pending
        self.pending_action = None
        if time.time() > until:
            return None
        if getattr(self, "pending_source", None) not in (None, getattr(self, "_source", None)):
            self.pending_action = pending  # the question was asked on the other device (laptop / phone): not this answer
            return None
        def go():
            try:
                return action()
            except Exception as e:
                return reply(f"Sorry, that didn't work: {e}")
        refuse = re.search(r"\b(?:don'?t|do not|never|cancel|stop|wait|not now|later|leave it|vaddu|vadhu)\b", t)
        # "no problem", "no issues", "no worries" mean YES in Indian English
        if re.match(r"^(?:no problem|no problems|no issue|no issues|not a problem|no worries)\b", t) and not refuse:
            return go()
        # saying the same order again ("shutdown the system") = yes, I really mean it
        again = getattr(self, "pending_again", None)
        if again is not None and again.search(t) and not refuse and not NO.match(t):
            return go()
        if NO.match(t) or re.search(r"\b(?:no|not|don'?t|cancel|wait|stop|leave|later|never)\b", t):
            return reply("Okay, cancelled.")
        # Follow-ups like "do it", "I am telling you to close", "close cheyyi" count as YES
        if (YES.match(t) or re.search(r"\b(?:do it|i am telling you to close|close cheyyi|i want you to close|close the tabs|close them|do what i said)\b", t, re.I)) and not re.match(r"^(?:ha\s*){2,}$|^haha", t):
            return go()
        # If the user speaks an explicit new command (not an order to close or proceed), drop pending
        if re.match(r"^(?:open|play|call|search|what|when|where|why|how|who|tell|show|set|turn)\b", t):
            return None
        if len(t.split()) <= 3 and not getattr(self, "pending_reasked", True):
            # a short unclear answer (speech often mishears "yes"): ask once more instead of silently forgetting
            self.pending_action = (action, time.time() + 90)
            self.pending_reasked = True
            q = getattr(self, "pending_question", "Are you sure?")
            return reply(f"Sorry, I didn't catch that. {q}\nSay “yes” to go ahead, or “no” to cancel.",
                         f"Sorry, I didn't catch that. {q} Say yes or no.", confirm=True)
        return None

    # ---------------- my words (slang he teaches)
    COMMON_WORDS = {"like", "i", "you", "me", "the", "a", "an", "is", "it", "to", "and", "of", "in", "on", "my", "we", "he", "she",
                    "they", "said", "say", "mean", "not", "no", "yes", "what", "why", "how", "this", "that", "was", "are", "be",
                    "do", "did", "have", "has", "can", "will", "so", "but", "or", "for", "at", "hey", "vega", "open", "call", "play"}

    def my_words(self):
        """His word fixes. Bad ones saved by mistake (a common word like "like", or a whole sentence) are removed."""
        from vega_core import read_json, write_json
        w = read_json("my_words.json", {})
        if not isinstance(w, dict):
            return {}
        good = {k: v for k, v in w.items() if isinstance(k, str) and isinstance(v, str) and k.strip()
                and k.strip().lower() not in self.COMMON_WORDS and len(v.split()) <= 6 and len(k.split()) <= 4}
        if len(good) != len(w):
            write_json("my_words.json", good)
        return good

    def apply_my_words(self, t):
        words = self.my_words()
        if not words:
            return t
        out = t
        for k in sorted(words, key=len, reverse=True):
            out = re.sub(r"(?<![\w])" + re.escape(k) + r"(?![\w])", words[k], out)
        return out

    def words_command(self, t, raw):
        from vega_core import reply, write_json
        m = re.match(r"^(?:learn|remember|note)?\s*(?:that\s+)?when i say\s+[\"“']?(.+?)[\"”']?,?\s+i mean\s+[\"“']?(.+?)[\"”']?$", t) or \
            re.match(r"^(?:learn|remember|note)(?:\s+that)?\s+[\"“']?(.+?)[\"”']?\s+(?:means|=)\s+[\"“']?(.+?)[\"”']?$", t) or \
            re.match(r"^[\"“']?(.+?)[\"”']?\s+ante\s+[\"“']?(.+?)[\"”']?\s*(?:ani\s+)?(?:gurthu pettuko|gurtu pettuko|nerchuko|learn)$", t)
        if m:
            key, val = m.group(1).strip(" .,"), m.group(2).strip(" .,")
            if not key or not val or key == val or len(key) > 40 or len(val) > 80:
                return reply("Say it like this: when I say mama I mean Ravi")
            words = self.my_words()
            words[key.lower()] = val.lower()
            write_json("my_words.json", words)
            return reply(f"Got it. When you say “{key}”, I'll understand “{val}”.")
        m = re.match(r"^(?:forget|remove|delete)\s+(?:the\s+)?(?:word|slang)\s+[\"“']?(.+?)[\"”']?$", t)
        if m:
            words = self.my_words()
            if words.pop(m.group(1).strip().lower(), None) is None:
                return reply(f"I don't have a word “{m.group(1)}” saved.")
            write_json("my_words.json", words)
            return reply(f"Forgot “{m.group(1)}”.")
        if re.match(r"^(?:show |list |what are )?(?:my |your )?(?:words|slang|slangs|my words)$|^what words do you know$", t):
            words = self.my_words()
            if not words:
                return reply("You haven't taught me any words yet. Try: when I say mama I mean Ravi")
            return reply("Your words:\n" + "\n".join(f"• {k} → {v}" for k, v in sorted(words.items())),
                         f"You've taught me {len(words)} words. They're on screen.")
        return None

    # ---------------- laptop controls
    def laptop_control(self, t, raw):
        from vega_core import reply
        for fn in (self._volume_media, self._brightness, self._radios, self._screenshot, self._power, self._close_app,
                   self._folders_and_files):
            out = fn(t, raw)
            if out is not None:
                return out
        return None

    def _need_windows(self):
        from vega_core import reply
        return None if IS_WINDOWS else reply("That only works on Windows.")

    def _volume_media(self, t, raw):
        from vega_core import reply
        m = re.match(r"^(?:set\s+)?(?:the\s+)?(?:volume|sound)\s+(?:to\s+|at\s+)?(\d{1,3})\s*(?:%|percent)?$", t) or \
            re.match(r"^(?:set|put|make)\s+(?:the\s+)?(?:volume|sound)\s+(?:to|at)\s+(\d{1,3})\s*(?:%|percent)?$", t)
        full = re.match(r"^(?:full|max|maximum)\s+(?:volume|sound)$|^(?:volume|sound)\s+(?:full|max|maximum)$", t)
        if m or full:
            if self._need_windows():
                return self._need_windows()
            n = 100 if full else max(0, min(100, int(m.group(1))))
            _key(VK_VOL_DOWN, 50)
            _key(VK_VOL_UP, round(n / 2))
            return reply(f"Volume set to {n}%.")
        up = re.match(r"^(?:turn\s+(?:the\s+)?volume\s+up|volume\s+up|increase\s+(?:the\s+)?(?:volume|sound)|louder|"
                      r"(?:sound|volume)\s+(?:up|increase|more)|raise\s+(?:the\s+)?volume|turn\s+it\s+up)(?:\s+(?:a\s+lot|more|little|bit|a\s+bit))?$", t)
        down = re.match(r"^(?:turn\s+(?:the\s+)?volume\s+down|volume\s+down|decrease\s+(?:the\s+)?(?:volume|sound)|lower\s+(?:the\s+)?(?:volume|sound)|"
                        r"quieter|reduce\s+(?:the\s+)?(?:volume|sound)|(?:sound|volume)\s+(?:down|less|decrease)|turn\s+it\s+down)(?:\s+(?:a\s+lot|more|little|bit|a\s+bit))?$", t)
        if up or down:
            if self._need_windows():
                return self._need_windows()
            steps = 10 if "a lot" in t else 5
            _key(VK_VOL_UP if up else VK_VOL_DOWN, steps)
            return reply(f"Volume {'up' if up else 'down'}.", "")
        if re.match(r"^(?:mute|mute (?:the )?(?:sound|volume|laptop|audio)|silent|silence)$", t):
            if self._need_windows():
                return self._need_windows()
            _key(VK_VOL_MUTE)
            return reply("Muted. Say “unmute” to bring the sound back.", "")
        if re.match(r"^(?:unmute|unmute (?:the )?(?:sound|volume|laptop|audio)|sound on)$", t):
            if self._need_windows():
                return self._need_windows()
            _key(VK_VOL_UP)
            _key(VK_VOL_DOWN)
            return reply("Sound is back on.")
        if re.match(r"^(?:pause|pause (?:the )?(?:music|song|video|it|this)|stop (?:the )?(?:music|song|video))$", t):
            if self._need_windows():
                return self._need_windows()
            _key(VK_PLAY)
            return reply("Paused.", "")
        if re.match(r"^(?:resume|continue|play|unpause|resume (?:the )?(?:music|song|video)|play (?:the )?(?:music|song|video) again)$", t):
            if self._need_windows():
                return self._need_windows()
            _key(VK_PLAY)
            return reply("Playing.", "")
        if re.match(r"^(?:next|skip)(?:\s+(?:the\s+|this\s+)?(?:song|track|video|one))?$|^play (?:the )?next (?:song|track|video)$", t):
            if self._need_windows():
                return self._need_windows()
            _key(VK_NEXT)
            return reply("Next one.", "")
        if re.match(r"^(?:previous|last|go back)(?:\s+(?:song|track|video))$|^play (?:the )?previous (?:song|track|video)$", t):
            if self._need_windows():
                return self._need_windows()
            _key(VK_PREV)
            _key(VK_PREV)
            return reply("Previous one.", "")
        return None

    def _brightness(self, t, raw):
        from vega_core import reply
        if not re.match(r"^(?:please\s+)?(?:set |make |turn |increase |decrease |reduce |lower |raise |put |change )?(?:the |my |screen |laptop )*"
                        r"(?:brightness|bright)|^(?:dim|dimmer)(?: the| my)?(?: screen)?$|^(?:make (?:the |my )?screen )?(?:brighter|dimmer)$", t) \
                or len(t.split()) > 8 or re.search(r"\b(?:why|don'?t|didn'?t|not|never)\b", t):
            return None
        m = re.search(r"(\d{1,3})\s*(?:%|percent)?", t)
        up = re.search(r"\b(up|increase|more|higher|raise|brighter|full|max)\b", t)
        down = re.search(r"\b(down|decrease|less|lower|reduce|dimmer|dim)\b", t)
        if not (m or up or down):
            return None
        if self._need_windows():
            return self._need_windows()
        ok, cur = _ps("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness | Select-Object -First 1).CurrentBrightness")
        try:
            now = int(cur.strip().splitlines()[-1])
        except (ValueError, IndexError):
            now = 50
        if m:
            n = int(m.group(1))
        elif re.search(r"\b(full|max)\b", t):
            n = 100
        else:
            n = now + (20 if up else -20)
        n = max(5, min(100, n))
        ok, out = _ps(f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods | Select-Object -First 1).WmiSetBrightness(1,{n})")
        if not ok:
            return reply("I couldn't change the brightness on this screen. Use the brightness keys (F-keys) on your laptop.")
        return reply(f"Brightness set to {n}%.", "")

    def _radios(self, t, raw):
        from vega_core import reply
        m = re.match(r"^(?:turn|switch|put)\s+(on|off)\s+(?:the\s+|my\s+)?(wi-?fi|wifi|bluetooth|internet|net)$", t) or \
            re.match(r"^(?:turn|switch|put)\s+(?:the\s+|my\s+)?(wi-?fi|wifi|bluetooth|internet|net)\s+(on|off)$", t) or \
            re.match(r"^(wi-?fi|wifi|bluetooth)\s+(on|off)$", t) or \
            re.match(r"^(enable|disable|connect|disconnect)\s+(?:the\s+|my\s+)?(wi-?fi|wifi|bluetooth|internet)$", t)
        if not m:
            return None
        a, b = m.group(1), m.group(2)
        if a in ("on", "off", "enable", "disable", "connect", "disconnect"):
            state, thing = a, b
        else:
            thing, state = a, b
        on = state in ("on", "enable", "connect")
        kind = "Bluetooth" if thing == "bluetooth" else "WiFi"
        nice = "Bluetooth" if kind == "Bluetooth" else "Wi-Fi"
        if self._need_windows():
            return self._need_windows()

        def do():
            ok, out = _ps(RADIO_SCRIPT.replace("__KIND__", kind).replace("__STATE__", "On" if on else "Off"), wait=25)
            if ok and "Allowed" in out:
                return reply(f"{nice} is {'on' if on else 'off'}.")
            os.startfile("ms-settings:bluetooth" if kind == "Bluetooth" else "ms-settings:network-wifi")
            return reply(f"Windows didn't let me switch {nice} myself, so I opened the {nice} settings. Flip the switch there.")

        if kind == "WiFi" and not on:
            return self.ask_confirm("Turn off Wi-Fi? Your phone link and web answers will stop until it's back on"
                                    + (", and you won't be able to turn it back on from your phone." if getattr(self, "_source", "") == "phone" else "."), do)
        return do()

    def _screenshot(self, t, raw):
        from vega_core import reply
        # only a real order: "take a screenshot", "screenshot", "capture my screen" - not "I'll share you a screenshot"
        if not re.match(r"^(?:please\s+|can you\s+|could you\s+)?(?:take|capture|grab|get|snap|make)\s+(?:a\s+|the\s+|my\s+|one\s+)?"
                        r"(?:screen ?shot|screen capture|picture of (?:my |the )?screen)(?:\s+(?:now|please|of (?:my |the )?screen))?$|"
                        r"^screen ?shot(?:\s+(?:now|please))?$|^(?:capture|snap) (?:my |the )?screen$|^screen capture$", t):
            return None
        if self._need_windows():
            return self._need_windows()
        folder = os.path.join(os.path.expanduser("~"), "Pictures", "Vega Screenshots")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, datetime.now().strftime("Screenshot %Y-%m-%d %H-%M-%S.png"))
        env = dict(os.environ, VEGA_SHOT=path)  # the script saves the picture here
        try:
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", SCREENSHOT_SCRIPT],
                           capture_output=True, text=True, timeout=20, env=env, creationflags=NO_WINDOW)
        except Exception:
            pass
        if not os.path.exists(path):
            return reply("I couldn't take a screenshot. Press Windows + Shift + S to take one yourself.")
        out = reply(f"Screenshot saved in Pictures → Vega Screenshots:\n{os.path.basename(path)}", "Screenshot taken.")
        out["photo"] = path
        return out

    def _power(self, t, raw):
        from vega_core import reply
        if re.match(r"^(?:cancel|abort|stop|don'?t)\s+(?:the\s+)?(?:shut ?down|shutdown|restart|reboot)$", t):
            if self._need_windows():
                return self._need_windows()
            subprocess.run(["shutdown", "/a"], creationflags=NO_WINDOW)
            return reply("Cancelled. Your laptop stays on.")
        what = None
        question = re.match(r"^(?:how|what|why|when|where|which|should|shall|can i|could i|is it|does|do i|do you|explain|tell me)\b", t)
        if re.match(r"^(?:shut ?down|shutdown|power off|switch off|turn off)(?:\s+(?:my|the)\s+|\s+)?(?:laptop|computer|pc|system)?(?:\s+now)?$", t) \
                and not re.match(r"^(?:switch|turn) off$", t):
            what = "shutdown"
        elif re.match(r"^(?:restart|reboot)(?:\s+(?:my|the)\s+|\s+)?(?:laptop|computer|pc|system)?(?:\s+now)?$", t):
            what = "restart"
        elif not question and POWER_SAID["shutdown"].search(t):  # said naturally: "no problem, shutdown the system"
            what = "shutdown"
        elif not question and POWER_SAID["restart"].search(t):
            what = "restart"
        elif re.match(r"^(?:sleep|go to sleep|put (?:my |the )?(?:laptop|computer|pc|system) (?:to|in) sleep|"
                      r"(?:laptop|computer|pc|system) sleep|sleep (?:my |the )?(?:laptop|computer|pc|system))$", t):
            what = "sleep"
        if not what:
            return None
        if self._need_windows():
            return self._need_windows()

        def do():
            if what == "shutdown":
                subprocess.run(["shutdown", "/s", "/t", "60"], creationflags=NO_WINDOW)
                return reply("Shutting down in 1 minute. Save your work.\nSay “cancel shutdown” to stop it.",
                             "Shutting down in one minute. Say cancel shutdown to stop it.")
            if what == "restart":
                subprocess.run(["shutdown", "/r", "/t", "60"], creationflags=NO_WINDOW)
                return reply("Restarting in 1 minute. Save your work.\nSay “cancel restart” to stop it.",
                             "Restarting in one minute. Say cancel restart to stop it.")
            subprocess.Popen(["powershell", "-NoProfile", "-Command",
                              "Start-Sleep 3; Add-Type -AssemblyName System.Windows.Forms; "
                              "[System.Windows.Forms.Application]::SetSuspendState('Suspend', $false, $false)"],
                             creationflags=NO_WINDOW)
            return reply("Going to sleep in a few seconds. Good night!" if datetime.now().hour >= 21 else "Going to sleep in a few seconds.")

        words = {"shutdown": "Shut down your laptop", "restart": "Restart your laptop", "sleep": "Put your laptop to sleep"}
        return self.ask_confirm(f"{words[what]}?" + ("" if what == "sleep" else " Unsaved work may be lost."), do,
                                again=POWER_SAID.get(what) or re.compile(r"\bsleep\b"))

    def running_matches(self, name):
        import psutil
        name = name.lower().strip()
        want = [p.lower() for p in APP_PROCESSES.get(name, [])]
        found = {}
        for p in psutil.process_iter(["name"]):
            n = (p.info.get("name") or "")
            low = n.lower()
            if not low or low in PROTECTED:
                continue
            stem = low[:-4] if low.endswith(".exe") else low
            if low in want or stem == name.replace(" ", "") or (len(name) >= 4 and name.replace(" ", "") in stem) or \
                    difflib.SequenceMatcher(None, stem, name.replace(" ", "")).ratio() >= 0.85:
                found.setdefault(n, 0)
                found[n] += 1
        return found

    def _close_app(self, t, raw):
        from vega_core import reply
        import psutil

        CANONICAL_APP_NAMES = {
            "chrome.exe": "Chrome", "brave.exe": "Brave", "msedge.exe": "Edge", "firefox.exe": "Firefox", "opera.exe": "Opera",
            "whatsapp.exe": "WhatsApp", "whatsapp.root.exe": "WhatsApp", "telegram.exe": "Telegram", "spotify.exe": "Spotify",
            "code.exe": "VS Code", "winword.exe": "Word", "excel.exe": "Excel", "powerpnt.exe": "PowerPoint", "notepad.exe": "Notepad",
            "virtualbox.exe": "VirtualBox", "vlc.exe": "VLC", "discord.exe": "Discord", "steam.exe": "Steam", "zoom.exe": "Zoom",
            "ms-teams.exe": "Teams", "teams.exe": "Teams", "outlook.exe": "Outlook", "olk.exe": "Outlook",
            "calculatorapp.exe": "Calculator", "mspaint.exe": "Paint", "applemusic.exe": "Apple Music",
            "phoneexperiencehost.exe": "Phone Link", "instagram.exe": "Instagram",
        }

        # --- 1. Close single tab / current tab (Ctrl+W) ---
        if re.match(r"^(?:close|shut)\s+(?:this\s+|the\s+|current\s+)?tab$", t):
            if self._need_windows():
                return self._need_windows()
            cmd = "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SendKeys]::SendWait('^w')"
            subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, creationflags=NO_WINDOW)
            return reply("Closed the current tab.")

        # --- 2. Close single window (Alt+F4) ---
        if re.match(r"^(?:close|shut)\s+(?:this\s+|the\s+|current\s+)?window$", t):
            if self._need_windows():
                return self._need_windows()
            cmd = "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SendKeys]::SendWait('%{F4}')"
            subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, creationflags=NO_WINDOW)
            return reply("Closed the window.")

        # --- 3. Close other browser tabs in Brave / Chrome / Edge ---
        if re.match(r"^(?:close|shut)\s+all\s+(?:brave\s+|chrome\s+|edge\s+)?tabs\s+(?:except|other than|but)\s+(?:this\s+one|current\s+one|this)$", t):
            if self._need_windows():
                return self._need_windows()
            cmd = "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SendKeys]::SendWait('^{F4}')"
            subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, creationflags=NO_WINDOW)
            return reply("Closed other tabs in your browser.")

        # --- 4. Close all / everything / tabs / apps EXCEPT / OTHER THAN / BUT <app> ---
        m_except = re.match(
            r"^(?:close|quit|exit|shut(?:\s+down)?|kill)\s+(?:all\s+)?(?:the\s+|my\s+)?(?:other\s+)?(?:open\s+)?(?:browser\s+)?"
            r"(?:apps|applications|windows|tabs|programs|stuff|everything|them)?\s*"
            r"(?:except|other\s+than|but|apart\s+from)\s+(.+?)(?:\s+(?:app|application|window|tab|browser|only))?$",
            t, re.I
        )
        if m_except:
            if self._need_windows():
                return self._need_windows()
            except_target = m_except.group(1).lower().strip()
            running_procs = {p.info.get("name", "").lower() for p in psutil.process_iter(["name"]) if p.info.get("name")}
            open_other_apps = []
            for app_name, exes in APP_PROCESSES.items():
                if except_target in app_name or any(except_target in e.lower() for e in exes):
                    continue  # Keep the named app open!
                for exe in exes:
                    if exe.lower() in running_procs and exe.lower() not in PROTECTED:
                        open_other_apps.append((app_name.title(), exe))

            if not open_other_apps:
                return reply(f"No apps other than {except_target.title()} are open right now.")

            names_list = sorted(list(dict.fromkeys([CANONICAL_APP_NAMES.get(a[1].lower(), a[0]) for a in open_other_apps])))
            exes_to_close = list(dict.fromkeys([a[1] for a in open_other_apps]))

            def do_close_except():
                for exe in exes_to_close:
                    gentle_close(exe)
                return reply(f"Gently closed {', '.join(names_list)} (kept {except_target.title()} open).")

            self.last_action = (do_close_except, time.time(), f"close all apps except {except_target.title()}")

            # If more than 3 apps: list them and ask before closing!
            if len(names_list) > 3:
                return self.ask_confirm(
                    f"I found {len(names_list)} open apps: {', '.join(names_list)}. Close all of them and keep {except_target.title()} open? Unsaved work can be saved.",
                    do_close_except,
                    spoken_question=f"I found {len(names_list)} open apps: {', '.join(names_list)}. Close them all and keep {except_target.title()} open? Say yes or no."
                )
            else:
                return do_close_except()

        # --- 5. Close all tabs / open browser tabs ---
        if re.match(r"^(?:close|quit|exit|kill|shut(?:\s+down)?)\s+(?:all\s+)?(?:the\s+|my\s+)?(?:open\s+)?(?:browser\s+)?tabs?$", t):
            if self._need_windows():
                return self._need_windows()
            running_procs = {p.info.get("name", "").lower() for p in psutil.process_iter(["name"]) if p.info.get("name")}
            open_browsers = []
            for b in ("brave", "chrome", "edge", "firefox", "opera"):
                for exe in APP_PROCESSES.get(b, []):
                    if exe.lower() in running_procs and exe.lower() not in PROTECTED:
                        open_browsers.append((b.title(), exe))
            if not open_browsers:
                return reply("No browser tabs or windows are open right now.")
            b_names = sorted(list(dict.fromkeys([CANONICAL_APP_NAMES.get(a[1].lower(), a[0]) for a in open_browsers])))
            b_exes = list(dict.fromkeys([a[1] for a in open_browsers]))

            def do_close_tabs():
                for exe in b_exes:
                    gentle_close(exe)
                return reply(f"Gently closed all tabs in {', '.join(b_names)}.")

            self.last_action = (do_close_tabs, time.time(), f"close tabs in {', '.join(b_names)}")
            return self.ask_confirm(f"Close all open tabs in {', '.join(b_names)}? Unsaved pages can prompt to save.", do_close_tabs)

        # --- 6. Close them all / close all apps / close everything ---
        if re.match(r"^(?:close|quit|exit|kill|shut(?:\s+down)?)\s+(?:them\s+(?:all|on|in)|dem\s+all|all(?:\s+(?:of\s+them|the\s+apps|the\s+windows|apps|windows))?|everything|them)$", t):
            if self._need_windows():
                return self._need_windows()
            running_procs = {p.info.get("name", "").lower() for p in psutil.process_iter(["name"]) if p.info.get("name")}
            open_user_apps = []
            for app_name, exes in APP_PROCESSES.items():
                for exe in exes:
                    if exe.lower() in running_procs and exe.lower() not in PROTECTED:
                        open_user_apps.append((app_name.title(), exe))
            if not open_user_apps:
                return reply("No user apps are open right now.")
            names_list = sorted(list(dict.fromkeys([CANONICAL_APP_NAMES.get(a[1].lower(), a[0]) for a in open_user_apps])))
            procs_to_close = list(dict.fromkeys([a[1] for a in open_user_apps]))

            def do_close_all():
                for exe in procs_to_close:
                    gentle_close(exe)
                return reply(f"Gently closed {', '.join(names_list)}.")

            self.last_action = (do_close_all, time.time(), f"close {', '.join(names_list)}")
            return self.ask_confirm(f"Close open apps ({', '.join(names_list)})? Unsaved work can be saved.", do_close_all)

        # --- 7. Close specific named app ---
        m = re.match(r"^(?:close|quit|exit|kill|shut)\s+(?:down\s+)?(?:the\s+|my\s+)?(.+?)(?:\s+(?:app|application|window|browser))?$", t)
        if not m:
            return None
        name = m.group(1).strip()
        if re.search(r"\b(diary|journal|document|doc|file|pdf|vega|yourself|laptop|computer|pc|system|them|all|everything|nothing)\b", name):
            return None
        if self._need_windows():
            return self._need_windows()
        found = self.running_matches(name)
        if not found:
            return reply(f"{name.title()} isn't open right now.")
        procs = sorted(found)
        nice = name.title()

        def do():
            for p in procs:
                gentle_close(p)
            return reply(f"Gently closed {nice}.")

        self.last_action = (do, time.time(), f"close {nice}")
        return self.ask_confirm(f"Close {nice}? Anything unsaved in it can be saved.", do)

    def known_folders(self):
        home = os.path.expanduser("~")
        one = os.environ.get("OneDrive") or os.path.join(home, "OneDrive")

        def pick(*names):
            for n in names:
                if n and os.path.isdir(n):
                    return n
            return names[-1]

        from vega_core import here
        return {
            "downloads": pick(os.path.join(home, "Downloads")),
            "documents": pick(os.path.join(one, "Documents"), os.path.join(home, "Documents")),
            "desktop": pick(os.path.join(one, "Desktop"), os.path.join(home, "Desktop")),
            "pictures": pick(os.path.join(one, "Pictures"), os.path.join(home, "Pictures")),
            "photos": pick(os.path.join(one, "Pictures"), os.path.join(home, "Pictures")),
            "videos": pick(os.path.join(home, "Videos")),
            "music": pick(os.path.join(home, "Music")),
            "screenshots": pick(os.path.join(one, "Pictures", "Screenshots"), os.path.join(home, "Pictures", "Screenshots")),
            "vega screenshots": os.path.join(home, "Pictures", "Vega Screenshots"),
            "cloudlab": r"C:\CloudLab",
            "mybot": here(),
            "vega": here(),
        }

    def search_files(self, query, limit=8):
        """Find files whose names contain the words he said (Desktop, Documents, Downloads, OneDrive, C:\\CloudLab)."""
        words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in ("my", "the", "a", "file", "files", "document")]
        if not words:
            return []
        folders = self.known_folders()
        roots = []
        for k in ("desktop", "documents", "downloads", "pictures", "videos", "music", "cloudlab"):
            p = folders[k]
            if os.path.isdir(p) and p not in roots:
                roots.append(p)
        one = os.environ.get("OneDrive")
        if one and os.path.isdir(one) and one not in roots:
            roots.append(one)
        skip = {"node_modules", "__pycache__", "appdata", "site-packages", "venv", ".venv", ".git", "voice_model", "$recycle.bin"}
        hits, seen, scanned, start = [], set(), 0, time.time()
        for root in roots:
            base_depth = root.rstrip("\\/").count(os.sep)
            for dirpath, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in skip]
                if dirpath.count(os.sep) - base_depth >= 5:
                    dirs[:] = []
                for f in files:
                    scanned += 1
                    stem = os.path.splitext(f)[0].lower()
                    parts = re.findall(r"[a-z0-9]+", stem)
                    score = 0
                    for w in words:
                        if w in stem:
                            score += 2
                        elif any(difflib.SequenceMatcher(None, w, p).ratio() >= 0.8 for p in parts):
                            score += 1
                        else:
                            score = -1
                            break
                    if score > 0:
                        full = os.path.join(dirpath, f)
                        if full.lower() not in seen:
                            seen.add(full.lower())
                            try:
                                mtime = os.path.getmtime(full)
                            except OSError:
                                mtime = 0
                            hits.append((score, mtime, full))
                if scanned > 60000 or time.time() - start > 8:
                    break
        hits.sort(key=lambda h: (-h[0], -h[1]))
        return [h[2] for h in hits[:limit]]

    def open_path(self, path):
        from vega_core import reply
        if not IS_WINDOWS:
            return reply(f"(Would open {path})")
        os.startfile(path)
        return reply(f"Opening {os.path.basename(path.rstrip(os.sep)) or path}.")

    def _folders_and_files(self, t, raw):
        from vega_core import reply
        folders = self.known_folders()
        m = re.match(r"^(?:open|show|go to)\s+(?:my\s+|the\s+)?(.+?)(?:\s+folder)$", t) or \
            re.match(r"^(?:open|show|go to)\s+(?:my\s+|the\s+)?(downloads|documents|desktop|pictures|photos|videos|music|screenshots|vega screenshots)$", t)
        if m and m.group(1) in folders:
            p = folders[m.group(1)]
            if not os.path.isdir(p):
                return reply(f"I couldn't find your {m.group(1)} folder.")
            return self.open_path(p)
        # "open 2" / "open the second one" after a file search
        last = getattr(self, "last_files", None)
        if last:
            order = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "last": len(last)}
            m = re.match(r"^(?:open|show)\s+(?:number\s+|no\.?\s*|the\s+)?(\d+|first|second|third|fourth|fifth|sixth|last)(?:\s+one|\s+file)?$", t)
            if m:
                n = int(m.group(1)) if m.group(1).isdigit() else order[m.group(1)]
                if 1 <= n <= len(last):
                    return self.open_path(last[n - 1])
        m = re.match(r"^(?:find|search(?:\s+for)?|look for|where is|where's|locate)\s+(?:my\s+|the\s+)?(?:file|files|document|pdf)?\s*(?:called\s+|named\s+)?(.+?)(?:\s+(?:file|files|pdf|document))?$", t)
        if m and (re.search(r"\b(file|files|pdf|document|folder)\b", t) or t.startswith(("where is my", "where's my", "find my", "locate"))):
            return self.show_file_results(m.group(1), open_single=False)
        m = re.match(r"^open\s+(?:the\s+)?(?:file|pdf|document)\s+(?:called\s+|named\s+)?(.+)$", t)
        if m:
            return self.show_file_results(m.group(1), open_single=True)
        return None

    def show_file_results(self, query, open_single=True):
        from vega_core import reply
        hits = self.search_files(query)
        if not hits:
            return reply(f"I couldn't find a file called “{query}” in Desktop, Documents, Downloads, OneDrive or CloudLab.", unknown=True)
        self.last_files = hits
        if len(hits) == 1 and open_single:
            return self.open_path(hits[0])
        home = os.path.expanduser("~")
        lines = [f"{i}. {os.path.basename(p)}   ({os.path.dirname(p).replace(home, '~')})" for i, p in enumerate(hits, 1)]
        return reply(f"I found {len(hits)} file{'s' if len(hits) > 1 else ''}:\n" + "\n".join(lines) +
                     "\nSay “open 1” (or 2, 3...) to open one.",
                     f"I found {len(hits)} files. Say open 1, or another number.")

    # ---------------- phone calls through Windows Phone Link
    def phone_call(self, t, raw):
        from vega_core import reply, keep_case
        m = re.match(r"^(?:phone\s+call|normal\s+call|mobile\s+call|call)\s+(.+?)\s+(?:on|from|using|through|with|via)\s+"
                     r"(?:my\s+|the\s+)?(?:phone|mobile|phone link|iphone|sim|cell|cellphone)$", t) or \
            re.match(r"^(?:phone\s+call|normal\s+call|mobile\s+call)\s+(.+)$", t)
        if not m:
            return None
        name = keep_case(m.group(1).strip(), raw)
        who = self.find_contact(name)
        if not who:
            return reply(f"I don't have {name} in your contacts yet. Add them first:\nadd contact {name} 98765 43210",
                         f"I don't have {name} yet.", unknown=True)
        if not IS_WINDOWS:
            return reply("This only works on Windows with Phone Link.")
        try:
            os.startfile(f"tel:+{who['phone']}")
        except OSError:
            subprocess.Popen(["cmd", "/c", "start", "", f"tel:+{who['phone']}"], creationflags=NO_WINDOW)
        return reply(f"Opening Phone Link to call {who['name']}. Press Call there.\n"
                     "(Your phone must be connected in the Phone Link app. For your safety you press Call yourself.)",
                     f"Opening Phone Link to call {who['name']}. Press call there.")


# ---------------------------------------------------------------- is another app using the microphone? (calls)
_mic_cache = [0.0, False]
BROWSERS_EXE = ("brave.exe", "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe")


def other_app_using_mic():
    """True while another app (WhatsApp, Meet in Brave, Zoom, Teams, ChatGPT...) is using the microphone.
    Windows keeps this in the registry for the privacy icon. Checked at most every 3 seconds."""
    if not IS_WINDOWS:
        return False
    now = time.time()
    if now - _mic_cache[0] < 3:
        return _mic_cache[1]
    busy = False
    try:
        import winreg
        base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"

        def in_use(key):
            try:
                start, _ = winreg.QueryValueEx(key, "LastUsedTimeStart")
                stop, _ = winreg.QueryValueEx(key, "LastUsedTimeStop")
                return stop == 0 and start != 0
            except OSError:
                return False

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                name = winreg.EnumKey(root, i)
                if name == "NonPackaged":
                    with winreg.OpenKey(root, name) as np_key:
                        for j in range(winreg.QueryInfoKey(np_key)[0]):
                            app = winreg.EnumKey(np_key, j)
                            low = app.lower()
                            if "python" in low:
                                continue  # Vega herself: keep listening (browser tabs like Meet/Zoom count as mic in use)
                            with winreg.OpenKey(np_key, app) as k:
                                if in_use(k):
                                    busy = True
                else:
                    with winreg.OpenKey(root, name) as k:
                        if in_use(k):
                            busy = True
                if busy:
                    break
    except Exception:
        busy = False
    _mic_cache[0], _mic_cache[1] = now, busy
    return busy
