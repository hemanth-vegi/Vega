# 🌌 Vega 2.0 — Local-First AI Assistant & Engineering Workstation Companion

Vega is an autonomous, local-first Windows AI desktop assistant, engineering workstation companion, and DevOps study hub. Built with a modular Python backend and a zero-build Vanilla JS web interface, Vega seamlessly integrates local LLMs (via Ollama) and cloud providers (Gemini, Groq, Cerebras, OpenRouter) with desktop automation, voice interaction, developer telemetry, and an interactive study curriculum.

---

## ✨ Features at a Glance

### 🖥️ Tri-Workspace Interface
1. **Chat Mode**:
   - Multi-attachment support (Ctrl+V screenshots, file drag-and-drop, PDFs).
   - Fast Markdown and syntax-highlighted code rendering.
   - Provider picker: Auto-routing, Groq (70B / 120B), Google Gemini (2.5 Flash / Flash Lite), and Local Ollama models (Qwen 2.5 Coder, Gemma 2).
2. **Agent Workspace**:
   - Local & cloud agent task execution with sandboxed tools.
   - Reviewable edit-diff proposals and redacted action audit logs.
3. **Practice Lab**:
   - Disposable, in-memory Linux terminal simulator (`/home/student`) supporting safe sandboxed commands (`ls`, `pwd`, `cd`, `mkdir`, `touch`, `cat`, `dnf`, `rpm`) with zero host shell risk.

### 🔬 DevOps & Coding Study Hub (Lab Log)
- Interactive study tracking across 3 curricula:
  - **Linux Foundations & DevOps**: 168 structured lessons
  - **Python Core & Automation**: 36 lessons
  - **C Systems Programming**: 24 lessons
- Daily checklist, notes scratchpad, streak tracking, interactive 2D progress map, and AI log assessments.
- Hands-free voice commands to mark steps, append notes, and query study summaries.

### 🎙️ Voice & Biometrics Pipeline
- **Offline STT**: `faster-whisper` (CPU int8) supporting English and Telugu/Tenglish with 3-second mic diagnostics.
- **Natural TTS**: `edge-tts` with offline `pyttsx3` fallback.
- **Speaker Verification & Wake-Word**: "Hey Vega" wake-word with optional SpeechBrain voiceprint verification (`voiceprint.npy`) and "Wake only me" protection.

### ⚡ Workstation Telemetry & Arc Reactor HUD Mode
- Real-time CPU, RAM, Battery, and dedicated NVIDIA GPU % / VRAM monitoring streamed via Server-Sent Events (SSE).
- Futuristic **Arc Reactor HUD Mode** featuring concentric rotating energy rings bound to the Web Audio API (`AnalyserNode`) that react to microphone input volume and speech output.

### 🔒 Enterprise-Grade Local Security & Zero-Leak Architecture
- **CSRF & Origin Protection**: Transient session tokens (`X-Vega-Token`) and strict `Host` / `Origin` header validation on mutating endpoints.
- **Thread-Safe SQLite WAL Engine**: ACID persistence in `vega.db` with WAL mode, 30s busy timeout, and FTS5 full-text conversation search.
- **Safety Confirmation Gate**: Dangerous actions (`system_shutdown`, `restart`, `kill_process`) trigger explicit confirmation before running.
- **PIN & Inactivity Auto-Lock**: Screen locking with PIN verification and voice unlock.
- **Zero-Leak Design**: Personal chat history, biometrics, PINs, and credentials strictly excluded from version control.

---

## 🏗️ Project Architecture

