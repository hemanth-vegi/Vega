"""
Hemanth's personal voice assistant - Stage 1: talk and listen.

How it works:
  - Press Enter, speak, press Enter again  -> it hears you (Whisper, runs on your laptop)
  - Or type a message and press Enter      -> same thing, without the microphone
  - It answers with your own Ollama model and reads the answer out loud
  - It remembers your recent conversation between runs (memory.json)
  - It keeps a permanent notebook about you (facts.txt). Teach it by saying:
        "Remember that my best friend is Ravi."
        "What do you remember about me?"
        "Forget Ravi."
  - Type q and press Enter to quit

Everything runs on your own laptop. Nothing is sent to the internet
(except the one-time download of the Whisper speech model on first run).
"""

import sys
if hasattr(sys, "stdout") and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys, "stderr") and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ---------- settings: change these two lines when you pick a name ----------
NAME = "Vega"          # what the assistant calls itself on screen
MODEL = "vega"         # the Ollama model you created: ollama create vega -f Modelfile-full
# ---------------------------------------------------------------------------

import json
import os
import re
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WHISPER_SIZE = "base.en"  # "tiny.en" = faster, "small.en" = more accurate
MEMORY_FILE = os.path.join(BASE_DIR, "memory.json")
FACTS_FILE = os.path.join(BASE_DIR, "facts.txt")   # permanent notebook: one fact per line, you can also edit it in Notepad
MEMORY_TURNS = 20         # how many recent messages to remember between runs
SAMPLE_RATE = 16000

import numpy as np
import ollama
import pyttsx3
import sounddevice as sd
from faster_whisper import WhisperModel


def load_memory():
    """Load recent conversation from memory.json, if it exists."""
    if not os.path.exists(MEMORY_FILE):
        return []
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [m for m in data if m.get("role") in ("user", "assistant") and m.get("content")]
    except (OSError, json.JSONDecodeError):
        return []


def save_memory(history):
    """Save only the most recent messages, so the file stays small."""
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history[-MEMORY_TURNS:], f, ensure_ascii=False, indent=1)


def load_facts():
    """Read the permanent notebook (one fact per line)."""
    if not os.path.exists(FACTS_FILE):
        return []
    with open(FACTS_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def save_facts(facts):
    with open(FACTS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(facts) + ("\n" if facts else ""))


def handle_notebook(text):
    """
    Handle 'remember that ...', 'what do you remember', and 'forget ...'.
    Returns a reply string if it was a notebook command, otherwise None.
    """
    t = text.strip()

    m = re.match(r"^\s*remember\s+that[\s,:]+(.+)$", t, flags=re.I)
    if m:
        fact = m.group(1).strip().rstrip(".!?")
        if not fact:
            return "What should I remember?"
        facts = load_facts()
        if fact.lower() not in (x.lower() for x in facts):
            facts.append(fact)
            save_facts(facts)
        return f"Got it, I'll remember that {fact}."

    if re.search(r"what do you (remember|know) about me", t, flags=re.I):
        facts = load_facts()
        if not facts:
            return "My notebook about you is empty so far. Tell me things by saying: remember that..."
        return "Here's what I've noted about you: " + "; ".join(facts) + "."

    m = re.match(r"^\s*forget(?:\s+about|\s+that)?[\s,:]+(.+)$", t, flags=re.I)
    if m:
        word = m.group(1).strip().rstrip(".!?").lower()
        facts = load_facts()
        kept = [x for x in facts if word not in x.lower()]
        removed = len(facts) - len(kept)
        save_facts(kept)
        if removed == 0:
            return f"I didn't have anything noted about {word}."
        return f"Done. I forgot {removed} note{'s' if removed != 1 else ''} about {word}."

    return None


def record_until_enter():
    """Record from the microphone until the user presses Enter again."""
    chunks = []

    def callback(indata, frames, time_info, status):
        chunks.append(indata.copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback):
        input("  Listening... press Enter when you're done speaking. ")

    if not chunks:
        return None
    return np.concatenate(chunks, axis=0).flatten()


def transcribe(whisper, audio):
    """Turn recorded audio into text."""
    segments, _ = whisper.transcribe(audio, language="en", vad_filter=True)
    return " ".join(seg.text.strip() for seg in segments).strip()


def clean_for_speech(text):
    """Remove markdown symbols so the voice doesn't read out stars and hashes."""
    text = re.sub(r"```.*?```", " (I've put the code on screen.) ", text, flags=re.S)
    text = re.sub(r"[*_#`>|]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def speak(text):
    """Read text out loud with the Windows voice."""
    try:
        engine = pyttsx3.init()          # a fresh engine each time avoids a known freeze
        engine.setProperty("rate", 175)
        engine.say(clean_for_speech(text))
        engine.runAndWait()
        engine.stop()
    except Exception as e:
        print(f"  (Couldn't speak: {e})")


def ask(history):
    """Send the notebook plus the conversation to the Ollama model and get the reply."""
    facts = load_facts()
    notebook = []
    if facts:
        notebook = [
            {"role": "user", "content": "Things I have asked you to remember about me:\n- " + "\n- ".join(facts)},
            {"role": "assistant", "content": "Got it. I'll keep these in mind."},
        ]
    response = ollama.chat(model=MODEL, messages=notebook + history)
    return response["message"]["content"].strip()


def main():
    print(f"Starting {NAME}...")

    # Check the Ollama model exists
    try:
        names = [m.model for m in ollama.list().models]
    except Exception:
        print("Can't reach Ollama. Open the Ollama app (or run 'ollama serve') and try again.")
        return
    if not any(n == MODEL or n.startswith(MODEL + ":") for n in names):
        print(f"No model called '{MODEL}'. Create it first:")
        print(f"  ollama create {MODEL} -f Modelfile-full")
        return

    print("Loading speech recognition (the first run downloads it, about 150 MB)...")
    whisper = WhisperModel(WHISPER_SIZE, device="cpu", compute_type="int8")

    history = load_memory()
    if history:
        print(f"Remembering our last {len(history)} messages.")

    greeting = f"Hi Hemanth, {NAME} here. What do you need?"
    print(f"\n{NAME}: {greeting}")
    speak(greeting)

    while True:
        print()
        typed = input("Press Enter to talk, or type a message (q to quit): ").strip()

        if typed.lower() in ("q", "quit", "exit"):
            bye = "Bye Hemanth. Don't forget today's task."
            print(f"{NAME}: {bye}")
            speak(bye)
            break

        if typed:
            text = typed
        else:
            audio = record_until_enter()
            if audio is None or len(audio) < SAMPLE_RATE * 0.5:
                print("  I didn't hear anything. Try again.")
                continue
            text = transcribe(whisper, audio)
            if not text:
                print("  I couldn't make out any words. Try again, a bit closer to the mic.")
                continue
            print(f"You said: {text}")

        note_reply = handle_notebook(text)
        if note_reply:
            print(f"\n{NAME}: {note_reply}")
            speak(note_reply)
            continue

        stamp = datetime.now().strftime("%d %b %Y, %I:%M %p")
        history.append({"role": "user", "content": f"[{stamp}] {text}"})

        print(f"  {NAME} is thinking...")
        try:
            reply = ask(history)
        except Exception as e:
            print(f"  Something went wrong talking to Ollama: {e}")
            history.pop()
            continue

        history.append({"role": "assistant", "content": reply})
        save_memory(history)

        print(f"\n{NAME}: {reply}")
        speak(reply)


if __name__ == "__main__":
    main()
