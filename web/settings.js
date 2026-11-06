// ===================================================
// VEGA 2.0 - extra settings: "just talk", PIN and lock, keys, quit
// ===================================================

(function () {
  const $ = (id) => document.getElementById(id);

  const OLD_COPY = "An OLD copy of Vega is still running in the background, so this doesn't work yet. " +
                   "Double-click the Vega icon on your desktop: the new Vega replaces the old one.";

  function post(url, body) {
    const fetchFunc = (typeof window !== "undefined" && window.apiFetch) ? window.apiFetch : fetch;
    return fetchFunc(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })
      .then(async r => {
        const contentType = r.headers.get("content-type");
        if (contentType && contentType.includes("application/json")) {
          return r.json();
        }
        const text = await r.text();
        try { return JSON.parse(text); }
        catch (e) { return { ok: false, error: (r.status === 404 || r.status === 405) ? OLD_COPY : `Vega error ${r.status}. Look in debug_vega.log.` }; }
      });
  }

  // The page is new but the program behind it is old (an update while Vega kept running): say so clearly.
  fetch("/api/status").then(r => r.json()).then(st => {
    const pageVersion = window.VegaFrontendVersion;
    if (!pageVersion || st.version === pageVersion) return;
    const bar = document.createElement("div");
    bar.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:100000;background:#7c2d12;color:#fff;" +
                        "padding:10px 16px;font:14px Segoe UI,sans-serif;text-align:center";
      bar.textContent = OLD_COPY;
    document.body.appendChild(bar);
  }).catch(() => {});

  function fill(st) {
    if (!st) return;
    if ($("direct-toggle-check")) $("direct-toggle-check").checked = !!st.talk_without_wake;
    if ($("voice-toggle-check") && st.voice_on !== undefined) $("voice-toggle-check").checked = !!st.voice_on;
    if ($("cloud-speech-check")) {
      $("cloud-speech-check").checked = false;
      $("cloud-speech-check").disabled = true;
    }
    if ($("speech-language-select")) $("speech-language-select").value = st.voice_language || "auto";
    const sel = $("lock-minutes-select");
    if (sel && st.lock_minutes !== undefined) {
      const v = String(st.lock_minutes);
      if (![...sel.options].some(o => o.value === v)) sel.add(new Option(`${v} minutes`, v));
      sel.value = v;
    }
    if (st.has_pin === false && $("pin-old")) $("pin-old").placeholder = "(no PIN yet)";
    if (st.keys) {
      $("gemini-key-status").textContent = st.keys.gemini ? "A Gemini key is saved. Paste a new one only to replace it." : "No Gemini key yet: Vega uses the laptop model only.";
      $("telegram-key-status").textContent = st.keys.telegram ? "A Telegram token is saved. Paste a new one only to replace it." : "No Telegram token yet: the phone link is off.";
    }
    ["groq", "cerebras", "openrouter", "mistral", "nvidia"].forEach(n => {
      const el = $(n + "-key-status");
      if (el && st.keys) el.textContent = st.keys[n] ? "A key is saved. Paste a new one only to replace it." : "No key yet.";
    });
    document.querySelectorAll(".segmented-control .seg-btn").forEach(b =>
      b.classList.toggle("active", st.private_mode ? b.dataset.mode === "private" : b.dataset.mode === "online"));
  }

  $("direct-toggle-check").addEventListener("change", (e) =>
    post("/api/settings/update", { key: "talk_without_wake", value: e.target.checked }));

  const VOICE_LINES = ["Hey Vega, what's the weather today?", "Hey Vega, open YouTube and play some music.",
    "Hey Vega, remind me to study Linux at seven.", "Hey Vega, it's me, Hemanth. Unlock please."];
  $("voice-setup-btn").addEventListener("click", async () => {
    const btn = $("voice-setup-btn"), msg = $("voice-setup-msg");
    btn.disabled = true;
    try {
      for (let i = 0; i < VOICE_LINES.length; i++) {
        msg.innerHTML = `Recording ${i + 1} of ${VOICE_LINES.length}. Say in your normal voice:<br><b>“${VOICE_LINES[i]}”</b>`;
        let res = await post("/api/voice/enroll_clip", { first: i === 0 });
        if (!res.ok) {  // one more try for this sentence
          msg.innerHTML = `${res.error}<br>Again: <b>“${VOICE_LINES[i]}”</b>`;
          res = await post("/api/voice/enroll_clip", {});
          if (!res.ok) { msg.textContent = res.error; return; }
        }
      }
      msg.textContent = "Saving your voice…";
      const done = await post("/api/voice/enroll_finish", {});
      msg.textContent = done.ok ? done.text : done.error;
    } catch (e) {
      msg.textContent = "Error: " + e.message;
    } finally {
      btn.disabled = false;
    }
  });

  $("lock-minutes-select").addEventListener("change", (e) =>
    post("/api/settings/lock_minutes", { minutes: +e.target.value }).then(res => {
      $("pin-msg").textContent = res.ok ? (res.minutes ? `I'll lock myself after ${res.minutes} minutes without use.` :
        "I'll only lock when I start.") : "Couldn't change that.";
    }));

  $("pin-save-btn").addEventListener("click", () => {
    const old = $("pin-old").value, a = $("pin-new").value, b = $("pin-new2").value;
    if (a !== b) { $("pin-msg").textContent = "The two new PINs are different. Try again."; return; }
    post("/api/pin/set", { old, new: a }).then(res => {
      $("pin-msg").textContent = res.ok ? "New PIN saved. ✓" : res.error;
      if (res.ok) ["pin-old", "pin-new", "pin-new2"].forEach(id => ($(id).value = ""));
    });
  });

  function clearBox(id) {
    const el = $(id);
    el.value = "";
    el.dispatchEvent(new Event("input"));   // resets the "✓ N characters" counter
  }

  function saveKey(inputId, field, statusId) {
    const v = $(inputId).value.trim();
    if (!v) return;
    $(statusId).textContent = "Saving…";
    post("/api/keys", { [field]: v }).then(res => {
      clearBox(inputId);
      if (!res.ok) { $(statusId).textContent = res.error || "Couldn't save it."; return; }
      $(statusId).textContent = res.gemini || res.telegram || "Saved.";
    }).catch(err => ($(statusId).textContent = "Error: " + err.message));
  }
  $("gemini-key-save").addEventListener("click", () => saveKey("gemini-key-input", "gemini_api_key", "gemini-key-status"));
  $("telegram-token-save").addEventListener("click", () => saveKey("telegram-token-input", "telegram_token", "telegram-key-status"));

  ["groq", "cerebras", "openrouter", "mistral", "nvidia"].forEach(n => {
    const b = $(n + "-key-save");
    if (b) b.addEventListener("click", () => {
      const v = $(n + "-key-input").value.trim();
      if (!v) return;
      $(n + "-key-status").textContent = "Saving and testing…";
      post("/api/keys", { [n + "_api_key"]: v }).then(res => {
        clearBox(n + "-key-input");
        $(n + "-key-status").textContent = res.ok
          ? (res[n] || "Saved, but this Vega is an OLD copy still running. Click Settings → Quit Vega, then open her again from the desktop icon.")
          : (res.error || "Couldn't save it.");
        showBrains();
      }).catch(err => ($(n + "-key-status").textContent = "Error: " + err.message));
    });
  });

  function showBrains() {
    fetch("/api/brains").then(r => r.json()).then(res => {
      const box = $("brains-list");
      if (!box) return;
      if (!res.ok) { box.textContent = "Couldn't check."; return; }
      const state = { ready: "Ready", limit: "Limit reached", nokey: "No key" };
      box.innerHTML = res.brains.map(b => `<span class="brain-state">${state[b.state] || "Unavailable"}</span> <b>${b.name}</b>: ${b.text}`).join("<br>");
    }).catch(() => {
      const box = $("brains-list");
      if (box) box.textContent = "This Vega is an OLD copy still running. Click Quit Vega (at the bottom of Settings), then open her again from the desktop icon.";
    });
  }
  $("brains-refresh").addEventListener("click", showBrains);

  // Key boxes: show that something was pasted (the key itself stays hidden), plus Paste and Show buttons
  document.querySelectorAll('input[type="password"][id$="-key-input"], #telegram-token-input').forEach(inp => {
    const row = inp.closest(".inline-row");
    if (!row) return;
    const hint = document.createElement("span");
    hint.className = "key-count";
    hint.style.cssText = "font-size:12px;opacity:.75;margin-left:6px;white-space:nowrap";
    const mk = (label, title, fn) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "mini-btn"; b.textContent = label; b.title = title;
      b.addEventListener("click", fn);
      return b;
    };
    const update = () => {
      const n = inp.value.length;
      hint.textContent = n ? `✓ ${n} characters` : "";
    };
    inp.addEventListener("input", update);
    inp.addEventListener("paste", () => setTimeout(update, 50));
    const save = row.querySelector("button.accent");
    row.insertBefore(mk("Paste", "Paste from the clipboard", () => {
      inp.focus();
      if (navigator.clipboard && navigator.clipboard.readText) {
        navigator.clipboard.readText().then(t => { inp.value = (t || "").trim(); update(); })
          .catch(() => { hint.textContent = "Click the box and press Ctrl + V"; });
      } else {
        hint.textContent = "Click the box and press Ctrl + V";
      }
    }), save);
    const showButton = mk("Show", "Show or hide what you typed", () => {
      inp.type = inp.type === "password" ? "text" : "password";
      showButton.textContent = inp.type === "password" ? "Show" : "Hide";
    });
    row.insertBefore(showButton, save);
    row.insertBefore(hint, save);
  });

  $("open-vega-folder-btn").addEventListener("click", () => post("/api/open_folder", { folder: "vega" }));

  $("quit-vega-btn").addEventListener("click", () => {
    if (!confirm("Quit Vega?\n\n“Hey Vega”, reminders and the phone link stop until you open her again.\n" +
                 "(To just hide her, close the window: she keeps running by the clock.)")) return;
    post("/api/quit").finally(() => {
      document.body.innerHTML = '<div style="padding:40px;font-family:Segoe UI,sans-serif;color:#aaa">Vega has quit. You can close this window.</div>';
    });
  });

  window.VegaSettings = { fill };
})();
