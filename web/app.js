// ===================================================
// VEGA 2.0 - FRONTEND APPLICATION JAVASCRIPT
// Full implementation connecting every button, panel,
// voice events, live feeds, and real Lab Log.
// ===================================================

document.addEventListener("DOMContentLoaded", () => {
  // Capture client errors and post to Python debug log
  function logClientError(type, message, details = {}) {
    fetch("/api/debug/log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type, message, ...details })
    }).catch(() => {});
  }

  window.onerror = function(message, source, lineno, colno, error) {
    logClientError("window.onerror", String(message), {
      source, lineno, colno, stack: error ? error.stack : ""
    });
  };

  window.addEventListener("unhandledrejection", function(event) {
    logClientError("unhandledrejection", String(event.reason), {
      stack: event.reason && event.reason.stack ? event.reason.stack : ""
    });
  });

  const originalConsoleError = console.error;
  console.error = function(...args) {
    originalConsoleError.apply(console, args);
    const msg = args.map(a => typeof a === "object" ? JSON.stringify(a) : String(a)).join(" ");
    logClientError("console.error", msg);
  };

  // DOM Elements
  const messagesList = document.getElementById("messages-list");
  const messagesContainer = document.getElementById("messages-container");
  const inputContainer = document.getElementById("input-container");
  const modeChatTab = document.getElementById("mode-chat-tab");
  const modeAgentTab = document.getElementById("mode-agent-tab");
  const modePracticeTab = document.getElementById("mode-practice-tab");
  const modeHudTab = document.getElementById("mode-hud-tab");
  const practiceWorkspace = document.getElementById("practice-workspace");
  const agentWorkspace = document.getElementById("agent-workspace");
  const agentMessagesContainer = document.getElementById("agent-messages-container");
  const agentComposer = document.getElementById("agent-composer");
  const agentTogglePanelBtn = document.getElementById("agent-toggle-panel-btn");
  const agentSidePanel = document.getElementById("agent-side-panel");
  const welcomeScreen = document.getElementById("welcome-screen");
  const promptInput = document.getElementById("prompt-input");
  const sendBtn = document.getElementById("send-btn");
  const micBtn = document.getElementById("mic-btn");
  const attachBtn = document.getElementById("attach-btn");
  const fileInput = document.getElementById("file-input");
  const attachmentPreview = document.getElementById("attachment-preview");
  const attachmentPillsList = document.getElementById("attachment-pills-list");
  const clearAllAttachmentsBtn = document.getElementById("clear-all-attachments-btn");
  const promptsBtn = document.getElementById("prompts-btn");
  const promptsModal = document.getElementById("prompts-modal");
  const closePromptsModalBtn = document.getElementById("close-prompts-modal-btn");
  const summariesModal = document.getElementById("summaries-modal");
  const closeSummariesModalBtn = document.getElementById("close-summaries-modal-btn");
  const lablogModal = document.getElementById("lablog-modal");
  const closeLablogModalBtn = document.getElementById("close-lablog-modal-btn");
  const openLablogFullBtn = document.getElementById("open-lablog-full-btn");

  const stopContainer = document.getElementById("stop-generation-container");
  const stopBtn = document.getElementById("stop-btn");
  const chatHistoryContainer = document.getElementById("chat-history-container");
  const chatSearch = document.getElementById("chat-search");
  if (chatSearch) chatSearch.value = ""; // Chromium can restore stale search text when the app window reopens.
  const newChatBtn = document.getElementById("new-chat-btn");
  const currentChatTitle = document.getElementById("current-chat-title");
  const pinChatBtn = document.getElementById("pin-chat-btn");
  const statusBadge = document.getElementById("status-badge");
  const statusBadgeText = document.getElementById("status-badge-text");
  const listeningToggleBtn = document.getElementById("listening-toggle-btn");
  const listeningText = document.getElementById("listening-text");
  const speakingToggleBtn = document.getElementById("speaking-toggle-btn");
  const speakingText = document.getElementById("speaking-text");
  const topQuickbarBtn = document.getElementById("top-quickbar-btn");
  const modelBadge = document.getElementById("model-badge");
  const modelBadgeText = document.getElementById("model-badge-text");
  const themeToggle = document.getElementById("theme-toggle");
  const sidebar = document.getElementById("sidebar");
  const toggleSidebarBtn = document.getElementById("toggle-sidebar-btn");
  const rightPanel = document.getElementById("right-panel");
  const toggleRightPanelBtn = document.getElementById("toggle-right-panel-btn");
  const closePanelBtn = document.getElementById("close-panel-btn");
  const settingsBtn = document.getElementById("settings-btn");
  const settingsModal = document.getElementById("settings-modal");
  const closeSettingsBtn = document.getElementById("close-settings-btn");
  const speedSlider = document.getElementById("speech-rate-slider");
  const speedVal = document.getElementById("speed-val");
  const voiceToggle = document.getElementById("voice-toggle-check");
  const wakeToggle = document.getElementById("wake-toggle-check");
  const cloudSpeechToggle = document.getElementById("cloud-speech-check");
  const speechLanguageSelect = document.getElementById("speech-language-select");
  const speechModelSelect = document.getElementById("speech-model-select");
  const transcriptionStatus = document.getElementById("transcription-status");
  const startupToggle = document.getElementById("startup-toggle-check");

  // Right Panel Lab Log Elements
  const lablogTrackName = document.getElementById("lablog-track-name");
  const lablogDayTag = document.getElementById("lablog-day-tag");
  const hubTaskText = document.getElementById("hub-task-text");
  const hubTaskTopic = document.getElementById("hub-task-topic");
  const hubStepsList = document.getElementById("hub-steps-list");
  const hubNotesInput = document.getElementById("hub-notes-input");
  const saveNotesBtn = document.getElementById("save-notes-btn");
  const assessLogBtn = document.getElementById("assess-log-btn");
  const hubReviewText = document.getElementById("hub-review-text");
  const hubScoreBadge = document.getElementById("hub-score-badge");

  // State
  let isGenerating = false;
  let currentStreamingElement = null;
  let activeChatId = null;
  let allChats = [];
  let isRecording = false;
  let abortController = null;
  let attachedItems = [];
  let currentTrack = "linux";
  let readyText = "Ready - say “Hey Vega”";
  let micFromVoice = false;
  let transcriptReviewActive = false;
  let transcriptReviewLanguage = "";

  // Edge can restore a previous app-window form value after DOMContentLoaded.
  // Clear that stale value briefly after startup, but preserve deliberate typing.
  let chatSearchUserStarted = false;
  if (chatSearch) {
    const markSearchStarted = () => { chatSearchUserStarted = true; };
    chatSearch.addEventListener("keydown", markSearchStarted, { once: true });
    chatSearch.addEventListener("beforeinput", markSearchStarted, { once: true });
    const clearRestoredSearch = () => {
      if (!chatSearchUserStarted && chatSearch.value) {
        chatSearch.value = "";
        renderCurrentChatList();
      }
    };
    window.addEventListener("pageshow", clearRestoredSearch);
    window.addEventListener("load", clearRestoredSearch, { once: true });
    [0, 100, 500, 1500].forEach(delay => setTimeout(clearRestoredSearch, delay));
  }

  // Configure marked with highlight.js
  if (window.marked) {
    const safeRenderer = new marked.Renderer();
    safeRenderer.html = (html) => escapeHtml(html);
    safeRenderer.link = (href, title, text) => {
      if (!isSafeMarkdownUrl(href)) return text;
      let tag = `<a href="${escapeHtml(href)}"`;
      if (title) tag += ` title="${escapeHtml(title)}"`;
      return tag + `>${text}</a>`;
    };
    safeRenderer.image = (href, title, alt) => {
      if (!isSafeMarkdownUrl(href)) return escapeHtml(alt);
      let tag = `<img src="${escapeHtml(href)}" alt="${escapeHtml(alt)}"`;
      if (title) tag += ` title="${escapeHtml(title)}"`;
      return tag + `>`;
    };
    marked.setOptions({
      renderer: safeRenderer,
      highlight: function(code, lang) {
        if (window.hljs && lang && hljs.getLanguage(lang)) {
          return hljs.highlight(code, { language: lang }).value;
        }
        return window.hljs ? hljs.highlightAuto(code).value : code;
      },
      breaks: true,
      gfm: true
    });
  }

  // Auto-resize textarea
  promptInput.addEventListener("input", () => {
    promptInput.style.height = "auto";
    promptInput.style.height = Math.min(promptInput.scrollHeight, 160) + "px";
    if (transcriptReviewActive && transcriptionStatus) {
      transcriptionStatus.textContent = `Transcript edited locally · ${transcriptReviewLanguage}. Review it, then press Send.`;
    }
  });

  // Keyboard shortcut: Enter to send, Shift+Enter for newline.
  promptInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  });

  sendBtn.addEventListener("click", () => handleSend());

  // SSE (Server-Sent Events) live updates from Python
  function connectEventStream() {
    const evSource = new EventSource("/api/events");

    evSource.onmessage = (e) => {
      try {
        const payload = JSON.parse(e.data);
        const { type, data } = payload;

        if (type === "connected") {
          if (data.status) readyText = data.status;
          if (data.locked) showLockScreen(data.voice_unlock_ready, data.voice_reason);
          else if (appIsLocked) hideLockScreen();
        } else if (type === "wake") {
          updateStatusBadge("Yes? I'm listening…", "listening");
        } else if (type === "voice_input") {
          if (data.chat_id === activeChatId) appendMessage("you", data.text);
          updateStatusBadge("Thinking…", "thinking");
        } else if (type === "voice_response") {
          if (data.chat_id === activeChatId || !data.chat_id) appendMessage("assistant", data.text, data.source_label);
          if (data.open_tab === "lablog" && window.VegaLabLog) window.VegaLabLog.open();
          refreshChatList();
        } else if (type === "notice") {
          appendNotice(data.text, data.level);
        } else if (type === "reminder") {
          appendNotice("⏰ Reminder: " + data.text, "warn");
        } else if (type === "telemetry") {
          const pill = document.getElementById("telemetry-pill");
          const pillText = document.getElementById("telemetry-pill-text");
          if (data) {
            const vramStr = data.gpu ? ` · VRAM ${Math.round(data.gpu.vram_used_mb / 1024 * 10) / 10}G` : "";
            const textStr = `CPU ${data.cpu_percent}% · RAM ${data.ram_used_gb}G${vramStr}`;
            if (pillText) pillText.textContent = textStr;
            else if (pill) pill.textContent = textStr;
          }
        } else if (type === "lablog_updated") {
          if (window.VegaLabLog && window.VegaLabLog.refresh) window.VegaLabLog.refresh();
        } else if (type === "status") {
          if (data.badge === "ready" && data.text) readyText = data.text;
          updateStatusBadge(data.text, data.badge);
        } else if (type === "lock") {
          showLockScreen(data.voice_unlock_ready, data.voice_reason);
        } else if (type === "unlock") {
          hideLockScreen();
        } else if (type === "lock_msg") {
          if (lockErrorMsg) lockErrorMsg.textContent = data.text || "";
        } else if (type === "listening") {
          updateListeningUI(data.on);
        } else if (type === "speaking") {
          updateSpeakingUI(data.on);
        } else if (type === "hotkey_mic") {
          toggleMic();
        } else if (type === "toggle_quickbar" || type === "focus_input") {
          if (window.VegaLabLog) window.VegaLabLog.close();
          promptInput.focus();
        } else if (type === "pair_request") {
          const ok = confirm(`${data.name} just messaged your Vega bot on Telegram.\n\nAllow this phone to control Vega?\nOnly press OK if it's YOU.`);
          fetch("/api/phone/allow", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ allow: ok }) })
            .then(r => r.json()).then(res => { if (ok && res.ok) appendNotice("Phone connected. You can message Vega on Telegram now."); });
        }
      } catch (err) {
        console.error("SSE parse error", err);
      }
    };

    evSource.onerror = () => {
      // Automatic reconnect built into EventSource
    };
  }

  connectEventStream();

  function updateStatusBadge(text, badgeClass) {
    statusBadgeText.textContent = text;
    statusBadge.className = `badge ${badgeClass || "ready"}`;
  }

  // A small grey line in the chat (reminders, "phone connected", warnings). Not saved in the chat.
  function appendNotice(text, level) {
    if (!text) return;
    welcomeScreen.style.display = "none";
    const div = document.createElement("div");
    div.className = "chat-notice " + (level === "warn" ? "warn" : "");
    div.textContent = text;
    messagesList.appendChild(div);
    scrollToBottom();
  }
  window.appendNotice = appendNotice;

  (function greet() {
    const h = new Date().getHours();
    const el = document.getElementById("welcome-heading");
    if (el) el.textContent = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  })();

  // Tell Vega you're using her (for auto-lock) and whether her window is in front (for "just talk" mode)
  let lastPing = 0;
  function pingActivity(extra) {
    const now = Date.now();
    if (!extra && now - lastPing < 30000) return;
    lastPing = now;
    fetch("/api/activity", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(extra || {}) }).catch(() => {});
  }
  ["mousemove", "keydown", "click"].forEach(ev => window.addEventListener(ev, () => pingActivity(), { passive: true }));
  window.addEventListener("focus", () => pingActivity({ focused: true }));
  window.addEventListener("blur", () => pingActivity({ focused: false }));
  document.addEventListener("visibilitychange", () => pingActivity({ focused: !document.hidden && document.hasFocus() }));
  setTimeout(() => pingActivity({ focused: document.hasFocus() }), 800);

  // System Status Check
  function checkSystemStatus() {
    fetch("/api/status")
      .then(r => r.json())
      .then(st => {
        if (st) {
          updateModeBadge(st.brain_mode);
          updateListeningUI(st.listening_on);
          if (st.voice_on !== undefined) updateSpeakingUI(st.voice_on);
        }
      })
      .catch(() => {});
  }

  function updateModeBadge(mode) {
    const isOnline = (mode || "").toLowerCase().includes("online");
    const isPrivate = (mode || "").toLowerCase().includes("private");
    modelBadge.className = `badge ${isPrivate ? "private" : (isOnline ? "online" : "offline")}`;
    modelBadgeText.textContent = mode || "Online (Gemini)";
  }

  function updateListeningUI(isOn) {
    listeningToggleBtn.className = `badge-toggle-btn ${isOn ? "on" : "off"}`;
    listeningText.textContent = isOn ? "Listening: ON" : "Listening: OFF";
    wakeToggle.checked = isOn;
  }

  function updateSpeakingUI(isOn) {
    if (speakingToggleBtn) {
      speakingToggleBtn.className = `badge-toggle-btn ${isOn ? "on" : "off"}`;
    }
    if (speakingText) {
      speakingText.textContent = isOn ? "Speak: ON" : "Speak: OFF";
    }
    if (voiceToggle) {
      voiceToggle.checked = !!isOn;
    }
  }

  listeningToggleBtn.addEventListener("click", () => {
    fetch("/api/settings/toggle_listening", { method: "POST" })
      .then(r => r.json())
      .then(res => {
        if (res && res.ok) {
          updateListeningUI(res.listening_on);
        }
      });
  });

  if (speakingToggleBtn) {
    speakingToggleBtn.addEventListener("click", () => {
      fetch("/api/settings/toggle_speaking", { method: "POST" })
        .then(r => r.json())
        .then(res => {
          if (res && res.ok) {
            updateSpeakingUI(res.voice_on);
          }
        })
        .catch(() => {});
    });
  }

  topQuickbarBtn.addEventListener("click", () => lockVegaNow());

  // New Chat
  newChatBtn.addEventListener("click", () => {
    fetch("/api/chat/new", { method: "POST" })
      .then(r => r.json())
      .then(chat => {
        loadChat(chat.id);
        refreshChatList();
      })
      .catch(() => {
        activeChatId = "chat_" + Date.now();
        showWelcome();
      });
  });

  // Pin Chat
  pinChatBtn.addEventListener("click", () => {
    if (!activeChatId) return;
    const isPinned = pinChatBtn.classList.contains("pinned");
    fetch("/api/chat/pin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: activeChatId, pinned: !isPinned })
    }).then(r => r.json()).then(res => {
      if (res && res.ok) {
        setPinState(res.pinned);
        refreshChatList();
      }
    });
  });

  function setPinState(pinned) {
    if (pinned) {
      pinChatBtn.classList.add("pinned");
      pinChatBtn.textContent = "★ Pinned";
    } else {
      pinChatBtn.classList.remove("pinned");
      pinChatBtn.textContent = "☆ Pin";
    }
  }

  // Rename Chat from Title Header
  currentChatTitle.addEventListener("click", () => {
    if (!activeChatId) return;
    const cur = currentChatTitle.textContent.trim();
    const newName = prompt("Rename conversation:", cur);
    if (newName && newName.trim()) {
      fetch("/api/chat/rename", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: activeChatId, title: newName.trim() })
      }).then(r => r.json()).then(res => {
        if (res && res.ok) {
          currentChatTitle.textContent = res.title;
          refreshChatList();
        }
      });
    }
  });

  // Stop Generation
  stopBtn.addEventListener("click", () => {
    if (abortController) abortController.abort();
    fetch("/api/stop", { method: "POST" });
    finishStreaming();
  });

  // Mic Button Click-to-Talk
  micBtn.addEventListener("click", toggleMic);

  function toggleMic() {
    if (appIsLocked) return;
    if (!isRecording) {
      startRecording();
    } else {
      if (transcriptionStatus) transcriptionStatus.textContent = "Transcribing locally… nothing has been sent.";
      fetch("/api/mic/stop", { method: "POST" });  // "I'm done talking"
    }
  }

  function startRecording() {
    transcriptReviewActive = false;
    isRecording = true;
    micBtn.classList.add("recording");
    micBtn.title = "Listening... speak now (click again when you're done)";
    updateStatusBadge("Listening… speak now", "listening");
    if (transcriptionStatus) transcriptionStatus.textContent = "Recording for offline Whisper… click the mic again to stop. Nothing is sent automatically.";

    fetch("/api/mic/start", { method: "POST" })
      .then(r => r.json())
      .then(res => {
        stopRecording();
        if (res && res.locked) return;
        if (res && res.ok && res.text) {
          promptInput.value = res.text;
          promptInput.dispatchEvent(new Event("input", { bubbles: true }));
          promptInput.focus();
          const language = res.language === "te" ? "Telugu / Tenglish" : (res.language === "en" ? "English" : "auto-detected language");
          transcriptReviewLanguage = language;
          transcriptReviewActive = true;
          if (transcriptionStatus) transcriptionStatus.textContent = `Transcribed locally · ${language} · ${res.model || "Whisper CPU int8"}. Review or correct it, then press Send.`;
        } else if (res && res.error) {
          if (transcriptionStatus) transcriptionStatus.textContent = "Offline transcription failed; no text was sent.";
          appendNotice("Microphone problem: " + res.error, "warn");
        } else {
          if (transcriptionStatus) transcriptionStatus.textContent = "No speech recognized; nothing was sent. Try again or type your message.";
          appendNotice("I didn't hear anything. Press the mic and try again, a bit closer.");
        }
      })
      .catch(() => {
        stopRecording();
        if (transcriptionStatus) transcriptionStatus.textContent = "Offline transcription request failed; no text was sent.";
      });
  }

  function stopRecording() {
    isRecording = false;
    micBtn.classList.remove("recording");
    micBtn.title = "Click to talk (or press Ctrl+Alt+V from anywhere)";
    updateStatusBadge(readyText, "ready");
  }

  // Multi-File & Screenshot Attachments
  function formatBytes(bytes) {
    if (!bytes || bytes <= 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
  }

  function addFiles(fileList) {
    if (!fileList || !fileList.length) return;
    for (const file of fileList) {
      const isImage = file.type.startsWith("image/") || /\.(png|jpe?g|webp|gif|bmp|svg)$/i.test(file.name);
      const isPdf = file.type === "application/pdf" || /\.pdf$/i.test(file.name);
      const item = {
        id: "att_" + Date.now() + "_" + Math.random().toString(36).slice(2, 7),
        name: file.name,
        size: file.size,
        isImage: isImage,
        isPdf: isPdf,
        mimeType: file.type || (isImage ? "image/png" : (isPdf ? "application/pdf" : "text/plain")),
        dataUrl: null,
        text: null,
        loaded: false
      };
      attachedItems.push(item);

      const reader = new FileReader();
      if (isImage || isPdf) {
        reader.onload = function(evt) {
          item.dataUrl = evt.target.result;
          item.loaded = true;
          renderAttachmentPills();
        };
        reader.readAsDataURL(file);
      } else {
        reader.onload = function(evt) {
          item.text = evt.target.result;
          item.loaded = true;
          renderAttachmentPills();
        };
        reader.readAsText(file);
      }
      reader.onerror = function() {
        item.loaded = true;
        item.error = "Could not read this file. Remove it and attach it again.";
        renderAttachmentPills();
      };
    }
    renderAttachmentPills();
  }

  function renderAttachmentPills() {
    if (!attachmentPreview || !attachmentPillsList) return;
    if (attachedItems.length === 0) {
      attachmentPreview.classList.add("hidden");
      if (clearAllAttachmentsBtn) clearAllAttachmentsBtn.classList.add("hidden");
      fileInput.value = "";
      return;
    }

    attachmentPreview.classList.remove("hidden");
    if (clearAllAttachmentsBtn) {
      clearAllAttachmentsBtn.classList.toggle("hidden", attachedItems.length < 2);
    }

    attachmentPillsList.innerHTML = "";
    attachedItems.forEach((item, idx) => {
      const pill = document.createElement("div");
      pill.className = "attachment-pill";

      if (item.isImage && item.dataUrl) {
        const thumb = document.createElement("img");
        thumb.className = "attachment-thumb";
        thumb.src = item.dataUrl;
        thumb.alt = item.name;
        pill.appendChild(thumb);
      } else {
        const icon = document.createElement("span");
        icon.className = "file-icon";
        icon.textContent = item.isPdf ? "PDF" : (item.isImage ? "IMG" : "FILE");
        pill.appendChild(icon);
      }

      const info = document.createElement("div");
      info.className = "pill-info";

      const nameSpan = document.createElement("span");
      nameSpan.className = "pill-name";
      nameSpan.textContent = item.name;
      nameSpan.title = item.name;
      info.appendChild(nameSpan);

      const sizeSpan = document.createElement("span");
      sizeSpan.className = "pill-size";
      sizeSpan.textContent = item.error || (item.loaded ? formatBytes(item.size) : `Reading ${formatBytes(item.size)}…`);
      info.appendChild(sizeSpan);

      pill.appendChild(info);

      const rmBtn = document.createElement("button");
      rmBtn.className = "remove-pill-btn";
      rmBtn.textContent = "✕";
      rmBtn.title = `Remove ${item.name}`;
      rmBtn.addEventListener("click", () => {
        attachedItems.splice(idx, 1);
        renderAttachmentPills();
      });
      pill.appendChild(rmBtn);

      attachmentPillsList.appendChild(pill);
    });
  }

  attachBtn.addEventListener("click", () => fileInput.click());

  fileInput.addEventListener("change", (e) => {
    if (e.target.files && e.target.files.length) {
      addFiles(Array.from(e.target.files));
    }
    fileInput.value = "";
  });

  if (clearAllAttachmentsBtn) {
    clearAllAttachmentsBtn.addEventListener("click", () => {
      attachedItems = [];
      renderAttachmentPills();
    });
  }

  // Paste multiple screenshots (Ctrl + V) from clipboard
  window.addEventListener("paste", (e) => {
    if (appIsLocked) return;
    const activeEl = document.activeElement;
    if (activeEl && (activeEl.tagName === "INPUT" && activeEl.type === "password" || activeEl.id === "pin-input")) return;
    const items = e.clipboardData && e.clipboardData.items;
    if (!items) return;

    let imageFiles = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.type.startsWith("image/")) {
        const file = item.getAsFile();
        if (file) {
          const num = attachedItems.filter(a => a.isImage).length + imageFiles.length + 1;
          const renamedFile = new File([file], `Screenshot ${num}.png`, { type: file.type || "image/png" });
          imageFiles.push(renamedFile);
        }
      }
    }
    if (imageFiles.length > 0) {
      e.preventDefault();
      addFiles(imageFiles);
    }
  });

  // Drag and drop multiple files / screenshots into input area
  const inputCard = document.querySelector(".input-card");
  if (inputContainer) {
    ["dragenter", "dragover"].forEach(evt => {
      inputContainer.addEventListener(evt, (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (inputCard) inputCard.classList.add("drag-over");
      });
    });
    ["dragleave", "drop"].forEach(evt => {
      inputContainer.addEventListener(evt, (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (inputCard) inputCard.classList.remove("drag-over");
      });
    });
    inputContainer.addEventListener("drop", (e) => {
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
        addFiles(Array.from(e.dataTransfer.files));
      }
    });
  }


  // ===================================================
  // GEMINI-STYLE MODEL SWITCHER (Chat Mode)
  // ===================================================
  const VEGA_MODELS = [
    {
      id: "auto",
      provider: "auto",
      model: "auto",
      label: "Auto",
      title: "Auto Router",
      desc: "Auto-selects fastest verified brain (Groq / Gemini / Ollama)"
    },
    {
      id: "groq-70b",
      provider: "groq",
      model: "llama-3.3-70b-versatile",
      label: "Groq 70B",
      title: "Groq Llama 3.3 70B",
      desc: "Ultra-fast free cloud inference · Complex reasoning & coding"
    },
    {
      id: "groq-120b",
      provider: "groq",
      model: "openai/gpt-oss-120b",
      label: "Groq 120B",
      title: "Groq GPT-OSS 120B",
      desc: "Deep knowledge & architecture · Open-weights flagship"
    },
    {
      id: "gemini-25-flash",
      provider: "gemini",
      model: "gemini-2.5-flash",
      label: "Gemini 2.5",
      title: "Gemini 2.5 Flash",
      desc: "Google AI flagship · High intelligence & multimodal speed"
    },
    {
      id: "gemini-flash-lite",
      provider: "gemini",
      model: "gemini-flash-latest",
      label: "Gemini Lite",
      title: "Gemini Flash Lite",
      desc: "Lightweight, instant conversational answers & low latency"
    },
    {
      id: "ollama-local",
      provider: "ollama",
      model: "qwen2.5:3b",
      label: "Local Qwen",
      title: "Ollama Local (Offline)",
      desc: "100% private on-device model · No internet required"
    },
    {
      id: "ollama-coder",
      provider: "ollama",
      model: "qwen2.5-coder:3b",
      label: "Local Coder",
      title: "Ollama Qwen Coder",
      desc: "Local code specialist · On-device programming assistance"
    }
  ];

  const chatModelBtn = document.getElementById("chat-model-picker-btn");
  const chatModelLabel = document.getElementById("chat-model-picker-label");
  const chatModelDropdown = document.getElementById("chat-model-dropdown");

  let activeChatModelId = localStorage.getItem("vega_chat_model_v2") || "auto";

  function getActiveChatModel() {
    return VEGA_MODELS.find(m => m.id === activeChatModelId) || VEGA_MODELS[0];
  }

  function renderChatModelDropdown() {
    if (!chatModelDropdown) return;
    const current = getActiveChatModel();
    if (chatModelLabel) chatModelLabel.textContent = current.label;

    let html = "";
    VEGA_MODELS.forEach(m => {
      const isSelected = m.id === current.id;
      html += `
        <div class="model-picker-item ${isSelected ? "selected" : ""}" data-chat-model-id="${m.id}" role="menuitem">
          <div class="model-picker-check">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
          </div>
          <div class="model-picker-text">
            <div class="model-picker-title">${m.title}</div>
            <div class="model-picker-desc">${m.desc}</div>
          </div>
        </div>
      `;
    });
    html += `
      <div class="model-picker-divider"></div>
      <div class="model-picker-footer" id="chat-model-manage-keys">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
        <span>Manage Keys & Local Models</span>
      </div>
    `;
    chatModelDropdown.innerHTML = html;

    chatModelDropdown.querySelectorAll(".model-picker-item").forEach(el => {
      el.addEventListener("click", () => {
        const mid = el.dataset.chatModelId;
        if (mid) {
          activeChatModelId = mid;
          localStorage.setItem("vega_chat_model_v2", mid);
          renderChatModelDropdown();
          closeChatModelDropdown();
        }
      });
    });

    const manageBtn = chatModelDropdown.querySelector("#chat-model-manage-keys");
    if (manageBtn) {
      manageBtn.addEventListener("click", () => {
        closeChatModelDropdown();
        if (settingsModal) {
          settingsModal.classList.remove("hidden");
          const keysTab = document.querySelector('[data-tab="keys"]');
          if (keysTab) keysTab.click();
        }
      });
    }
  }

  function toggleChatModelDropdown() {
    if (!chatModelDropdown) return;
    const isHidden = chatModelDropdown.classList.contains("hidden");
    if (isHidden) {
      renderChatModelDropdown();
      chatModelDropdown.classList.remove("hidden");
      if (chatModelBtn) {
        chatModelBtn.classList.add("active");
        chatModelBtn.setAttribute("aria-expanded", "true");
      }
    } else {
      closeChatModelDropdown();
    }
  }

  function closeChatModelDropdown() {
    if (!chatModelDropdown) return;
    chatModelDropdown.classList.add("hidden");
    if (chatModelBtn) {
      chatModelBtn.classList.remove("active");
      chatModelBtn.setAttribute("aria-expanded", "false");
    }
  }

  if (chatModelBtn) {
    chatModelBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleChatModelDropdown();
    });
  }

  document.addEventListener("click", (e) => {
    if (chatModelDropdown && !chatModelDropdown.classList.contains("hidden")) {
      if (!chatModelDropdown.contains(e.target) && !chatModelBtn.contains(e.target)) {
        closeChatModelDropdown();
      }
    }
  });

  renderChatModelDropdown();

  // Prompts Library Modal
  promptsBtn.addEventListener("click", () => promptsModal.classList.remove("hidden"));
  closePromptsModalBtn.addEventListener("click", () => promptsModal.classList.add("hidden"));
  promptsModal.addEventListener("click", (e) => {
    if (e.target === promptsModal) promptsModal.classList.add("hidden");
  });

  document.querySelectorAll(".prompts-grid .prompt-card").forEach(card => {
    card.addEventListener("click", () => {
      const pText = card.dataset.prompt;
      if (pText) {
        promptInput.value = pText;
        promptInput.focus();
        promptInput.style.height = "auto";
        promptInput.style.height = Math.min(promptInput.scrollHeight, 160) + "px";
        promptsModal.classList.add("hidden");
      }
    });
  });

  // Summaries Modal
  closeSummariesModalBtn.addEventListener("click", () => summariesModal.classList.add("hidden"));
  summariesModal.addEventListener("click", (e) => {
    if (e.target === summariesModal) summariesModal.classList.add("hidden");
  });

  document.getElementById("sum-action-news").addEventListener("click", () => {
    summariesModal.classList.add("hidden");
    handleSendWithText("Summarize today's top news headlines.");
  });

  document.getElementById("sum-action-tech").addEventListener("click", () => {
    summariesModal.classList.add("hidden");
    handleSendWithText("Summarize the latest tech news headlines.");
  });

  document.getElementById("sum-action-internships").addEventListener("click", () => {
    summariesModal.classList.add("hidden");
    handleSendWithText("Summarize the latest DevOps and Cloud internship opportunities.");
  });

  document.getElementById("sum-action-diary").addEventListener("click", () => {
    summariesModal.classList.add("hidden");
    handleSendWithText("Summarize what I wrote in my diary today.");
  });

  // 4 Starter Cards
  document.getElementById("card-briefing").addEventListener("click", () => {
    handleSendWithText("Good morning! Give me the morning briefing.");
  });
  document.getElementById("card-python").addEventListener("click", () => {
    handleSendWithText("Write a Python script to monitor CPU and GPU memory usage on Windows.");
  });
  document.getElementById("card-tenglish").addEventListener("click", () => {
    handleSendWithText("Nuvvu ela unnav vega? Cloud computing basics telugu lo explain cheyyi.");
  });
  document.getElementById("card-lablog").addEventListener("click", () => window.VegaLabLog.open());

  // Sidebar Daily Items
  const dailyHubModal = document.getElementById("daily-hub-modal");
  const openAppsHubBtn = document.getElementById("open-apps-hub-btn");
  const railAppsBtn = document.getElementById("rail-apps-btn");
  const closeDailyHubBtn = document.getElementById("close-daily-hub-btn");

  const showDailyHub = () => {
    if (dailyHubModal) dailyHubModal.classList.remove("hidden");
  };
  const hideDailyHub = () => {
    if (dailyHubModal) dailyHubModal.classList.add("hidden");
  };

  if (openAppsHubBtn) openAppsHubBtn.addEventListener("click", showDailyHub);
  if (railAppsBtn) railAppsBtn.addEventListener("click", showDailyHub);
  if (closeDailyHubBtn) closeDailyHubBtn.addEventListener("click", hideDailyHub);
  if (dailyHubModal) {
    dailyHubModal.addEventListener("click", (e) => {
      if (e.target === dailyHubModal) hideDailyHub();
    });
  }

  // Quick Settings Popover Toggle
  const quickSettingsBtn = document.getElementById("quick-settings-btn");
  const quickSettingsPopover = document.getElementById("quick-settings-popover");

  if (quickSettingsBtn && quickSettingsPopover) {
    quickSettingsBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      quickSettingsPopover.classList.toggle("hidden");
    });
    document.addEventListener("click", (e) => {
      if (!quickSettingsPopover.contains(e.target) && e.target !== quickSettingsBtn) {
        quickSettingsPopover.classList.add("hidden");
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        quickSettingsPopover.classList.add("hidden");
        hideDailyHub();
      }
    });
  }

  // Expand sidebar when search box is clicked in collapsed rail state
  if (chatSearch && chatSearch.parentElement) {
    chatSearch.parentElement.addEventListener("click", () => {
      if (sidebar && sidebar.classList.contains("collapsed")) {
        sidebar.classList.remove("collapsed");
        setTimeout(() => chatSearch.focus(), 150);
      }
    });
  }

  const dailyLablog = document.getElementById("daily-lablog");
  if (dailyLablog) dailyLablog.addEventListener("click", () => { hideDailyHub(); window.VegaLabLog.open(); });

  const dailyNews = document.getElementById("daily-news");
  if (dailyNews) dailyNews.addEventListener("click", () => {
    hideDailyHub();
    handleSendWithText("What are the top news headlines today?");
  });

  const dailyTech = document.getElementById("daily-tech");
  if (dailyTech) dailyTech.addEventListener("click", () => {
    hideDailyHub();
    openRightPanelTab("tech");
    loadTechNews();
  });

  const dailyInternships = document.getElementById("daily-internships");
  if (dailyInternships) dailyInternships.addEventListener("click", () => {
    hideDailyHub();
    openRightPanelTab("internships");
    loadInternships();
  });

  const dailyWeather = document.getElementById("daily-weather");
  if (dailyWeather) dailyWeather.addEventListener("click", () => {
    hideDailyHub();
    handleSendWithText("What's the weather in Chennai?");
  });

  const dailyTasks = document.getElementById("daily-tasks");
  if (dailyTasks) dailyTasks.addEventListener("click", () => {
    hideDailyHub();
    handleSendWithText("Show my upcoming tasks.");
  });

  const dailyReminders = document.getElementById("daily-reminders");
  if (dailyReminders) dailyReminders.addEventListener("click", () => {
    hideDailyHub();
    handleSendWithText("Show my reminders.");
  });

  const dailyDiary = document.getElementById("daily-diary");
  if (dailyDiary) dailyDiary.addEventListener("click", () => {
    hideDailyHub();
    handleSendWithText("Open my diary.");
  });

  const dailySummaries = document.getElementById("daily-summaries");
  if (dailySummaries) dailySummaries.addEventListener("click", () => {
    hideDailyHub();
    summariesModal.classList.remove("hidden");
  });

  // Workspace Mode Switching (Chat | Agent | Practice Lab)
  let currentAppMode = "chat";
  function setAppMode(mode) {
    currentAppMode = mode;
    const lablogPage = document.getElementById("lablog-page");
    if (lablogPage) lablogPage.classList.add("hidden");
    const isChat = mode === "chat";
    const isAgent = mode === "agent";
    const isPractice = mode === "practice";
    const isHud = mode === "hud";

    if (modeChatTab) {
      modeChatTab.classList.toggle("active", isChat);
      modeChatTab.setAttribute("aria-selected", String(isChat));
    }
    if (modeAgentTab) {
      modeAgentTab.classList.toggle("active", isAgent);
      modeAgentTab.setAttribute("aria-selected", String(isAgent));
    }
    if (modePracticeTab) {
      modePracticeTab.classList.toggle("active", isPractice);
      modePracticeTab.setAttribute("aria-selected", String(isPractice));
    }
    if (modeHudTab) {
      modeHudTab.classList.toggle("active", isHud);
      modeHudTab.setAttribute("aria-selected", String(isHud));
    }

    if (messagesContainer) messagesContainer.classList.toggle("hidden", !isChat);
    if (inputContainer) inputContainer.classList.toggle("hidden", !isChat);
    if (agentWorkspace) agentWorkspace.classList.toggle("hidden", !isAgent);
    if (agentMessagesContainer) agentMessagesContainer.classList.toggle("hidden", !isAgent);
    if (agentComposer) agentComposer.classList.toggle("hidden", !isAgent);
    if (agentTogglePanelBtn) agentTogglePanelBtn.classList.toggle("hidden", !isAgent);
    if (!isAgent && agentSidePanel) agentSidePanel.classList.add("hidden");
    if (practiceWorkspace) practiceWorkspace.classList.toggle("hidden", !isPractice);
    if (window.arcReactorHUD) window.arcReactorHUD.toggleHUD(isHud);

    // Hide Daily Hub and Chat-specific header controls in Agent and Practice modes
    if (!isChat) {
      if (rightPanel) rightPanel.classList.add("collapsed");
      if (toggleRightPanelBtn) toggleRightPanelBtn.classList.add("hidden");
      if (currentChatTitle) currentChatTitle.classList.add("hidden");
      if (pinChatBtn) pinChatBtn.classList.add("hidden");
      if (modelBadge) modelBadge.classList.add("hidden");
    } else {
      if (toggleRightPanelBtn) toggleRightPanelBtn.classList.remove("hidden");
      if (currentChatTitle) currentChatTitle.classList.remove("hidden");
      if (pinChatBtn) pinChatBtn.classList.remove("hidden");
      if (modelBadge) modelBadge.classList.remove("hidden");
    }

    if (isAgent && window.VegaAgentLab) {
      if (window.VegaAgentLab.onActivated) window.VegaAgentLab.onActivated();
      if (window.VegaAgentLab.refresh) window.VegaAgentLab.refresh();
    }
    if (isPractice && window.VegaPracticeLab) {
      if (window.VegaPracticeLab.onActivated) window.VegaPracticeLab.onActivated();
    }
  }
  window.setAppMode = setAppMode;
  window.getAppMode = () => currentAppMode;

  if (modeChatTab) modeChatTab.addEventListener("click", () => setAppMode("chat"));
  if (modeAgentTab) modeAgentTab.addEventListener("click", () => setAppMode("agent"));
  if (modePracticeTab) modePracticeTab.addEventListener("click", () => setAppMode("practice"));
  if (modeHudTab) modeHudTab.addEventListener("click", () => setAppMode("hud"));

  const practiceNavChat = document.getElementById("practice-nav-chat");
  if (practiceNavChat) practiceNavChat.addEventListener("click", () => setAppMode("chat"));
  const practiceNavAgent = document.getElementById("practice-nav-agent");
  if (practiceNavAgent) practiceNavAgent.addEventListener("click", () => setAppMode("agent"));

  const dailyAgentTasks = document.getElementById("daily-agent-tasks");
  if (dailyAgentTasks) {
    dailyAgentTasks.addEventListener("click", () => setAppMode("agent"));
  }
  const dailyPracticeLab = document.getElementById("daily-practice-lab");
  if (dailyPracticeLab) {
    dailyPracticeLab.addEventListener("click", () => setAppMode("practice"));
  }
  const settingsOpenAgents = document.getElementById("settings-open-agents");
  if (settingsOpenAgents) {
    settingsOpenAgents.addEventListener("click", () => {
      settingsModal.classList.add("hidden");
      setAppMode("agent");
    });
  }

  // Project Creation
  document.getElementById("add-project-btn").addEventListener("click", () => {
    const name = prompt("Enter project name (e.g., Cloud DevOps, Assignments):");
    if (!name || !name.trim()) return;
    const notes = prompt("Project context or notes for Vega (optional):") || "";
    fetch("/api/project/new", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim(), notes: notes.trim() })
    }).then(r => r.json()).then(res => {
      if (res && res.ok) {
        loadProjects();
      }
    });
  });

  function loadProjects() {
    fetch("/api/projects")
      .then(r => r.json())
      .then(projs => {
        const container = document.getElementById("projects-list-container");
        container.innerHTML = "";
        if (!Array.isArray(projs)) {
          container.textContent = projs && projs.locked
            ? "Unlock Vega to view projects."
            : "Projects are temporarily unavailable.";
          return;
        }
        if (projs.length === 0) {
          container.innerHTML = `<div style="padding:6px 10px; color:var(--text-muted); font-size:12px;">No custom projects</div>`;
          return;
        }
        projs.forEach(p => {
          const item = document.createElement("div");
          item.className = "daily-item";
          item.innerHTML = `
            <span class="daily-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M3 5h7l2 2h9v13H3zM3 9h18"/></svg></span>
            <span style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${p.name}</span>
          `;
          item.onclick = () => {
            handleSendWithText(`In our project '${p.name}', let's work on our tasks.`);
          };
          container.appendChild(item);
        });
      })
      .catch(() => {
        const container = document.getElementById("projects-list-container");
        if (container) container.textContent = "Projects are temporarily unavailable.";
      });
  }

  loadProjects();

  function handleSendWithText(t) {
    if (window.VegaLabLog) window.VegaLabLog.close();
    setAppMode("chat");
    promptInput.value = t;
    handleSend();
  }
  window.handleSendWithText = handleSendWithText;

  // Send Message Logic with SSE Streaming
  async function handleSend(spoken) {
    let text = promptInput.value.trim();
    if ((!text && attachedItems.length === 0) || isGenerating) return;
    if (appIsLocked) { showLockScreen(); return; }

    const currentAttachments = [...attachedItems];
    if (currentAttachments.some(item => !item.loaded || item.error)) {
      window.appendNotice("Wait for attached files to finish reading. Remove and reattach any file that failed to read.", "warn");
      return;
    }
    attachedItems = [];
    renderAttachmentPills();

    // If user attached files/screenshots but didn't type text, provide a sensible default prompt
    if (!text) {
      const imgCount = currentAttachments.filter(a => a.isImage).length;
      const fileCount = currentAttachments.length - imgCount;
      if (imgCount > 0 && fileCount === 0) {
        text = imgCount === 1 ? "Please look at this screenshot and explain what it shows / help me." : `Please look at these ${imgCount} screenshots and explain what they show / help me.`;
      } else if (fileCount > 0 && imgCount === 0) {
        text = fileCount === 1 ? "Please review this attached file." : `Please review these ${fileCount} attached files.`;
      } else {
        text = "Please review the attached screenshots and files.";
      }
    }

    let imagesForBackend = [];
    let fileTextBlocks = [];
    let shownBadges = [];

    for (const att of currentAttachments) {
      if ((att.isImage || att.isPdf) && att.dataUrl) {
        imagesForBackend.push({
          name: att.name,
          mime_type: att.mimeType,
          data: att.dataUrl
        });
        shownBadges.push(`${att.isImage ? "Image" : "PDF"} · ${att.name}`);
      } else if (att.text) {
        fileTextBlocks.push(`--- File: ${att.name} ---\n${att.text}\n-----------------------`);
        shownBadges.push(`File · ${att.name}`);
      }
    }

    let fullPrompt = text;
    if (fileTextBlocks.length > 0) {
      fullPrompt = fileTextBlocks.join("\n\n") + "\n\n" + text;
    }

    let shown = text;
    if (shownBadges.length > 0) {
      shown = shownBadges.join("  ") + "\n" + text;
    }

    promptInput.value = "";
    transcriptReviewActive = false;
    promptInput.style.height = "auto";
    welcomeScreen.style.display = "none";

    const youRow = appendMessage("you", shown, null, null, null, currentAttachments);

    isGenerating = true;
    updateStatusBadge("Thinking...", "thinking");
    stopContainer.classList.remove("hidden");

    currentStreamingElement = createAssistantBubble();
    messagesList.appendChild(currentStreamingElement);
    scrollToBottom();

    abortController = new AbortController();

    try {
      const response = await fetch("/api/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: fullPrompt,
          shown: shown,
          chat_id: activeChatId,
          spoken: spoken === true,
          images: imagesForBackend,
          provider: getActiveChatModel().provider,
          model: getActiveChatModel().model
        }),
        signal: abortController.signal
      });

      if (!response.ok) throw new Error("HTTP error " + response.status);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let fullText = "";
      let sourceLabel = "Vega";
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop();  // a half-received event waits for the next piece

        for (const ev of events) {
          const line = ev.split("\n").find(l => l.startsWith("data: "));
          if (!line) continue;
          let data;
          try { data = JSON.parse(line.substring(6)); } catch (e) { continue; }
          if (data.token) {
            fullText = data.full || fullText;
            sourceLabel = data.source_label || sourceLabel;
            updateStreamingBubble(fullText, sourceLabel);
          } else if (data.done) {
            fullText = data.text || fullText;
            sourceLabel = data.source_label || sourceLabel;
            if (data.nosave && youRow && /^[A-Za-z0-9]{4,12}$/.test(text)) youRow.remove();  // never show a PIN
            finalizeAssistantBubble(fullText, sourceLabel);
            if (data.open_tab === "lablog" && window.VegaLabLog) window.VegaLabLog.open();
          }
        }
      }

      finalizeAssistantBubble(fullText, sourceLabel);
      refreshChatList();
      checkSystemStatus();
    } catch (err) {
      if (err.name !== "AbortError") {
        finalizeAssistantBubble("Error contacting Vega: " + err.message, "Offline");
      }
    } finally {
      finishStreaming();
      updateStatusBadge(readyText, "ready");
    }
  }

  // Appending UI Messages
  function appendMessage(who, text, sourceLabel = null, model = null, timestamp = null, attachments = null) {
    welcomeScreen.style.display = "none";
    const row = document.createElement("div");
    row.className = `message-row ${who === "you" ? "user" : "assistant"}`;

    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.textContent = who === "you" ? "H" : "V";

    const wrapper = document.createElement("div");
    wrapper.className = "message-content-wrapper";

    const meta = document.createElement("div");
    meta.className = "message-meta";
    const timeStr = timestamp || new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    if (who === "you") {
      meta.innerHTML = `<span>${timeStr}</span> <strong>You</strong>`;
    } else {
      const tagClass = (sourceLabel || "").toLowerCase() === "online" ? "online" : "offline";
      const tagText = sourceLabel ? `<span class="message-source-tag ${tagClass}">${sourceLabel}</span>` : "";
      meta.innerHTML = `<strong>Vega</strong> ${tagText} <span>${timeStr}</span>`;
    }

    const body = document.createElement("div");
    body.className = "message-body";
    if (who === "you") {
      if (attachments && attachments.length > 0) {
        const imgAtts = attachments.filter(a => a.isImage && a.dataUrl);
        if (imgAtts.length > 0) {
          const gallery = document.createElement("div");
          gallery.className = "msg-attachments-gallery";
          imgAtts.forEach(a => {
            const img = document.createElement("img");
            img.className = "msg-attachment-img";
            img.src = a.dataUrl;
            img.alt = a.name;
            img.title = `${a.name} (Click to open full image)`;
            img.addEventListener("click", () => window.open(a.dataUrl, "_blank"));
            gallery.appendChild(img);
          });
          body.appendChild(gallery);
        }

        const fileAtts = attachments.filter(a => !a.isImage);
        if (fileAtts.length > 0) {
          const filesRow = document.createElement("div");
          filesRow.className = "msg-files-badges";
          fileAtts.forEach(a => {
            const badge = document.createElement("div");
            badge.className = "msg-file-badge";
            badge.innerHTML = `<span class="file-type-icon">${a.isPdf ? "PDF" : "FILE"}</span> <span>${escapeHtml(a.name)}</span> <span style="opacity:0.6; font-size:10px;">(${formatBytes(a.size)})</span>`;
            filesRow.appendChild(badge);
          });
          body.appendChild(filesRow);
        }
      }
      const textDiv = document.createElement("div");
      textDiv.textContent = text;
      body.appendChild(textDiv);
    } else {
      body.innerHTML = formatMarkdown(text);
      attachCodeCopyButtons(body);
    }

    wrapper.appendChild(meta);
    wrapper.appendChild(body);

    // Message Actions
    const actions = document.createElement("div");
    actions.className = "message-actions";

    if (who === "you") {
      const editBtn = document.createElement("button");
      editBtn.className = "msg-action-btn";
      editBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m4 16-.8 4.8L8 20l11-11-4-4L4 16Z"/><path d="m13.5 6.5 4 4"/></svg> Edit`;
      editBtn.onclick = () => {
        promptInput.value = text;
        promptInput.focus();
      };
      actions.appendChild(editBtn);
    } else {
      const copyBtn = document.createElement("button");
      copyBtn.className = "msg-action-btn";
      copyBtn.innerHTML = `Copy`;
      copyBtn.onclick = () => {
        navigator.clipboard.writeText(text);
        copyBtn.textContent = "✓ Copied";
        setTimeout(() => copyBtn.innerHTML = `Copy`, 1500);
      };

      const regenBtn = document.createElement("button");
      regenBtn.className = "msg-action-btn";
      regenBtn.innerHTML = `Try again`;
      regenBtn.onclick = () => {
        fetch("/api/regenerate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ chat_id: activeChatId })
        }).then(r => r.json()).then(res => {
          if (res && res.ok && res.prompt) {
            handleSendWithText(res.prompt);
          }
        });
      };

      actions.appendChild(copyBtn);
      actions.appendChild(regenBtn);
    }
    wrapper.appendChild(actions);

    row.appendChild(avatar);
    row.appendChild(wrapper);
    messagesList.appendChild(row);
    scrollToBottom();
    return row;
  }

  function createAssistantBubble() {
    const row = document.createElement("div");
    row.className = "message-row assistant";

    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.textContent = "V";

    const wrapper = document.createElement("div");
    wrapper.className = "message-content-wrapper";

    const meta = document.createElement("div");
    meta.className = "message-meta";
    meta.innerHTML = `<strong>Vega</strong> <span class="message-source-tag online">Thinking...</span>`;

    const body = document.createElement("div");
    body.className = "message-body";
    body.innerHTML = `<span class="streaming-cursor"></span>`;

    wrapper.appendChild(meta);
    wrapper.appendChild(body);
    row.appendChild(avatar);
    row.appendChild(wrapper);
    return row;
  }

  function updateStreamingBubble(fullText, sourceLabel) {
    if (!currentStreamingElement) return;
    const body = currentStreamingElement.querySelector(".message-body");
    const meta = currentStreamingElement.querySelector(".message-meta");
    const tagClass = (sourceLabel || "").toLowerCase() === "online" ? "online" : "offline";
    meta.innerHTML = `<strong>Vega</strong> <span class="message-source-tag ${tagClass}">${sourceLabel}</span> <span>Streaming...</span>`;
    body.innerHTML = formatMarkdown(fullText) + '<span class="streaming-cursor"></span>';
    attachCodeCopyButtons(body);
    scrollToBottom();
  }

  function finalizeAssistantBubble(fullText, sourceLabel) {
    if (!currentStreamingElement) return;
    const body = currentStreamingElement.querySelector(".message-body");
    const meta = currentStreamingElement.querySelector(".message-meta");
    const tagClass = (sourceLabel || "").toLowerCase() === "online" ? "online" : "offline";
    meta.innerHTML = `<strong>Vega</strong> <span class="message-source-tag ${tagClass}">${sourceLabel}</span> <span>${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>`;
    body.innerHTML = formatMarkdown(fullText);
    attachCodeCopyButtons(body);
    scrollToBottom();
  }

  function finishStreaming() {
    isGenerating = false;
    stopContainer.classList.add("hidden");
    if (currentStreamingElement) {
      const cursor = currentStreamingElement.querySelector(".streaming-cursor");
      if (cursor) cursor.remove();
      currentStreamingElement = null;
    }
  }

  function escapeHtml(t) {
    return String(t).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }
  window.escapeHtml = escapeHtml;

  function isSafeMarkdownUrl(href) {
    const value = String(href || "").trim();
    const compact = value.replace(/[\u0000-\u0020]+/g, "").toLowerCase();
    const scheme = compact.match(/^([a-z][a-z0-9+.-]*):/);
    return !scheme || ["http", "https", "mailto"].includes(scheme[1]);
  }

  function formatMarkdown(text) {
    if (window.marked) return marked.parse(text);
    return escapeHtml(text).replace(/\n/g, "<br>");
  }

  function attachCodeCopyButtons(container) {
    container.querySelectorAll("pre code").forEach((codeEl) => {
      const pre = codeEl.parentElement;
      if (pre.parentElement.classList.contains("code-block-container")) return;

      const codeContainer = document.createElement("div");
      codeContainer.className = "code-block-container";

      const header = document.createElement("div");
      header.className = "code-header";

      const lang = (codeEl.className.match(/language-(\w+)/) || [])[1] || "code";
      header.innerHTML = `<span>${lang}</span>`;

      const copyBtn = document.createElement("button");
      copyBtn.className = "copy-btn";
      copyBtn.innerHTML = `Copy code`;
      copyBtn.onclick = () => {
        navigator.clipboard.writeText(codeEl.innerText);
        copyBtn.textContent = "✓ Copied!";
        setTimeout(() => copyBtn.innerHTML = `Copy code`, 1800);
      };

      header.appendChild(copyBtn);
      pre.parentNode.insertBefore(codeContainer, pre);
      codeContainer.appendChild(header);
      codeContainer.appendChild(pre);
    });
  }

  function scrollToBottom() {
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }

  // Load Chat History
  function loadChat(chatId) {
    if (window.VegaLabLog) window.VegaLabLog.close();
    activeChatId = chatId;
    messagesList.innerHTML = "";
    fetch("/api/chat/" + chatId)
      .then(r => r.json())
      .then(chat => {
        if (!chat || !chat.messages || chat.messages.length === 0) {
          showWelcome();
          currentChatTitle.textContent = "New Conversation";
          setPinState(false);
        } else {
          welcomeScreen.style.display = "none";
          currentChatTitle.textContent = chat.title || "Conversation";
          setPinState(!!chat.pinned);
          chat.messages.forEach((m) => {
            const t = m.t ? new Date(m.t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : null;
            appendMessage(m.who === "vega" ? "assistant" : "you", (m.who === "phone" ? "Phone: " : "") + (m.text || ""), m.source_label || null, m.model, t);
          });
        }
      })
      .catch(() => showWelcome());
  }

  function showWelcome() {
    messagesList.innerHTML = "";
    welcomeScreen.style.display = "block";
    currentChatTitle.textContent = "New Conversation";
    setPinState(false);
  }

  // Refresh Sidebar Chat List
  function refreshChatList() {
    fetch("/api/chats")
      .then(r => r.json())
      .then(chats => {
        allChats = chats || [];
        renderCurrentChatList();
      })
      .catch(() => {});
  }

  function renderChatList(chats) {
    chatHistoryContainer.innerHTML = "";
    if (!chats || chats.length === 0) {
      const empty = document.createElement("div");
      empty.style.cssText = "padding:16px 10px; color:var(--text-muted); font-size:12px;";
      empty.textContent = chatSearch.value.trim() ? "No matching chats" : "No chats yet";
      chatHistoryContainer.appendChild(empty);
      return;
    }

    const pinned = [];
    const today = [];
    const yesterday = [];
    const older = [];

    const now = new Date();
    chats.forEach((c) => {
      if (c.pinned) {
        pinned.push(c);
        return;
      }
      const d = (c.updated || c.updated_at) ? new Date(c.updated || c.updated_at) : new Date();
      const diffHours = (now - d) / (1000 * 60 * 60);
      if (diffHours < 24) today.push(c);
      else if (diffHours < 48) yesterday.push(c);
      else older.push(c);
    });

    if (pinned.length) appendChatGroup("Pinned", pinned);
    if (today.length) appendChatGroup("Today", today);
    if (yesterday.length) appendChatGroup("Yesterday", yesterday);
    if (older.length) appendChatGroup("Older", older);
  }

  function appendChatGroup(title, items) {
    const grp = document.createElement("div");
    grp.className = "chat-group-title";
    grp.textContent = title;
    chatHistoryContainer.appendChild(grp);

    items.forEach((c) => {
      const item = document.createElement("div");
      item.className = `chat-item ${c.id === activeChatId ? "active" : ""}`;
      item.innerHTML = `
        <span class="chat-item-title">${c.pinned ? "★ " : ""}${escapeHtml(c.title || "Untitled")}</span>
        <div class="chat-item-actions">
          <button class="chat-action-btn edit" title="Rename" aria-label="Rename"><svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m4 16-.8 4.8L8 20l11-11-4-4L4 16Z"/><path d="m13.5 6.5 4 4"/></svg></button>
          <button class="chat-action-btn del" title="Delete" aria-label="Delete"><svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4h8v2m3 0-1 14H6L5 6m4 4v6m6-6v6"/></svg></button>
        </div>
      `;

      item.querySelector(".chat-item-title").onclick = () => loadChat(c.id);

      item.querySelector(".edit").onclick = (e) => {
        e.stopPropagation();
        const newTitle = prompt("Rename chat:", c.title);
        if (newTitle) {
          fetch("/api/chat/rename", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: c.id, title: newTitle })
          }).then(refreshChatList);
        }
      };

      item.querySelector(".del").onclick = (e) => {
        e.stopPropagation();
        if (confirm("Delete this chat?")) {
          fetch("/api/chat/delete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: c.id })
          }).then(() => {
            if (activeChatId === c.id) showWelcome();
            refreshChatList();
          });
        }
      };

      chatHistoryContainer.appendChild(item);
    });
  }

  function renderCurrentChatList() {
    const q = chatSearch.value.trim().toLowerCase();
    renderChatList(q ? allChats.filter(c => (c.title || "").toLowerCase().includes(q)) : allChats);
  }

  // Search Filter
  chatSearch.addEventListener("input", renderCurrentChatList);

  // UI Toggles (Sidebar & Right Panel)
  toggleSidebarBtn.addEventListener("click", () => sidebar.classList.toggle("collapsed"));
  toggleRightPanelBtn.addEventListener("click", () => rightPanel.classList.toggle("collapsed"));
  closePanelBtn.addEventListener("click", () => rightPanel.classList.add("collapsed"));

  function openRightPanelTab(tabName) {
    rightPanel.classList.remove("collapsed");
    document.querySelectorAll(".panel-tabs .tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-pane").forEach((p) => p.classList.remove("active"));
    const btn = document.querySelector(`.panel-tabs .tab-btn[data-tab="${tabName}"]`);
    if (btn) btn.classList.add("active");
    const pane = document.getElementById("tab-" + tabName);
    if (pane) pane.classList.add("active");
  }

  // Right Panel Tab switching
  document.querySelectorAll(".panel-tabs .tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      openRightPanelTab(btn.dataset.tab);
      if (btn.dataset.tab === "lablog") loadLabLog();
      else if (btn.dataset.tab === "tech") loadTechNews();
      else if (btn.dataset.tab === "internships") loadInternships();
    });
  });

  // Settings Modal
  settingsBtn.addEventListener("click", () => settingsModal.classList.remove("hidden"));
  closeSettingsBtn.addEventListener("click", () => settingsModal.classList.add("hidden"));
  settingsModal.addEventListener("click", (e) => {
    if (e.target === settingsModal) settingsModal.classList.add("hidden");
  });

  // Brain Mode Switcher
  document.querySelectorAll(".segmented-control .seg-btn").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll(".segmented-control .seg-btn").forEach((btn) => btn.classList.remove("active"));
      b.classList.add("active");
      const mode = b.dataset.mode;
      updateModeBadge(mode);
      fetch("/api/settings/mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: mode })
      }).then(() => checkSystemStatus());
    });
  });

  // Voice & Wake Toggles
  if (voiceToggle) {
    voiceToggle.addEventListener("change", () => {
      fetch("/api/settings/update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: "voice_on", value: voiceToggle.checked })
      })
      .then(r => r.json())
      .then(res => {
        if (res && res.ok) updateSpeakingUI(voiceToggle.checked);
      })
      .catch(() => {});
    });
  }

  wakeToggle.addEventListener("change", () => {
    fetch("/api/settings/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: "wake_word_on", value: wakeToggle.checked })
    }).then(() => checkSystemStatus());
  });

  cloudSpeechToggle.addEventListener("change", () => {
    cloudSpeechToggle.checked = false;
  });

  if (speechLanguageSelect) {
    speechLanguageSelect.addEventListener("change", () => {
      fetch("/api/settings/update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: "voice_language", value: speechLanguageSelect.value })
      }).then(r => r.json()).then(res => {
        if (transcriptionStatus) transcriptionStatus.textContent = res && res.ok
          ? `Offline Whisper language: ${speechLanguageSelect.options[speechLanguageSelect.selectedIndex].text} · ${res.speech_model || "local model"}${res.speech_model_ready ? " ready" : " loading from cache"}. Review transcripts before sending.`
          : "Couldn't change the local speech language.";
      }).catch(() => {
        if (transcriptionStatus) transcriptionStatus.textContent = "Couldn't change the local speech language.";
      });
    });
  }

  if (speechModelSelect) {
    speechModelSelect.addEventListener("change", () => {
      fetch("/api/settings/update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: "telugu_model", value: speechModelSelect.value })
      }).then(r => r.json()).then(res => {
        if (transcriptionStatus) transcriptionStatus.textContent = res && res.ok
          ? `Offline multilingual model: ${speechModelSelect.options[speechModelSelect.selectedIndex].text} · ${res.speech_model || "local model"}${res.speech_model_ready ? " ready" : " loading from cache"}. No download or cloud service is used.`
          : (res && res.error) || "Couldn't change the local speech model.";
      }).catch(() => {
        if (transcriptionStatus) transcriptionStatus.textContent = "Couldn't change the local speech model.";
      });
    });
  }

  speedSlider.addEventListener("input", (e) => {
    speedVal.textContent = e.target.value + "x";
    fetch("/api/settings/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: "speech_rate", value: parseFloat(e.target.value) })
    });
  });

  startupToggle.addEventListener("change", () => {
    fetch("/api/settings/startup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: startupToggle.checked })
    });
  });

  // Settings Shortcuts & Folders
  document.getElementById("create-desktop-shortcut-btn").addEventListener("click", () => {
    fetch("/api/settings/desktop_shortcut", { method: "POST" })
      .then(r => r.json())
      .then(res => alert(res.ok ? "Desktop shortcut created!" : "Could not create shortcut."));
  });

  document.getElementById("open-diary-folder-btn").addEventListener("click", () => {
    fetch("/api/open_folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder: "diary" })
    });
  });

  document.getElementById("open-docs-folder-btn").addEventListener("click", () => {
    fetch("/api/open_folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder: "docs" })
    });
  });


  // Self-Test Button
  const runSelftestBtn = document.getElementById("run-selftest-btn");
  if (runSelftestBtn) {
    runSelftestBtn.addEventListener("click", () => {
      const resultsDiv = document.getElementById("selftest-results");
      runSelftestBtn.textContent = "Running...";
      runSelftestBtn.disabled = true;
      resultsDiv.style.display = "block";
      resultsDiv.textContent = "Running self-test...";
      fetch("/api/selftest")
        .then(r => r.json())
        .then(data => {
          runSelftestBtn.textContent = "Run Self-Test";
          runSelftestBtn.disabled = false;
          if (!data || !data.results) {
            resultsDiv.textContent = "ERROR: No results returned";
            return;
          }
          let html = "";
          let pass = 0, fail = 0;
          data.results.forEach(r => {
            const ok = r.status === "PASS";
            if (ok) pass++; else fail++;
            const color = ok ? "#4ade80" : "#f87171";
            const err = r.error ? " - " + r.error : "";
            html += "<div style=\"color:" + color + "\">" + (ok ? "PASS" : "FAIL") + ": " + r.name + err + "</div>";
          });
          html += "<div style=\"margin-top:6px;color:#888\">Result: " + pass + "/" + (pass+fail) + " passed. Red ones need a look (Hey Vega, Gemini, Ollama, phone).</div>";
          resultsDiv.innerHTML = html;
        })
        .catch(err => {
          runSelftestBtn.textContent = "Run Self-Test";
          runSelftestBtn.disabled = false;
          resultsDiv.textContent = "ERROR: " + err.message;
        });
    });
  }

  // ==========================================
  // LOCK SCREEN, VOICE LOCK & MIC DIAGNOSTIC
  // ==========================================
  const lockScreenModal = document.getElementById("lock-screen-modal");
  const lockPinInput = document.getElementById("lock-pin-input");
  const lockUnlockBtn = document.getElementById("lock-unlock-btn");
  const lockVoiceBtn = document.getElementById("lock-voice-btn");
  const lockVoiceHint = document.getElementById("lock-voice-hint");
  const lockErrorMsg = document.getElementById("lock-error-msg");
  const lockVegaBtn = document.getElementById("lock-vega-btn");
  const wakeOnlyMeCheck = document.getElementById("wake-only-me-check");
  const testMicBtn = document.getElementById("test-mic-btn");
  const testMicResults = document.getElementById("test-mic-results");
  const testMicStatus = document.getElementById("test-mic-status");
  const testMicLevel = document.getElementById("test-mic-level");
  const testMicHeard = document.getElementById("test-mic-heard");

  let appIsLocked = false;

  let lastVoice = { ready: false, reason: "" };
  function showLockScreen(voiceUnlockReady, voiceReason) {
    if (voiceUnlockReady === undefined) { voiceUnlockReady = lastVoice.ready; voiceReason = lastVoice.reason; }
    else lastVoice = { ready: !!voiceUnlockReady, reason: voiceReason || "" };
    appIsLocked = true;
    const off = document.getElementById("lock-voice-off");
    if (off) {
      off.textContent = voiceUnlockReady ? "" : (voiceReason || "Voice unlock isn't ready.");
      off.classList.toggle("hidden", !!voiceUnlockReady || !voiceReason);
    }
    if (lockScreenModal) {
      lockScreenModal.classList.remove("hidden");
      if (lockPinInput) {
        lockPinInput.value = "";
        setTimeout(() => lockPinInput.focus(), 120);
      }
      if (lockErrorMsg) lockErrorMsg.textContent = "";
      if (voiceUnlockReady) {
        if (lockVoiceBtn) lockVoiceBtn.classList.remove("hidden");
        if (lockVoiceHint) lockVoiceHint.classList.remove("hidden");
      } else {
        if (lockVoiceBtn) lockVoiceBtn.classList.add("hidden");
        if (lockVoiceHint) lockVoiceHint.classList.add("hidden");
      }
    }
    updateStatusBadge("Locked", "locked");
    if (window.VegaLabLog) window.VegaLabLog.close();
    settingsModal.classList.add("hidden");
  }

  function hideLockScreen() {
    appIsLocked = false;
    if (lockScreenModal) {
      lockScreenModal.classList.add("hidden");
      if (lockPinInput) lockPinInput.value = "";
      if (lockErrorMsg) lockErrorMsg.textContent = "";
    }
    updateStatusBadge(readyText, "ready");
    if (promptInput) promptInput.focus();
    refreshChatList();
    loadLabLog();
    if (window.VegaAgentLab && window.VegaAgentLab.refresh) {
      window.VegaAgentLab.refresh();
    }
  }

  window.showLockScreen = showLockScreen;
  window.hideLockScreen = hideLockScreen;

  function tryPinUnlock() {
    const pin = lockPinInput ? lockPinInput.value.trim() : "";
    if (!pin) {
      if (lockErrorMsg) lockErrorMsg.textContent = "Please enter your PIN.";
      return;
    }
    if (lockUnlockBtn) {
      lockUnlockBtn.disabled = true;
      lockUnlockBtn.textContent = "Verifying...";
    }
    fetch("/api/lock/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin: pin })
    })
      .then(r => r.json())
      .then(res => {
        if (lockUnlockBtn) {
          lockUnlockBtn.disabled = false;
          lockUnlockBtn.textContent = "Unlock";
        }
        if (res && res.ok) {
          hideLockScreen();
        } else {
          if (lockErrorMsg) lockErrorMsg.textContent = res.error || "Wrong PIN. Try again.";
          if (lockPinInput) {
            lockPinInput.value = "";
            lockPinInput.focus();
          }
        }
      })
      .catch(err => {
        if (lockUnlockBtn) {
          lockUnlockBtn.disabled = false;
          lockUnlockBtn.textContent = "Unlock";
        }
        if (lockErrorMsg) lockErrorMsg.textContent = "Error: " + err.message;
      });
  }

  if (lockUnlockBtn) {
    lockUnlockBtn.addEventListener("click", tryPinUnlock);
  }
  if (lockPinInput) {
    lockPinInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") tryPinUnlock();
    });
  }

  if (lockVoiceBtn) {
    lockVoiceBtn.addEventListener("click", () => {
      lockVoiceBtn.disabled = true;
      lockVoiceBtn.textContent = "Listening for your voice...";
      if (lockErrorMsg) lockErrorMsg.textContent = "Say 'Hey Vega' or speak now...";
      fetch("/api/lock/voice_unlock", { method: "POST" })
        .then(r => r.json())
        .then(res => {
          lockVoiceBtn.disabled = false;
          lockVoiceBtn.textContent = "Unlock with my voice";
          if (res && res.ok) {
            hideLockScreen();
          } else {
            if (lockErrorMsg) lockErrorMsg.textContent = res.error || "Voice not recognized. Use your PIN.";
          }
        })
        .catch(err => {
          lockVoiceBtn.disabled = false;
          lockVoiceBtn.textContent = "Unlock with my voice";
          if (lockErrorMsg) lockErrorMsg.textContent = "Error: " + err.message;
        });
    });
  }

  function lockVegaNow() {
    fetch("/api/lock/now", { method: "POST" })
      .then(r => r.json())
      .then(res => {
        if (res && res.ok) showLockScreen(res.voice_unlock_ready, res.voice_reason);
        else if (res && res.error) appendNotice(res.error, "warn");
      });
  }
  window.lockVegaNow = lockVegaNow;

  if (lockVegaBtn) {
    lockVegaBtn.addEventListener("click", () => {
      settingsModal.classList.add("hidden");
      lockVegaNow();
    });
  }

  if (wakeOnlyMeCheck) {
    wakeOnlyMeCheck.addEventListener("change", () => {
      fetch("/api/settings/wake_only_me", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ wake_only_me: wakeOnlyMeCheck.checked })
      });
    });
  }

  if (testMicBtn) {
    testMicBtn.addEventListener("click", () => {
      testMicBtn.disabled = true;
      testMicBtn.textContent = "Testing...";
      if (testMicResults) testMicResults.style.display = "block";
      if (testMicStatus) testMicStatus.textContent = "Recording 3 seconds... speak now!";
      if (testMicLevel) testMicLevel.textContent = "";
      if (testMicHeard) testMicHeard.textContent = "";

      fetch("/api/mic/test", { method: "POST" })
        .then(r => r.json())
        .then(res => {
          testMicBtn.disabled = false;
          testMicBtn.textContent = "Test microphone";
          if (res && res.rms !== undefined) {
            if (testMicLevel) testMicLevel.textContent = `Sound level: ${res.level_pct}% (RMS: ${res.rms})`;
            if (testMicHeard) testMicHeard.textContent = `Heard: "${res.heard || "(silence)"}"`;
          }
          if (res && res.ok) {
            if (testMicStatus) testMicStatus.textContent = "Done! Recorded successfully.";
          } else {
            if (testMicStatus) testMicStatus.textContent = "Error: " + (res.error || "Unknown test error");
          }
        })
        .catch(err => {
          testMicBtn.disabled = false;
          testMicBtn.textContent = "Test microphone";
          if (testMicStatus) testMicStatus.textContent = "Request error: " + err.message;
        });
    });
  }

  // Theme Toggle
  themeToggle.addEventListener("click", () => {
    document.body.classList.toggle("light-theme");
    const isLight = document.body.classList.contains("light-theme");
    localStorage.setItem("vega-theme", isLight ? "light" : "dark");
  });

  if (localStorage.getItem("vega-theme") === "light") {
    document.body.classList.add("light-theme");
  }

  // ==========================================
  // REAL LAB LOG IMPLEMENTATION WITH DAY NAVIGATION & STREAK
  // ==========================================
  let currentLabDayIdx = null; // null means active today
  const lablogPrevBtn = document.getElementById("lablog-prev-btn");
  const lablogTodayBtn = document.getElementById("lablog-today-btn");
  const lablogNextBtn = document.getElementById("lablog-next-btn");
  const lablogStatusTag = document.getElementById("lablog-status-tag");
  const lablogStreakBadge = document.getElementById("lablog-streak-badge");
  const lablogProgressText = document.getElementById("lablog-progress-text");
  const lablogDateText = document.getElementById("lablog-date-text");

  function loadLabLog(dayIdx) {
    if (dayIdx !== undefined) currentLabDayIdx = dayIdx;
    const url = (currentLabDayIdx !== null && currentLabDayIdx !== undefined)
      ? `/api/lablog?track=${currentTrack}&day=${currentLabDayIdx}`
      : `/api/lablog?track=${currentTrack}`;

    fetch(url)
      .then(r => r.json())
      .then(data => {
        if (!data || !data.ok) {
          hubTaskText.textContent = "Could not load Lab Log.";
          return;
        }

        currentLabDayIdx = data.current_idx !== undefined ? data.current_idx : data.today_idx;
        lablogTrackName.textContent = (data.track_name || "Linux").toUpperCase() + " LAB";
        lablogDayTag.textContent = data.label || `Day ${currentLabDayIdx + 1}`;
        hubTaskText.textContent = data.title || "No milestone title";
        hubTaskTopic.textContent = data.topic ? `Topic: ${data.topic}` : "";

        if (lablogStatusTag) {
          if (data.is_today) {
            lablogStatusTag.textContent = "TODAY";
            lablogStatusTag.style.background = "#1e3a2f";
            lablogStatusTag.style.color = "#4ade80";
          } else if (currentLabDayIdx < data.today_idx) {
            lablogStatusTag.textContent = "PAST DAY";
            lablogStatusTag.style.background = "#2a2d35";
            lablogStatusTag.style.color = "var(--text-muted)";
          } else {
            lablogStatusTag.textContent = "UPCOMING";
            lablogStatusTag.style.background = "#1f293d";
            lablogStatusTag.style.color = "var(--accent)";
          }
        }

        if (lablogStreakBadge) {
          lablogStreakBadge.textContent = `Streak: ${data.streak || 0} day${data.streak !== 1 ? 's' : ''}`;
        }
        if (lablogProgressText && data.stats) {
          lablogProgressText.textContent = `${data.stats.done} of ${data.stats.total} days done`;
        }
        if (lablogDateText && data.date_str) {
          lablogDateText.textContent = data.date_str;
        }

        // Render Steps with Interactive Checkboxes
        hubStepsList.innerHTML = "";
        (data.steps || []).forEach(st => {
          const item = document.createElement("div");
          item.className = "step-item";
          item.innerHTML = `
            <input type="checkbox" class="step-checkbox" ${st.done ? "checked" : ""} data-step="${st.num}">
            <span class="step-text ${st.done ? "done" : ""}">${st.num}. ${st.text}</span>
          `;

          const cb = item.querySelector(".step-checkbox");
          const sp = item.querySelector(".step-text");

          cb.addEventListener("change", () => {
            fetch("/api/lablog/tick", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ track: currentTrack, step: st.num, done: cb.checked, day: currentLabDayIdx })
            }).then(r => r.json()).then(res => {
              if (res && res.ok) {
                if (cb.checked) sp.classList.add("done");
                else sp.classList.remove("done");
              }
            });
          });

          hubStepsList.appendChild(item);
        });

        // Notes & Review
        hubNotesInput.value = data.notes || "";
        if (data.score !== undefined && data.score !== null) {
          hubScoreBadge.classList.remove("hidden");
          hubScoreBadge.textContent = `Score: ${data.score}/10`;
        } else {
          hubScoreBadge.classList.add("hidden");
        }
        hubReviewText.textContent = data.review || "Submit your notes and click 'Assess my log' for Vega's review.";
      })
      .catch(err => {
        hubTaskText.textContent = "Error loading Lab Log: " + err.message;
      });
  }

  if (lablogPrevBtn) {
    lablogPrevBtn.addEventListener("click", () => {
      if (currentLabDayIdx !== null && currentLabDayIdx > 0) {
        loadLabLog(currentLabDayIdx - 1);
      }
    });
  }
  if (lablogTodayBtn) {
    lablogTodayBtn.addEventListener("click", () => {
      currentLabDayIdx = null;
      loadLabLog();
    });
  }
  if (lablogNextBtn) {
    lablogNextBtn.addEventListener("click", () => {
      if (currentLabDayIdx !== null) {
        loadLabLog(currentLabDayIdx + 1);
      }
    });
  }

  // Save Notes
  saveNotesBtn.addEventListener("click", () => {
    saveNotesBtn.textContent = "Saving...";
    fetch("/api/lablog/save_notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track: currentTrack, notes: hubNotesInput.value.trim(), day: currentLabDayIdx })
    }).then(r => r.json()).then(res => {
      saveNotesBtn.textContent = res && res.ok ? "Saved ✓" : "Failed";
      setTimeout(() => saveNotesBtn.textContent = "Save notes", 1500);
    });
  });

  // Assess Log
  assessLogBtn.addEventListener("click", () => {
    assessLogBtn.textContent = "Assessing...";
    assessLogBtn.disabled = true;
    fetch("/api/lablog/assess", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track: currentTrack, day: currentLabDayIdx })
    }).then(r => r.json()).then(res => {
      assessLogBtn.textContent = "Assess my log";
      assessLogBtn.disabled = false;
      if (res && res.ok) {
        if (res.score !== undefined && res.score !== null) {
          hubScoreBadge.classList.remove("hidden");
          hubScoreBadge.textContent = `Score: ${res.score}/10`;
        }
        hubReviewText.textContent = res.review || "Assessment completed.";
      }
    }).catch(() => {
      assessLogBtn.textContent = "Assess my log";
      assessLogBtn.disabled = false;
    });
  });

  // Full Lab Log Modal
  openLablogFullBtn.addEventListener("click", () => window.VegaLabLog.open(currentTrack, currentLabDayIdx));
  closeLablogModalBtn.addEventListener("click", () => lablogModal.classList.add("hidden"));
  lablogModal.addEventListener("click", (e) => {
    if (e.target === lablogModal) lablogModal.classList.add("hidden");
  });

  document.querySelectorAll(".track-tab-bar .track-tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".track-tab-bar .track-tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      currentTrack = btn.dataset.track;
      loadFullLabLog(currentTrack);
      loadLabLog();
    });
  });

  function loadFullLabLog(tid) {
    const content = document.getElementById("full-lablog-content");
    content.innerHTML = `<div style="padding:20px; color:var(--text-muted); text-align:center;">Loading ${tid.toUpperCase()} curriculum...</div>`;
    fetch(`/api/lablog?track=${tid}`)
      .then(r => r.json())
      .then(data => {
        if (!data || !data.ok) {
          content.innerHTML = `<div style="padding:20px; color:var(--text-muted);">Could not load curriculum.</div>`;
          return;
        }

        content.innerHTML = `
          <div style="background:var(--bg-card); padding:16px; border-radius:var(--radius-md); border:1px solid var(--border-color); margin-bottom:14px;">
            <div style="font-size:16px; font-weight:700; margin-bottom:4px;">${data.title}</div>
            <div style="font-size:13px; color:var(--accent); margin-bottom:8px;">${data.label} • ${data.topic}</div>
            <div style="font-size:12px; color:var(--text-muted);">Total lessons in track: ${data.total_lessons} • Active progress milestone: Day ${data.today_idx + 1}</div>
          </div>
          <div class="steps-title">PRACTICAL EXERCISES:</div>
          <div class="steps-list" style="margin-bottom:16px;">
            ${(data.steps || []).map(s => `
              <div class="step-item">
                <span style="color:var(--accent); font-weight:600;">${s.num}.</span>
                <span class="step-text ${s.done ? "done" : ""}">${s.text}</span>
              </div>
            `).join("")}
          </div>
          <div class="steps-title">SAVED LAB NOTES:</div>
          <div style="background:var(--bg-main); padding:10px; border-radius:var(--radius-sm); border:1px solid var(--border-color); font-size:12px; line-height:1.5; color:var(--text-main); margin-bottom:14px; white-space:pre-wrap;">${data.notes || "No notes logged for today yet."}</div>
          <div class="steps-title">VEGA'S REVIEW & VERIFICATION:</div>
          <div style="background:var(--bg-main); padding:10px; border-radius:var(--radius-sm); border:1px solid var(--border-color); font-size:12px; line-height:1.5; color:var(--text-muted); white-space:pre-wrap;">${data.review || "No review submitted yet."}</div>
        `;
      });
  }

  // ==========================================
  // LIVE TECH NEWS & INTERNSHIPS
  // ==========================================
  const hubTechList = document.getElementById("hub-tech-list");
  const hubInternshipsList = document.getElementById("hub-internships-list");

  document.getElementById("refresh-tech-btn").addEventListener("click", loadTechNews);
  document.getElementById("refresh-internships-btn").addEventListener("click", loadInternships);

  function loadTechNews() {
    hubTechList.innerHTML = `<div class="news-item loading">Loading latest tech headlines...</div>`;
    fetch("/api/news/tech")
      .then(r => r.json())
      .then(data => {
        hubTechList.innerHTML = "";
        if (!data || !data.ok || !data.items || data.items.length === 0) {
          hubTechList.innerHTML = `
            <div class="retry-container">
              <div>Couldn't load, Retry</div>
              <button class="retry-btn" onclick="loadTechNews()">Retry</button>
            </div>
          `;
          return;
        }

        data.items.forEach(n => {
          const item = document.createElement("div");
          item.className = "news-item";
          item.innerHTML = `
            <div class="news-title">${n.title}</div>
            <div class="news-time">${n.source} • ${n.time}</div>
          `;
          item.onclick = () => {
            fetch("/api/open_url", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ url: n.url })
            });
          };
          hubTechList.appendChild(item);
        });
      })
      .catch(() => {
        hubTechList.innerHTML = `
          <div class="retry-container">
            <div>Couldn't load, Retry</div>
            <button class="retry-btn" onclick="loadTechNews()">Retry</button>
          </div>
        `;
      });
  }

  function loadInternships() {
    hubInternshipsList.innerHTML = `<div class="news-item loading">Searching DevOps & Cloud internships...</div>`;
    fetch("/api/news/internships")
      .then(r => r.json())
      .then(data => {
        hubInternshipsList.innerHTML = "";
        if (!data || !data.ok || !data.items || data.items.length === 0) {
          hubInternshipsList.innerHTML = `
            <div class="retry-container">
              <div>Couldn't load, Retry</div>
              <button class="retry-btn" onclick="loadInternships()">Retry</button>
            </div>
          `;
          return;
        }

        data.items.forEach(item => {
          const div = document.createElement("div");
          div.className = "news-item";
          div.innerHTML = `
            <div class="news-title">${item.title}</div>
            <div class="news-time">${item.company} • <span style="color:var(--online-color); font-weight:600;">${item.badge}</span></div>
          `;
          div.onclick = () => {
            fetch("/api/open_url", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ url: item.url })
            });
          };
          hubInternshipsList.appendChild(div);
        });
      })
      .catch(() => {
        hubInternshipsList.innerHTML = `
          <div class="retry-container">
            <div>Couldn't load, Retry</div>
            <button class="retry-btn" onclick="loadInternships()">Retry</button>
          </div>
        `;
      });
  }
  window.loadTechNews = loadTechNews;
  window.loadInternships = loadInternships;
  window.loadLabLog = loadLabLog;

  // Load Initial State
  fetch("/api/initial_state")
    .then(r => r.json())
    .then(st => {
      if (st && st.version && window.VegaFrontendVersion && st.version !== window.VegaFrontendVersion) {
        window.location.reload();
        return;
      }
      if (st) {
        if (st.active_chat_id) loadChat(st.active_chat_id);
        if (st.today_task) hubTaskText.textContent = st.today_task;
        if (speechLanguageSelect && ["auto", "english", "telugu"].includes(st.voice_language)) speechLanguageSelect.value = st.voice_language;
        if (speechModelSelect && ["small", "medium"].includes(st.telugu_model)) speechModelSelect.value = st.telugu_model;
        if (st.brain_mode) updateModeBadge(st.brain_mode);
        if (st.listening_on !== undefined) updateListeningUI(st.listening_on);
        if (st.voice_on !== undefined) updateSpeakingUI(st.voice_on);
        if (st.startup_enabled !== undefined) startupToggle.checked = st.startup_enabled;
        if (st.speech_rate !== undefined) {
          speedSlider.value = st.speech_rate;
          speedVal.textContent = st.speech_rate + "x";
        }
        if (st.status) { readyText = st.status; updateStatusBadge(readyText, st.locked ? "locked" : "ready"); }
        if (window.VegaSettings) window.VegaSettings.fill(st);
        if (st.wake_only_me !== undefined && wakeOnlyMeCheck) wakeOnlyMeCheck.checked = st.wake_only_me;
        if (st.locked) {
          showLockScreen(st.voice_unlock_ready, st.voice_reason);
        }

        if (!st.locked) {
          refreshChatList();
          loadLabLog();
        }
      }
    })
    .catch(() => {
      refreshChatList();
      loadLabLog();
    });
});
