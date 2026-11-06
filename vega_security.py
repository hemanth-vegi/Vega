"""
Vega's locks: a PIN for the app window, and a voiceprint so she only obeys your voice.

PIN:   stored as a salted hash (never the PIN itself) in security.json.
Voice: your voiceprint (a list of numbers, not a recording) is stored in voiceprint.npy.
       Uses the free SpeechBrain ECAPA speaker model, running on your laptop's CPU.

Honest limits: the voice check stops Vega reacting to other people talking nearby,
but a recording of your voice could fool it, and a cold or a noisy room can make
it reject you. The PIN is the real lock.
If you forget your PIN: close Vega and delete security.json in this folder.
"""

import sys
if hasattr(sys, "stdout") and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys, "stderr") and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


import hashlib
import importlib.util
import os
import secrets
import threading

import numpy as np

from vega_core import here, read_json, write_json

MODEL_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"
VOICE_PACKAGES = ["speechbrain", "torch", "torchaudio"]


# ---------------------------------------------------------------- PIN

def security():
    s = read_json("security.json", {})
    return s if isinstance(s, dict) else {}


def save_security(s):
    write_json("security.json", s)


def _hash(pin, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt_hex), 200_000).hex()


def has_pin():
    s = security()
    return bool(s.get("pin_hash") and s.get("pin_salt"))


def set_pin(pin):
    s = security()
    s["pin_salt"] = secrets.token_hex(16)
    s["pin_hash"] = _hash(pin, s["pin_salt"])
    s.setdefault("lock_minutes", 30)
    save_security(s)


def remove_pin():
    s = security()
    s.pop("pin_hash", None)
    s.pop("pin_salt", None)
    save_security(s)


def check_pin(pin):
    s = security()
    if not (s.get("pin_hash") and s.get("pin_salt")):
        return True
    return secrets.compare_digest(_hash(pin, s["pin_salt"]), s["pin_hash"])


def lock_minutes():
    """Minutes without use before Vega locks herself. 0 = never; active timeouts are at least 15 minutes."""
    try:
        minutes = max(0, int(security().get("lock_minutes", 30)))
        return max(15, minutes) if minutes else 0
    except (TypeError, ValueError):
        return 30


def set_lock_minutes(n):
    s = security()
    minutes = max(0, int(n))
    s["lock_minutes"] = max(15, minutes) if minutes else 0
    save_security(s)


# ---------------------------------------------------------------- voice

def voice_packages_missing():
    return [p for p in VOICE_PACKAGES if importlib.util.find_spec(p) is None]


def voice_only():
    """True if 'Only obey my voice' is enabled in settings."""
    return bool(security().get("voice_only"))


def voice_only_ready():
    """True if 'Only obey my voice' is enabled, voiceprint exists, and dependencies are installed."""
    return voice_only() and has_voiceprint() and not voice_packages_missing()


def set_voice_only(on):
    s = security()
    s["voice_only"] = bool(on)
    s.setdefault("voice_threshold", 0.35)
    save_security(s)


def voice_threshold():
    try:
        return float(security().get("voice_threshold", 0.35))
    except (TypeError, ValueError):
        return 0.35


def unlock_threshold():
    """Unlocking needs a closer voice match than normal commands."""
    try:
        return float(security().get("voice_unlock_threshold", max(0.45, voice_threshold() + 0.1)))
    except (TypeError, ValueError):
        return 0.45


MIN_LEVEL, MAX_LEVEL = 0.20, 0.45


def set_thresholds(command_level, unlock_level):
    """Set by hand in Menu -> Voice strictness. Both kept between 0.20 and 0.45."""
    clamp = lambda x: round(min(MAX_LEVEL, max(MIN_LEVEL, float(x))), 2)
    s = security()
    s["voice_threshold"] = clamp(command_level)
    s["voice_unlock_threshold"] = clamp(unlock_level)
    s["manual_levels"] = True
    save_security(s)


def voice_unlock_ready():
    return os.path.exists(here("voiceprint.npy")) and not voice_packages_missing() and security().get("voice_unlock", True)


