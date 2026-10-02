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
  const welcomeScreen = document.getElementById("welcome-screen");
  const promptInput = document.getElementById("prompt-input");
  const sendBtn = document.getElementById("send-btn");
  const micBtn = document.getElementById("mic-btn");
  const attachBtn = document.getElementById("attach-btn");
  const fileInput = document.getElementById("file-input");
  const attachmentPreview = document.getElementById("attachment-preview");
  const attachmentFilename = document.getElementById("attachment-filename");
  const removeAttachmentBtn = document.getElementById("remove-attachment-btn");
  const emojiBtn = document.getElementById("emoji-btn");
  const emojiPopover = document.getElementById("emoji-popover");
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
  const newChatBtn = document.getElementById("new-chat-btn");
  const currentChatTitle = document.getElementById("current-chat-title");
  const pinChatBtn = document.getElementById("pin-chat-btn");
  const statusBadge = document.getElementById("status-badge");
  const statusBadgeText = document.getElementById("status-badge-text");
  const listeningToggleBtn = document.getElementById("listening-toggle-btn");
  const listeningText = document.getElementById("listening-text");
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
  let attachedFileContent = null;
  let currentTrack = "linux";

  // Configure marked with highlight.js
  if (window.marked) {
    marked.setOptions({
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
  });

  // Keyboard shortcut: Enter to send, Shift+Enter for newline, Ctrl+. for emojis
  promptInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    } else if (e.ctrlKey && e.key === ".") {
      e.preventDefault();
      toggleEmojiPopover();
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

        if (type === "wake") {
          updateStatusBadge("Listening...", "listening");
          micBtn.classList.add("recording");
          micBtn.title = "Listening to your voice...";
        } else if (type === "voice_input") {
          updateStatusBadge("Thinking...", "thinking");
          appendMessage("you", data.text);
        } else if (type === "voice_response") {
          micBtn.classList.remove("recording");
          micBtn.title = "Click to talk";
          updateStatusBadge("Ready - just talk", "ready");
          appendMessage("assistant", data.text, data.source_label);
          if (data.open_tab === "lablog") {
            openRightPanelTab("lablog");
            loadLabLog();
          }
          refreshChatList();
        } else if (type === "status") {
          updateStatusBadge(data.text, data.badge);
        } else if (type === "hotkey_mic") {
          toggleMic();
        } else if (type === "toggle_quickbar") {
          fetch("/api/quickbar/toggle", { method: "POST" });
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

  // System Status Check
  function checkSystemStatus() {
    fetch("/api/status")
      .then(r => r.json())
      .then(st => {
        if (st) {
          updateModeBadge(st.brain_mode);
          updateListeningUI(st.listening_on);
          if (st.voice_on !== undefined) voiceToggle.checked = st.voice_on;
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
    listeningText.textContent = isOn ? "🎤 Listening: ON" : "🎤 Listening: OFF";
    wakeToggle.checked = isOn;
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

  topQuickbarBtn.addEventListener("click", () => {
    fetch("/api/quickbar/toggle", { method: "POST" });
  });

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
    if (!isRecording) {
      startRecording();
    } else {
      stopRecording();
    }
  }

  function startRecording() {
    isRecording = true;
    micBtn.classList.add("recording");
    micBtn.title = "Listening... speak now";
    updateStatusBadge("Listening...", "listening");

    fetch("/api/mic/start", { method: "POST" })
      .then(r => r.json())
      .then(res => {
        stopRecording();
        if (res && res.ok && res.text) {
          promptInput.value = res.text;
          handleSend();
        }
      })
      .catch(() => stopRecording());
  }

  function stopRecording() {
    isRecording = false;
    micBtn.classList.remove("recording");
    micBtn.title = "Click to talk (or press Ctrl+Alt+V from anywhere)";
    updateStatusBadge("Ready - just talk", "ready");
    fetch("/api/mic/stop", { method: "POST" });
  }

  // File Attachments
  attachBtn.addEventListener("click", () => fileInput.click());

  fileInput.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;

    attachmentFilename.textContent = file.name;
    attachmentPreview.classList.remove("hidden");

    const reader = new FileReader();
    reader.onload = function(evt) {
      attachedFileContent = {
        name: file.name,
        text: evt.target.result
      };
    };
    reader.readAsText(file);
  });

  removeAttachmentBtn.addEventListener("click", () => {
    attachedFileContent = null;
    attachmentPreview.classList.add("hidden");
    fileInput.value = "";
  });

  // Emoji Popover
  emojiBtn.addEventListener("click", toggleEmojiPopover);

  function toggleEmojiPopover() {
    emojiPopover.classList.toggle("hidden");
  }

  document.querySelectorAll(".emoji-grid span").forEach(span => {
    span.addEventListener("click", () => {
      const emoji = span.textContent;
      const start = promptInput.selectionStart;
      const end = promptInput.selectionEnd;
      promptInput.value = promptInput.value.substring(0, start) + emoji + promptInput.value.substring(end);
      promptInput.selectionStart = promptInput.selectionEnd = start + emoji.length;
      promptInput.focus();
      emojiPopover.classList.add("hidden");
    });
  });

  document.addEventListener("click", (e) => {
    if (!emojiPopover.contains(e.target) && e.target !== emojiBtn) {
      emojiPopover.classList.add("hidden");
    }
  });

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
  document.getElementById("card-lablog").addEventListener("click", () => {
    openRightPanelTab("lablog");
    handleSendWithText("What is my Lab Log task for today?");
  });

  // Sidebar Daily Items
  document.getElementById("daily-lablog").addEventListener("click", () => {
    openRightPanelTab("lablog");
    loadLabLog();
  });
  document.getElementById("daily-news").addEventListener("click", () => {
    handleSendWithText("What are the top news headlines today?");
  });
  document.getElementById("daily-tech").addEventListener("click", () => {
    openRightPanelTab("tech");
    loadTechNews();
  });
  document.getElementById("daily-internships").addEventListener("click", () => {
    openRightPanelTab("internships");
    loadInternships();
  });
  document.getElementById("daily-weather").addEventListener("click", () => {
    handleSendWithText("What's the weather in Chennai?");
  });
  document.getElementById("daily-tasks").addEventListener("click", () => {
    handleSendWithText("Show my upcoming tasks.");
  });
  document.getElementById("daily-reminders").addEventListener("click", () => {
    handleSendWithText("Show my reminders.");
  });
  document.getElementById("daily-diary").addEventListener("click", () => {
    handleSendWithText("Open my diary.");
  });
  document.getElementById("daily-summaries").addEventListener("click", () => {
    summariesModal.classList.remove("hidden");
  });

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
        if (!projs || projs.length === 0) {
          container.innerHTML = `<div style="padding:6px 10px; color:var(--text-muted); font-size:12px;">No custom projects</div>`;
          return;
        }
        projs.forEach(p => {
          const item = document.createElement("div");
          item.className = "daily-item";
          item.innerHTML = `
            <span class="daily-icon">📁</span>
            <span style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${p.name}</span>
          `;
          item.onclick = () => {
            handleSendWithText(`In our project '${p.name}', let's work on our tasks.`);
          };
          container.appendChild(item);
        });
      });
  }

  loadProjects();

  function handleSendWithText(t) {
    promptInput.value = t;
    handleSend();
  }

  // Send Message Logic with SSE Streaming
  async function handleSend() {
    let text = promptInput.value.trim();
    if (!text || isGenerating) return;

    if (attachedFileContent) {
      text = `[Attached File: ${attachedFileContent.name}]\n${attachedFileContent.text}\n\n${text}`;
      attachedFileContent = null;
      attachmentPreview.classList.add("hidden");
      fileInput.value = "";
    }

    promptInput.value = "";
    promptInput.style.height = "auto";
    welcomeScreen.style.display = "none";

    appendMessage("you", text);

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
        body: JSON.stringify({ text: text, chat_id: activeChatId }),
        signal: abortController.signal
      });

      if (!response.ok) throw new Error("HTTP error " + response.status);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let fullText = "";
      let sourceLabel = "Online";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split("\n");

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.substring(6));
              if (data.token) {
                fullText = data.full || (fullText + data.token);
                sourceLabel = data.source_label || sourceLabel;
                updateStreamingBubble(fullText, sourceLabel);
              } else if (data.done) {
                fullText = data.text || fullText;
                sourceLabel = data.source_label || sourceLabel;
                finalizeAssistantBubble(fullText, sourceLabel);
                if (data.open_tab === "lablog") {
                  openRightPanelTab("lablog");
                  loadLabLog();
                }
              }
            } catch (e) {
              fullText += line.substring(6);
              updateStreamingBubble(fullText, sourceLabel);
            }
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
      updateStatusBadge("Ready - just talk", "ready");
    }
  }

  // Appending UI Messages
  function appendMessage(who, text, sourceLabel = null, model = null, timestamp = null) {
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
      body.textContent = text;
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
      editBtn.innerHTML = `✏️ Edit`;
      editBtn.onclick = () => {
        promptInput.value = text;
        promptInput.focus();
      };
      actions.appendChild(editBtn);
    } else {
      const copyBtn = document.createElement("button");
      copyBtn.className = "msg-action-btn";
      copyBtn.innerHTML = `📋 Copy`;
      copyBtn.onclick = () => {
        navigator.clipboard.writeText(text);
        copyBtn.textContent = "✓ Copied";
        setTimeout(() => copyBtn.innerHTML = `📋 Copy`, 1500);
      };

      const regenBtn = document.createElement("button");
      regenBtn.className = "msg-action-btn";
      regenBtn.innerHTML = `🔄 Try again`;
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

  function formatMarkdown(text) {
    if (window.marked) return marked.parse(text);
    return text.replace(/\n/g, "<br>");
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
      copyBtn.innerHTML = `📋 Copy code`;
      copyBtn.onclick = () => {
        navigator.clipboard.writeText(codeEl.innerText);
        copyBtn.textContent = "✓ Copied!";
        setTimeout(() => copyBtn.innerHTML = `📋 Copy code`, 1800);
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
            appendMessage(m.who || "you", m.text || "", m.source_label || "Online", m.model, m.t);
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
        renderChatList(allChats);
      })
      .catch(() => {});
  }

  function renderChatList(chats) {
    chatHistoryContainer.innerHTML = "";
    if (!chats || chats.length === 0) {
      chatHistoryContainer.innerHTML = `<div style="padding:16px 10px; color:var(--text-muted); font-size:12px;">No chats yet</div>`;
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
      const d = c.updated_at ? new Date(c.updated_at) : new Date();
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
        <span class="chat-item-title">${c.pinned ? "★ " : ""}${c.title || "Untitled"}</span>
        <div class="chat-item-actions">
          <button class="chat-action-btn edit" title="Rename">✏️</button>
          <button class="chat-action-btn del" title="Delete">🗑️</button>
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

  // Search Filter
  chatSearch.addEventListener("input", (e) => {
    const q = e.target.value.toLowerCase();
    const filtered = allChats.filter((c) => (c.title || "").toLowerCase().includes(q));
    renderChatList(filtered);
  });

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
  voiceToggle.addEventListener("change", () => {
    fetch("/api/settings/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: "voice_on", value: voiceToggle.checked })
    });
  });

  wakeToggle.addEventListener("change", () => {
    fetch("/api/settings/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: "wake_word_on", value: wakeToggle.checked })
    }).then(() => checkSystemStatus());
  });

  cloudSpeechToggle.addEventListener("change", () => {
    fetch("/api/settings/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: "cloud_speech", value: cloudSpeechToggle.checked })
    });
  });

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
          html += "<div style=\"margin-top:6px;color:#888\">Result: " + pass + "/" + (pass+fail) + " PASS - saved to logs/selftest.txt</div>";
          resultsDiv.innerHTML = html;
        })
        .catch(err => {
          runSelftestBtn.textContent = "Run Self-Test";
          runSelftestBtn.disabled = false;
          resultsDiv.textContent = "ERROR: " + err.message;
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
  // REAL LAB LOG IMPLEMENTATION
  // ==========================================
  function loadLabLog() {
    fetch(`/api/lablog?track=${currentTrack}`)
      .then(r => r.json())
      .then(data => {
        if (!data || !data.ok) {
          hubTaskText.textContent = "Could not load Lab Log.";
          return;
        }

        lablogTrackName.textContent = (data.track_name || "Linux").toUpperCase() + " LAB";
        lablogDayTag.textContent = data.label || `Day ${data.today_idx + 1}`;
        hubTaskText.textContent = data.title || "No milestone title";
        hubTaskTopic.textContent = data.topic ? `Topic: ${data.topic}` : "";

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
              body: JSON.stringify({ track: currentTrack, step: st.num, done: cb.checked })
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

  // Save Notes
  saveNotesBtn.addEventListener("click", () => {
    saveNotesBtn.textContent = "Saving...";
    fetch("/api/lablog/save_notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track: currentTrack, notes: hubNotesInput.value.trim() })
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
      body: JSON.stringify({ track: currentTrack })
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
  openLablogFullBtn.addEventListener("click", () => {
    lablogModal.classList.remove("hidden");
    loadFullLabLog(currentTrack);
  });
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
      if (st) {
        if (st.active_chat_id) loadChat(st.active_chat_id);
        if (st.today_task) hubTaskText.textContent = st.today_task;
        if (st.brain_mode) updateModeBadge(st.brain_mode);
        if (st.listening_on !== undefined) updateListeningUI(st.listening_on);
        if (st.voice_on !== undefined) voiceToggle.checked = st.voice_on;
        if (st.startup_enabled !== undefined) startupToggle.checked = st.startup_enabled;
        if (st.speech_rate !== undefined) {
          speedSlider.value = st.speech_rate;
          speedVal.textContent = st.speech_rate + "x";
        }
        refreshChatList();
        loadLabLog();
        loadTechNews();
        loadInternships();
      }
    })
    .catch(() => {
      refreshChatList();
      loadLabLog();
      loadTechNews();
      loadInternships();
    });
});
