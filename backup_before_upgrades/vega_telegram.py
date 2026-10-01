"""
Talk to Vega from your phone with Telegram.

How it works: you make your own private Telegram bot (free, 2 minutes with
@BotFather). Vega checks that bot for new messages while your laptop is on,
answers them on your laptop, and sends the answer back to your phone.
Only YOUR Telegram chat is allowed: anyone else gets no answer.

Messages go through Telegram's servers, so don't send passwords or bank
details to Vega from your phone.
"""

import os
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
        self.token = str(vega.cfg.get("telegram_token") or "").strip()
        self.chat_id = str(vega.cfg.get("telegram_chat_id") or "").strip()
        self.running = True
        self.asked = set()

    # ------------- Telegram API
    def api(self, method, wait=40, **params):
        import requests
        r = requests.post(f"https://api.telegram.org/bot{self.token}/{method}", json=params, timeout=wait)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description") or f"HTTP {r.status_code}")
        return data["result"]

    def send(self, text, chat_id=None):
        chat_id = chat_id or self.chat_id
        if not (self.token and chat_id and text):
            return
        import re
        text = re.sub(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)", r"\1\n\2", text)  # [title](link) -> title + link on the phone
        for i in range(0, len(text), 3900):
            try:
                self.api("sendMessage", wait=15, chat_id=chat_id, text=text[i:i + 3900])
            except Exception as e:
                self.report(f"Couldn't send to your phone: {e}")

    def report(self, message):
        """Show a phone-link problem in the Vega window (once per minute at most)."""
        now = time.time()
        if message != getattr(self, "_last_msg", None) or now - getattr(self, "_last_time", 0) > 60:
            self._last_msg, self._last_time = message, now
            self.on_event("status", message)

    def pair(self, chat_id):
        """Called by the app after you click Allow."""
        self.chat_id = str(chat_id)
        self.send("Connected. I'm Vega, running on your laptop. Try: 'weather', 'my reminders', "
                  "'remind me in 10 minutes to stretch', or just chat. Voice notes work too.")

    # ------------- main loop
    def run(self):
        if not self.token:
            return
        try:
            me = self.api("getMe", wait=15)
            self.on_event("status", f"Phone link on: Telegram bot @{me.get('username')}")
        except Exception as e:
            self.on_event("status", f"Telegram didn't accept the bot token ({e}). Check it in Menu → Connect phone.")
            return
        offset = None
        started = time.time()
        while self.running:
            try:
                params = {"timeout": 30, "allowed_updates": ["message"]}
                if offset is not None:
                    params["offset"] = offset
                updates = self.api("getUpdates", wait=45, **params)
            except Exception as e:
                if not self.running:
                    return
                if "Conflict" in str(e):
                    self.report("Another copy of Vega is using the phone link. Close the other one.")
                    time.sleep(30)
                else:
                    self.report(f"Phone link problem, retrying: {e}")
                    time.sleep(5)
                continue
            if not self.running:
                return  # a newer phone link took over; it will handle these messages
            for u in updates:
                offset = u["update_id"] + 1
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

    def answer(self, msg):
        text = (msg.get("text") or "").strip()
        if msg.get("voice") or msg.get("audio"):
            text = self.voice_to_text(msg.get("voice") or msg.get("audio"))
            if not text:
                self.send("I couldn't make out that voice note. Try again or type it.")
                return
            self.send(f"(I heard: {text})")
        if not text:
            return
        if text in ("/start", "/help"):
            text = "help"
        try:
            self.api("sendChatAction", wait=10, chat_id=self.chat_id, action="typing")
        except Exception:
            pass
        try:
            out = self.vega.handle(text, source="phone", spoken=bool(msg.get("voice") or msg.get("audio")))
        except Exception as e:
            out = {"text": f"Something went wrong on the laptop: {e}"}
        self.send(out["text"])
        if out.get("photo"):
            self.send_photo(out["photo"])
        self.on_event("phone", text, out["text"])

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