```
vega/
├── vega_app.py              # Main bootstrap entrypoint & system tray
├── vega_webview.py          # Bottle web application & API router
├── core/
│   ├── config.py            # Dynamic binary & system path discovery
│   ├── supervisor.py        # Self-healing thread supervisor
│   └── events.py            # Thread-safe SSE pub/sub event bus
├── storage/
│   ├── db.py                # SQLite WAL database & FTS5 full-text search
│   └── contacts.py          # CSV/JSON contact importer & fuzzy matcher
├── tools/
│   ├── registry.py          # Tool decorator registry & safety confirmation gate
│   ├── system_tools.py      # Apps, volume, media, power, VirtualBox
│   ├── vision.py            # In-memory screen capture & multimodal analysis
│   ├── telemetry.py         # Hardware & NVIDIA GPU/VRAM telemetry
│   ├── lablog_tools.py      # Voice tools for Lab Log steps & notes
│   └── dev_media.py         # YouTube focus audio & WhatsApp contact launcher
├── llm/
│   ├── engine.py            # Unified LLM provider adapters & tool calling loop
│   ├── router.py            # Intent-based task router
│   └── distill.py           # Training dataset exporter (vega_training.jsonl)
├── audio/
│   ├── stt.py               # faster-whisper speech-to-text
│   ├── tts.py               # edge-tts & pyttsx3 speech synthesis
│   └── wake.py              # Wake-word detection & voiceprint verification
├── server/
│   ├── middleware.py        # Origin validation & CSRF token authentication
│   ├── routes_chat.py       # Chat & streaming endpoints
│   ├── routes_lablog.py     # Lab Log & curriculum routes
│   └── routes_agent.py      # Agent tasks & Practice Lab endpoints
└── web/
    ├── index.html           # Main UI shell (Sunsetting Red & Dark Obsidian)
    ├── style.css            # Responsive layout & 56px icon rail
    ├── hud.css / hud.js     # Web Audio API Arc Reactor HUD visualizer
    ├── api_client.js        # Automatic X-Vega-Token fetch wrapper
    ├── app.js               # Main UI controller & SSE handler
    ├── lablog.js            # Lab Log view & progress matrix
    ├── settings.js          # Settings & security configuration
    └── agent_lab.js         # Agent workspace & Practice Lab terminal
```

---

## 🚀 Quick Start

### 1. Requirements
- Windows 10 / 11 (64-bit)
- Python 3.10+ (Python 3.11 recommended)
- Optional: [Ollama](https://ollama.ai) for 100% offline local model inference

### 2. Installation

Clone the repository and install dependencies:

```powershell
# Clone the repository
git clone https://github.com/hemanth-vegi/mybot.git
cd mybot

# Install dependencies
python -m pip install -r requirements-vega.txt
python -m pip install bottle pystray pillow comtypes
```

*(Optional) For voice unlock biometric speaker verification:*
```powershell
python setup_voice.py
```

### 3. Environment Configuration

Copy `.env.example` to `.env` and configure your API keys (all keys are optional; local Ollama works out of the box):

```powershell
Copy-Item .env.example .env
```

Edit `.env`:
```ini
PORT=47632
HOST=127.0.0.1

# Cloud AI Providers (Optional)
GEMINI_API_KEY="your-gemini-key"
GROQ_API_KEY="your-groq-key"

# Telegram Remote Bridge (Optional)
TELEGRAM_BOT_TOKEN="your-telegram-bot-token"
```

### 4. Running Vega

Launch Vega using any of the following methods:

- **Double-click:** `Start Vega.bat` (launches background service with tray icon)
- **Console Mode:** `Start Vega (show errors).bat`
- **PowerShell:**
  ```powershell
  python vega_webview.py
  ```

Vega will open in your browser at `http://127.0.0.1:47632`.

---

## 🛡️ Privacy & Security

- **Local-First**: All conversation databases (`vega.db`), voiceprints (`voiceprint.npy`), local settings, and reminders stay on your machine.
- **Zero Cloud Leak**: Vega is configured by default with strict `.gitignore` rules ensuring personal credentials and databases are never committed to version control.
- **Local Origin Enforcement**: API endpoints reject any request originating outside `127.0.0.1` or lacking the active session token.

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