class VoiceID:
    """Turns a bit of speech into a 'voiceprint' and compares it with yours."""

    def __init__(self):
        self.model = None
        self.lock = threading.Lock()

    def load(self):
        with self.lock:
            if self.model is None:
                from speechbrain.inference.speaker import EncoderClassifier
                kwargs = dict(source=MODEL_SOURCE, savedir=here("voice_model"), run_opts={"device": "cpu"})
                try:
                    from speechbrain.utils.fetching import LocalStrategy
                    kwargs["local_strategy"] = LocalStrategy.COPY  # Windows can't make symlinks without admin rights
                except ImportError:
                    pass
                self.model = EncoderClassifier.from_hparams(**kwargs)
            return self.model

    @staticmethod
    def voiced(audio, rate=16000):
        """Keep only the parts where someone is speaking (silence and fan noise confuse the voice check)."""
        a = np.asarray(audio, dtype=np.float32).flatten()
        frame = int(rate * 0.03)
        n = len(a) // frame
        if n < 10:
            return a
        frames = a[:n * frame].reshape(n, frame)
        rms = np.sqrt(np.mean(frames ** 2, axis=1))
        thr = max(float(np.percentile(rms, 20)) * 2.5, float(rms.max()) * 0.08, 0.004)
        keep = rms > thr
        # keep a little around each spoken part so words aren't clipped
        keep = keep | np.roll(keep, 1) | np.roll(keep, -1)
        out = frames[keep].flatten()
        return out if len(out) >= rate * 0.5 else a

    @staticmethod
    def seconds(audio, rate=16000):
        return len(VoiceID.voiced(audio, rate)) / rate

    def embed(self, audio):
        import torch
        model = self.load()
        wav = torch.from_numpy(self.voiced(audio)).unsqueeze(0)
        with torch.no_grad():
            e = model.encode_batch(wav).squeeze().cpu().numpy().astype(np.float32)
        return e / (np.linalg.norm(e) + 1e-9)

    @staticmethod
    def _save(samples):
        samples = np.asarray(samples, dtype=np.float32)[-40:]  # keep the newest 40 samples
        v = samples.mean(axis=0)
        v = v / (np.linalg.norm(v) + 1e-9)
        np.save(here("voiceprint_samples.npy"), samples)
        np.save(here("voiceprint.npy"), v)
        return v

    @staticmethod
    def _samples():
        try:
            return list(np.load(here("voiceprint_samples.npy")))
        except (OSError, ValueError):
            try:
                return [np.load(here("voiceprint.npy"))]
            except (OSError, ValueError):
                return []

    def enroll(self, clips):
        embs = [self.embed(c) for c in clips if c is not None and len(c) > 12000]
        if len(embs) < 3:
            raise ValueError("I need at least 3 clear recordings.")
        v = self._save(embs)
        # Fair thresholds from YOUR recordings: how close each one is to the rest ("leave one out")
        loo = []
        for i, e in enumerate(embs):
            rest = np.mean([x for j, x in enumerate(embs) if j != i], axis=0)
            loo.append(float(np.dot(e, rest / (np.linalg.norm(rest) + 1e-9))))
        low = min(loo)
        s = security()
        s["voice_threshold"] = round(min(0.35, max(0.20, low - 0.15)), 2)
        s["voice_unlock_threshold"] = round(min(0.45, max(0.28, low - 0.08)), 2)
        save_security(s)
        return low

    def add_sample(self, audio):
        """Learn from a recording that was proven to be Hemanth (he typed the right PIN just after)."""
        if audio is None or len(audio) < 12000:
            return
        self._save(self._samples() + [self.embed(audio)])

    def score(self, audio):
        """Similarity to your voiceprint: about 0.5-0.8 for you, below 0.3 for others."""
        ref = np.load(here("voiceprint.npy"))
        return float(np.dot(self.embed(audio), ref))

    @staticmethod
    def has_voiceprint():
        return os.path.exists(here("voiceprint.npy"))

    @staticmethod
    def delete_voiceprint():
        for f in ("voiceprint.npy", "voiceprint_samples.npy"):
            try:
                os.remove(here(f))
            except OSError:
                pass
