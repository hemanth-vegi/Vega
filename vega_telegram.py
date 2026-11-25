"""
Talk to Vega from your phone with Telegram.

How it works: you make your own private Telegram bot (free, 2 minutes with
@BotFather). Vega checks that bot for new messages while your laptop is on,
answers them on your laptop, and sends the answer back to your phone.
Only YOUR Telegram chat is allowed: anyone else gets no answer.

Messages go through Telegram's servers, so don't send passwords or bank
details to Vega from your phone.
"""

import sys
if hasattr(sys, "stdout") and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys, "stderr") and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


import asyncio
import os
import re
import tempfile
import threading
import time


class TelegramBridge(threading.Thread):
    def __init__(self, vega, ears, on_event):
        """
        on_event(kind, *data):
          ("pair_request", chat_id, name)  someone messaged the bot and no chat is paired yet
          ("phone", text_in, text_out)     a message from your phone was answered
          ("status", message)              connected / problems
        """
        super().__init__(daemon=True)
        self.vega, self.ears, self.on_event = vega, ears, on_event
        self.token = str(os.environ.get("TELEGRAM_TOKEN") or vega.cfg.get("telegram_token") or "").strip()
        self.chat_id = str(vega.cfg.get("telegram_chat_id") or "").strip()
        self.running = True
        self.asked = set()

    # ------------- Telegram API
    def api(self, method, wait=40, **params):
        import requests
        try:
            r = requests.post(f"https://api.telegram.org/bot{self.token}/{method}", json=params, timeout=wait)
            data = r.json()
        except Exception as exc:
            # Request exceptions often include the full URL, which contains the bot token.
            raise RuntimeError(f"Telegram request failed ({type(exc).__name__})") from None
        if not data.get("ok"):
            raise RuntimeError(self._redact(data.get("description") or f"HTTP {r.status_code}"))
        return data["result"]

    def _redact(self, message):
        message = str(message)
        if self.token:
            message = message.replace(self.token, "[HIDDEN]")
        return re.sub(r"(https?://api\.telegram\.org/bot)[^/\s]+", r"\1[HIDDEN]", message, flags=re.I)

    def send(self, text, chat_id=None, buttons=None):
        chat_id = chat_id or self.chat_id
        if not (self.token and chat_id and text):
            return
        text = re.sub(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)", r"\1\n\2", text)  # [title](link) -> title + link on phone
        for i in range(0, len(text), 3900):
            chunk = text[i:i + 3900]
            payload = {"chat_id": chat_id, "text": chunk}
            if buttons and i + 3900 >= len(text):
                payload["reply_markup"] = {"inline_keyboard": buttons}
            try:
                self.api("sendMessage", wait=15, **payload)
            except Exception as e:
                self.report(f"Couldn't send to your phone: {e}")

    def report(self, message):
        """Show a phone-link problem in the Vega window (once per minute at most)."""
        message = self._redact(message)
        now = time.time()
        if message != getattr(self, "_last_msg", None) or now - getattr(self, "_last_time", 0) > 60:
            self._last_msg, self._last_time = message, now
            self.on_event("status", message)

    def pair(self, chat_id):
        """Called by the app after you click Allow."""
        self.chat_id = str(chat_id)
        welcome_buttons = [
            [{"text": "📋 Tasks", "callback_data": "cmd:my tasks"},
             {"text": "⚡ Status", "callback_data": "cmd:battery"}],
            [{"text": "📸 Screenshot", "callback_data": "cmd:take a screenshot"},
             {"text": "🔒 Lock Laptop", "callback_data": "cmd:lock my laptop"}]
        ]
        self.send("Connected! I'm Vega, running on your laptop.\n\n"
                  "Quick shortcuts:\n"
                  "• /screenshot or /ss - take & receive screenshot\n"
                  "• /lock - lock laptop screen\n"
                  "• /status or /battery - laptop power & CPU\n"
                  "• /docker - active containers\n"
                  "• /tasks or /reminders\n\n"
                  "Or send voice notes to talk to me!", buttons=welcome_buttons)

    # ------------- main loop
    def run(self):
        if not self.token:
            return
        try:
            me = self.api("getMe", wait=15)
            self.on_event("status", f"Phone link on: Telegram bot @{me.get('username')}")
        except Exception as e:
            self.report(f"Telegram didn't accept the bot token ({e}). Check it in Menu → Connect phone.")
            return
        offset = None
        started = time.time()
        retry_delay = 5
        outage_reported = False
        while self.running:
            try:
                params = {"timeout": 30, "allowed_updates": ["message", "callback_query"]}
                if offset is not None:
                    params["offset"] = offset
                updates = self.api("getUpdates", wait=45, **params)
            except Exception as e:
                if not self.running:
                    return
                if "Conflict" in str(e):
                    message = "Another copy of Vega is using the phone link. Close the other one."
                    wait = 30
                elif any(k in str(e).lower() for k in ("nameresolutionerror", "getaddrinfo", "connectionerror", "timed out", "timeout")):
                    message = "Phone link waiting for internet connection; retrying with backoff."
                    wait = retry_delay
                else:
                    message = f"Phone link problem, retrying: {e}"
                    wait = retry_delay
                if not outage_reported:
                    self.report(message)
                    outage_reported = True
                if "Conflict" not in str(e):
                    retry_delay = min(retry_delay * 2, 60)
                time.sleep(wait)
                continue
            if not self.running:
                return
            if outage_reported:
                self.report("Phone link reconnected.")
                outage_reported = False
            retry_delay = 5
            for u in updates:
                offset = u["update_id"] + 1

                # Handle interactive button clicks
                cb = u.get("callback_query")
                if cb:
                    threading.Thread(target=self.handle_callback, args=(cb,), daemon=True).start()
                    continue

                msg = u.get("message") or {}
                if not msg or msg.get("date", 0) < started - 600:
                    continue  # skip old messages from before Vega started
                chat = str(msg.get("chat", {}).get("id", ""))
                if not self.chat_id:
                    if chat not in self.asked:
                        self.asked.add(chat)
                        who = msg.get("from", {}).get("first_name", "someone")
                        self.send("Waiting for approval on the laptop. Click Allow in the Vega window.", chat)
                        self.on_event("pair_request", chat, who)
                    continue
                if chat != self.chat_id:
                    continue  # not you: ignore completely
                threading.Thread(target=self.answer, args=(msg,), daemon=True).start()

    def handle_callback(self, cb):
        cb_id = cb.get("id")
        data = str(cb.get("data") or "")
        msg = cb.get("message") or {}
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if chat_id != self.chat_id:
            return
        try:
            self.api("answerCallbackQuery", wait=10, callback_query_id=cb_id, text="⚡ Vega working on it...")
        except Exception:
            pass

        if data.startswith("cmd:"):
            command = data[4:].strip()
            self.execute_command(command, is_voice=False)

    def answer(self, msg):
        raw_text = (msg.get("text") or "").strip()
        is_voice = bool(msg.get("voice") or msg.get("audio"))
        if is_voice:
            raw_text = self.voice_to_text(msg.get("voice") or msg.get("audio"))
            if not raw_text:
                self.send("I couldn't make out that voice note. Try again or type it.")
                return
            self.send(f"🎤 (Heard: \"{raw_text}\")")
        if not raw_text:
            return

        # Normalize Slash Commands
        text = raw_text
        if text.startswith("/"):
            parts = text[1:].split(None, 1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""
            if cmd in ("start", "help"):
                text = "help"
            elif cmd in ("screenshot", "ss"):
                text = "take a screenshot"
            elif cmd in ("lock", "locklaptop"):
                text = "lock my laptop"
            elif cmd in ("battery", "status"):
                text = "battery"
            elif cmd in ("docker", "containers"):
                text = f"docker {arg}".strip() if arg else "docker status"
            elif cmd in ("tasks", "todo"):
                text = "my tasks"
            elif cmd in ("reminders",):
                text = "my reminders"
            elif cmd in ("vm", "server"):
                text = f"{arg} my vm".strip() if arg else "is my vm running"
            else:
                text = text[1:].strip()

        self.execute_command(text, is_voice=is_voice)

    def execute_command(self, text, is_voice=False):
        try:
            self.api("sendChatAction", wait=10, chat_id=self.chat_id, action="typing")
        except Exception:
            pass
        try:
            out = self.vega.handle(text, source="phone", spoken=is_voice)
        except Exception as e:
            out = {"text": f"Something went wrong on the laptop: {e}"}

        response_text = out.get("text", "")
        # Attach helpful inline buttons if it was a task/reminder query
        buttons = None
        lower_t = text.lower()
        if any(k in lower_t for k in ("task", "todo", "reminder")):
            buttons = [
                [{"text": "📋 My Tasks", "callback_data": "cmd:my tasks"},
                 {"text": "⏰ Reminders", "callback_data": "cmd:my reminders"}],
                [{"text": "📸 Screenshot", "callback_data": "cmd:take a screenshot"}]
            ]

        self.send(response_text, buttons=buttons)

        if out.get("photo"):
            self.send_photo(out["photo"])

        # If user sent a voice note, answer with voice note as well!
        if is_voice and response_text:
            self.send_voice(response_text)

        self.on_event("phone", text, response_text)

    def send_photo(self, path):
        """Send a picture (like a screenshot) to your phone only."""
        import requests
        try:
            with open(path, "rb") as f:
                r = requests.post(f"https://api.telegram.org/bot{self.token}/sendPhoto",
                                  data={"chat_id": self.chat_id}, files={"photo": f}, timeout=60)
            if not r.json().get("ok"):
                raise RuntimeError(r.json().get("description"))
        except Exception as e:
            self.send(f"I took it, but couldn't send the picture: {e}")

    def send_voice(self, text, chat_id=None):
        """Send spoken voice note back to phone using edge-tts."""
        chat_id = chat_id or self.chat_id
        if not (self.token and chat_id and text):
            return
        import edge_tts
        clean = re.sub(r"https?://\S+", "", text)
        clean = re.sub(r"[*_`#\[\]\n]", " ", clean)
        clean = re.sub(r"\s+", " ", clean)[:350].strip()
        if not clean:
            return
        try:
            self.api("sendChatAction", wait=10, chat_id=chat_id, action="record_voice")
        except Exception:
            pass

        fd, path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        try:
            voice = self.vega.cfg.get("telugu_voice", "te-IN-ShrutiNeural") if self.vega.cfg.get("speak_telugu") else "en-IN-NeerjaNeural"
            async def _synth():
                comm = edge_tts.Communicate(clean, voice)
                await comm.save(path)
            asyncio.run(_synth())
            import requests
            with open(path, "rb") as f:
                requests.post(f"https://api.telegram.org/bot{self.token}/sendVoice",
                              data={"chat_id": chat_id}, files={"voice": f}, timeout=45)
        except Exception:
            pass  # If voice generation fails (e.g. offline), text is already delivered
        finally:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

    def voice_to_text(self, voice):
        import requests
        try:
            info = self.api("getFile", wait=15, file_id=voice["file_id"])
            url = f"https://api.telegram.org/file/bot{self.token}/{info['file_path']}"
            data = requests.get(url, timeout=30).content
            fd, path = tempfile.mkstemp(suffix=".ogg")
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            try:
                return self.ears.transcribe_file(path)
            finally:
                os.remove(path)
        except Exception:
            return ""
