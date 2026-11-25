(() => {
  const $ = id => document.getElementById(id);
  const workspace = $("agent-workspace");
  const canvasContainer = $("agent-messages-container");
  const welcomeScreen = $("agent-welcome-screen");
  const tasksList = $("agent-tasks-list");
  const practiceView = $("practice-lab-view");
  const sidePanel = $("agent-side-panel");
  const togglePanelBtn = $("agent-toggle-panel-btn");
  const closePanelBtn = $("agent-close-panel-btn");

  const routePill = $("agent-route-pill");
  const selectedStatus = $("agent-selected-status");
  const routeConfigBtn = $("agent-route-config-btn");
  const routeConfigPanel = $("agent-route-config-panel");
  const category = $("agent-category");
  const providerMode = $("agent-provider-mode");
  const toolSelect = $("agent-tool");
  const modelSelect = $("agent-model");
  const taskMode = $("agent-mode");
  const quickModeToggle = $("agent-mode-quick-toggle");
  const quickModeLabel = $("agent-mode-quick-label");
  const agentModelBtn = $("agent-model-picker-btn");
  const agentModelLabel = $("agent-model-picker-label");
  const agentModelDropdown = $("agent-model-dropdown");
  const testButton = $("agent-test-connection");
  const inlineTestBtn = $("agent-inline-test-btn");

  const browseFilesBtn = $("agent-browse-files");
  const attachmentInput = $("agent-attachment-input");
  const attachmentList = $("agent-attachment-list");
  const attachmentStatus = $("agent-attachment-status");
  const attachmentSummary = $("agent-attachment-summary");

  const sourcePicker = $("agent-source-picker");
  const sourceList = $("agent-source-list");
  const sourceSummary = $("agent-source-summary");

  const taskPrompt = $("agent-task-prompt");
  const cancelButton = $("agent-cancel-btn");
  const sendButton = $("agent-send-task");
  const taskState = $("agent-task-state");
  const connectionStatus = $("agent-connection-status");
  const localStatus = $("agent-local-status");
  const historyList = $("agent-task-history");
  const providerList = $("agent-provider-list");
  const auditOutput = $("agent-audit-output");

  const practiceOutput = $("practice-lab-output");
  const practiceCwd = $("practice-lab-cwd");

  const attachmentFiles = [];
  const supportedAttachmentTypes = new Set([
    "txt", "md", "markdown", "rst", "py", "js", "mjs", "cjs", "ts", "tsx", "jsx",
    "json", "jsonl", "yaml", "yml", "toml", "ini", "cfg", "conf", "xml", "html", "htm",
    "css", "scss", "less", "sql", "sh", "ps1", "bat", "cmd", "log", "csv", "properties",
    "cs", "cpp", "c", "h", "hpp", "go", "rs", "java", "php", "rb", "swift", "kt",
    "dockerfile", "svg", "graphql", "proto", "diff", "patch"
  ]);
  const maxAttachmentBytes = 256000, maxAttachmentCount = 5, maxAttachmentTotalBytes = 1000000;

  let options = null, activeTaskId = null, pendingApprovalTaskId = null, currentBlockReason = "";
  let pollTimer = null, pollInFlight = false, pollErrorCount = 0;
  let sending = false, practiceTranscript = "Type help for supported commands.";
  let renderedTasks = new Map();

  function escapeHtml(t) {
    return String(t || "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }

  function formatMarkdown(text) {
    if (window.marked) return marked.parse(text);
    return escapeHtml(text).replace(/\n/g, "<br>");
  }

  async function api(url, init) {
    const response = await fetch(url, init);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      if (response.status === 423 || data.locked) {
        const error = new Error(data.error || "Vega is locked. Unlock Vega to use Agent and Practice Lab.");
        error.status = 423;
        error.locked = true;
        throw error;
      }
      const error = new Error(data.error || "Request failed (" + response.status + ")");
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function saved() {
    try { return JSON.parse(localStorage.getItem("vega-agent-workspace-v1") || "{}"); }
    catch (_) { return {}; }
  }

  function persist() {
    try {
      localStorage.setItem("vega-agent-workspace-v1", JSON.stringify({
        category: category.value,
        providerMode: providerMode.value,
        tool: toolSelect.value,
        model: modelSelect.value,
        mode: taskMode.value
      }));
    } catch (_) {}
  }

  function files() {
    if (!sourceList) return [];
    return Array.from(sourceList.querySelectorAll("input[data-source]:checked")).map(x => x.value);
  }

  function agentRows() {
    if (Array.isArray(options && options.agents)) return options.agents;
    return (Array.isArray(options && options.tools) ? options.tools : []).filter(agent => agent.agent);
  }

  function attachmentBytes() {
    return attachmentFiles.reduce((total, file) => total + file.size, 0);
  }

  function renderAttachments() {
    if (!attachmentList) return;
    attachmentList.replaceChildren();
    attachmentFiles.forEach((file, index) => {
      const item = document.createElement("div");
      item.className = "agent-attachment-item";
      const name = document.createElement("span");
      name.textContent = file.name + " · " + Math.max(1, Math.ceil(file.size / 1024)) + " KB";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "agent-attachment-remove";
      remove.textContent = "✕";
      remove.setAttribute("aria-label", "Remove " + file.name);
      remove.addEventListener("click", () => {
        attachmentFiles.splice(index, 1);
        renderAttachments();
        update();
      });
      item.append(name, remove);
      attachmentList.appendChild(item);
    });

    const total = attachmentBytes();
    if (attachmentSummary) {
      attachmentSummary.textContent = attachmentFiles.length
        ? attachmentFiles.length + " file(s) · " + Math.ceil(total / 1024) + " KB total"
        : "No external files attached";
      attachmentSummary.classList.toggle("hidden", attachmentFiles.length === 0);
    }
  }

  function addAttachments(selected) {
    const existing = new Set(attachmentFiles.map(file => file.name + "\0" + file.size + "\0" + file.lastModified));
    const errors = [];
    Array.from(selected || []).forEach(file => {
      const key = file.name + "\0" + file.size + "\0" + file.lastModified;
      if (existing.has(key)) return;
      const extension = file.name.includes(".") ? file.name.split(".").pop().toLowerCase() : "";
      if (!supportedAttachmentTypes.has(extension)) {
        errors.push(file.name + ": unsupported format (." + (extension || "unknown") + "); choose a readable text or source file (.py, .js, .ts, .txt, .md, .json, etc.).");
        return;
      }
      if (/(^|[._ -])(secret|secrets|credential|credentials|password)([._ -]|$)|\.env/i.test(file.name)) {
        errors.push(file.name + ": credential and secret files cannot be attached.");
        return;
      }
      if (file.size > maxAttachmentBytes) {
        errors.push(file.name + ": exceeds the 256 KB per-file limit.");
        return;
      }
      if (attachmentFiles.length >= maxAttachmentCount) {
        errors.push("Attach up to five external files.");
        return;
      }
      if (attachmentBytes() + file.size > maxAttachmentTotalBytes) {
        errors.push("External attachments have a 1 MB total limit.");
        return;
      }
      attachmentFiles.push(file);
      existing.add(key);
    });
    if (attachmentStatus) {
      attachmentStatus.textContent = errors.join(" ") || (attachmentFiles.length ? "Selected text stays on this PC until you start the displayed local task." : "");
    }
    renderAttachments();
    update();
  }

  function pick() {
    const rec = options && options.recommendations && options.recommendations[category.value];
    let toolId = toolSelect.value === "auto" ? (rec && rec.tool || "ollama") : toolSelect.value;
    let modelId = modelSelect.value === "auto" ? (rec && rec.model || "qwen2.5:3b") : modelSelect.value;
    let tool = (options && options.tools || []).find(x => x.id === toolId);
    let model = (options && options.models || []).find(x => x.id === modelId);

    if (toolSelect.value === "auto" && rec && rec.tool) {
      const recTool = (options && options.tools || []).find(x => x.id === rec.tool);
      if (recTool) tool = recTool;
    }

    const local = Boolean(tool && (tool.id === "ollama" || tool.local_adapter));
    const verifiedModels = (options && options.verified_models && options.verified_models[toolId]) || [];
    const directVerified = Boolean(options && options.verified_connections && options.verified_connections[toolId] === modelId);
    const listVerified = Array.isArray(verifiedModels) && verifiedModels.includes(modelId);
    const modelVerified = Boolean(directVerified || listVerified);

    let connected = false;
    if (tool && tool.cloud) {
      connected = Boolean(modelVerified && tool.free_quota_status === "verified");
    } else if (local) {
      const modelAvailable = model ? model.availability === "available" : true;
      connected = Boolean(modelVerified && modelAvailable && (tool.id === "ollama" || tool.installed));
    } else if (tool) {
      connected = Boolean(modelVerified && tool.connected);
    }

    return { rec, toolId, modelId, tool, model, local, connected };
  }

  function renderProviders() {
    if (!providerList) return;
    providerList.replaceChildren();
    const settingsTarget = $("agent-settings-providers");
    if (settingsTarget) settingsTarget.replaceChildren();

    const agents = agentRows();
    const installedCount = agents.filter(agent => agent.installed).length;
    const connectedCount = agents.filter(agent => agent.connected).length;
    const settingsStatus = $("agent-settings-status");
    if (settingsStatus) {
      settingsStatus.textContent = installedCount + " of " + agents.length +
        " installed · " + connectedCount + " local connections verified · cloud runs block while free quota is unknown.";
    }

    agents.forEach(agent => {
      const card = document.createElement("article");
      card.className = "agent-provider-card";
      const head = document.createElement("div");
      head.className = "agent-provider-heading";
      const title = document.createElement("strong");
      title.textContent = agent.label + (agent.version ? " " + agent.version : "");
      const kind = document.createElement("span");
      kind.textContent = agent.local_adapter ? "Local route" : "Cloud route";
      head.append(title, kind);

      const installed = agent.installed === true ? "installed" :
        agent.installed === false ? "not installed" : "install status unknown";
      const connection = agent.connected === true ? "verified" :
        agent.connected === false ? "not verified" : "not checked";
      const stat = document.createElement("div");
      stat.textContent = "Windows: " + installed + " · Vega: registered · Connection: " + connection;

      const allowance = typeof agent.free_allowance === "string" && agent.free_allowance.trim()
        ? agent.free_allowance : "Not reported";
      const free = document.createElement("div");
      free.textContent = "Free use: " + allowance;

      const why = document.createElement("div");
      why.textContent = (typeof agent.reason === "string" && agent.reason ? agent.reason : "No additional diagnostic reported.") +
        (typeof agent.setup_step === "string" && agent.setup_step ? " " + agent.setup_step : "");

      card.append(head, stat, free, why);
      providerList.appendChild(card);
      if (settingsTarget) settingsTarget.appendChild(card.cloneNode(true));
    });
  }

  function renderSourceFiles() {
    if (!sourceList) return;
    const keep = new Set(files());
    sourceList.replaceChildren();
    (Array.isArray(options.source_files) ? options.source_files : []).forEach(file => {
      const label = document.createElement("label");
      label.className = "agent-source-option";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = file.path;
      input.dataset.source = "true";
      input.checked = keep.has(file.path);
      input.addEventListener("change", update);
      const name = document.createElement("span");
      name.textContent = file.path + " · " + Math.ceil(file.bytes / 1024) + " KB";
      label.append(input, name);
      sourceList.appendChild(label);
    });
    if (!sourceList.children.length) {
      const empty = document.createElement("div");
      empty.className = "agent-source-empty";
      empty.textContent = "No eligible source files found. Questions need no files.";
      sourceList.appendChild(empty);
    }
  }

  function updateModelSelect(keepModel) {
    if (!options) return;
    const currentToolId = toolSelect.value;
    const oldVal = keepModel !== undefined ? keepModel : modelSelect.value;
    modelSelect.replaceChildren();

    const auto = document.createElement("option");
    auto.value = "auto";
    auto.textContent = "Auto · recommendation";
    modelSelect.appendChild(auto);

    let allowed = [];
    if (currentToolId === "groq") {
      allowed = ["llama-3.3-70b-versatile", "openai/gpt-oss-120b"];
    } else if (currentToolId === "gemini") {
      allowed = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash", "gemini-1.5-flash"];
    } else if (currentToolId === "ollama") {
      allowed = (Array.isArray(options.models) ? options.models : [])
        .map(m => m.id)
        .filter(id => !id.startsWith("gemini-") && id !== "llama-3.3-70b-versatile" && id !== "openai/gpt-oss-120b");
    } else {
      allowed = ["llama-3.3-70b-versatile", "gemini-2.0-flash", "openai/gpt-oss-120b", "gemini-1.5-flash"];
      (Array.isArray(options.models) ? options.models : []).forEach(m => {
        if (!allowed.includes(m.id)) allowed.push(m.id);
      });
    }

    allowed.forEach(mId => {
      const modelObj = (options.models || []).find(x => x.id === mId);
      const isCloud = mId.startsWith("gemini-") || mId.includes("70b") || mId.includes("120b");
      const o = document.createElement("option");
      o.value = mId;
      o.textContent = mId + (isCloud ? " · free cloud" : (modelObj && modelObj.availability === "available" ? " · local" : " · not detected"));
      modelSelect.appendChild(o);
    });

    if (Array.from(modelSelect.options).some(x => x.value === oldVal)) {
      modelSelect.value = oldVal;
    } else {
      modelSelect.value = "auto";
    }
  }

  function fill() {
    if (!options) return;
    const old = saved(), currentTool = toolSelect.value;
    toolSelect.replaceChildren();

    const primaryEngines = [
      { id: "auto", label: "Auto (Local verified route)" },
      { id: "ollama", label: "Ollama (Local offline)" },
      { id: "groq", label: "Groq Cloud (Free Llama 3.3 70B / 120B)" },
      { id: "gemini", label: "Gemini Cloud (Free 2.0 / 1.5 Flash)" }
    ];

    primaryEngines.forEach(eng => {
      const o = document.createElement("option");
      o.value = eng.id;
      o.textContent = eng.label;
      toolSelect.appendChild(o);
    });

    (Array.isArray(options.tools) ? options.tools : []).forEach(tool => {
      if (["auto", "groq", "gemini", "ollama"].includes(tool.id)) return;
      let stateBadge = "unavailable";
      if (tool.connected) {
        stateBadge = "verified";
      } else if (tool.installed && tool.local_ready) {
        stateBadge = "installed (untested)";
      } else if (tool.setup_status === "setup_required" || !tool.installed) {
        stateBadge = "setup required";
      }
      const o = document.createElement("option");
      o.value = tool.id;
      o.textContent = (tool.label || tool.id) + " · " + stateBadge;
      if (tool.reason) o.title = tool.reason + (tool.setup_step ? " " + tool.setup_step : "");
      toolSelect.appendChild(o);
    });

    let wantedTool = old.tool || currentTool || "auto";
    toolSelect.value = Array.from(toolSelect.options).some(x => x.value === wantedTool) ? wantedTool : "auto";

    updateModelSelect(old.model);

    if (["general", "coding", "devops"].includes(old.category)) category.value = old.category;
    if (["auto", "local", "free_cloud"].includes(old.providerMode)) {
      providerMode.value = old.providerMode;
    } else {
      providerMode.value = "auto";
    }
    taskMode.value = old.mode === "edit" ? "edit" : "read_only";

    renderSourceFiles();
    renderProviders();

    if (localStatus) {
      localStatus.textContent = "Copilot Agent active · High-speed Cloud (Groq / Gemini) & Ollama Local";
    }
    update();
  }


  // ===================================================
  // GEMINI-STYLE MODEL SWITCHER (Agent Mode)
  // ===================================================
  const AGENT_MODELS = [
    {
      id: "auto",
      tool: "auto",
      model: "auto",
      providerMode: "auto",
      label: "Auto",
      title: "Auto Router",
      desc: "Auto-selects verified free cloud brain or local Ollama"
    },
    {
      id: "groq-70b",
      tool: "groq",
      model: "llama-3.3-70b-versatile",
      providerMode: "free_cloud",
      label: "Groq 70B",
      title: "Groq Llama 3.3 70B",
      desc: "Ultra-fast free cloud inference · Complex reasoning & coding"
    },
    {
      id: "groq-120b",
      tool: "groq",
      model: "openai/gpt-oss-120b",
      providerMode: "free_cloud",
      label: "Groq 120B",
      title: "Groq GPT-OSS 120B",
      desc: "Deep knowledge & architecture · Open-weights flagship"
    },
    {
      id: "gemini-25-flash",
      tool: "gemini",
      model: "gemini-2.5-flash",
      providerMode: "free_cloud",
      label: "Gemini 2.5",
      title: "Gemini 2.5 Flash",
      desc: "Google AI flagship · High intelligence & multimodal speed"
    },
    {
      id: "gemini-flash-lite",
      tool: "gemini",
      model: "gemini-flash-latest",
      providerMode: "free_cloud",
      label: "Gemini Lite",
      title: "Gemini Flash Lite",
      desc: "Lightweight, instant conversational answers & low latency"
    },
    {
      id: "ollama-local",
      tool: "ollama",
      model: "qwen2.5:3b",
      providerMode: "local",
      label: "Local Qwen",
      title: "Ollama Local (Offline)",
      desc: "100% private on-device model · No internet required"
    },
    {
      id: "ollama-coder",
      tool: "ollama",
      model: "qwen2.5-coder:3b",
      providerMode: "local",
      label: "Local Coder",
      title: "Ollama Qwen Coder",
      desc: "Local code specialist · On-device programming assistance"
    }
  ];

  function getActiveAgentModelId() {
    const t = toolSelect ? toolSelect.value : "auto";
    const m = modelSelect ? modelSelect.value : "auto";
    if (t === "auto") return "auto";
    if (t === "groq") return m.includes("120b") ? "groq-120b" : "groq-70b";
    if (t === "gemini") return (m.includes("latest") || m.includes("1.5")) ? "gemini-flash-lite" : "gemini-25-flash";
    if (t === "ollama") return m.includes("coder") ? "ollama-coder" : "ollama-local";
    return "auto";
  }

  function renderAgentModelDropdown() {
    if (!agentModelDropdown) return;
    const currentId = getActiveAgentModelId();
    const currentObj = AGENT_MODELS.find(x => x.id === currentId) || AGENT_MODELS[0];
    if (agentModelLabel) agentModelLabel.textContent = currentObj.label;

    let html = "";
    AGENT_MODELS.forEach(m => {
      const isSelected = m.id === currentId;
      html += `
        <div class="model-picker-item ${isSelected ? "selected" : ""}" data-agent-model-id="${m.id}" role="menuitem">
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
      <div class="model-picker-footer" id="agent-model-manage-keys">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
        <span>Manage Keys & Local Models</span>
      </div>
    `;
    agentModelDropdown.innerHTML = html;

    agentModelDropdown.querySelectorAll(".model-picker-item").forEach(el => {
      el.addEventListener("click", () => {
        const mid = el.dataset.agentModelId;
        const target = AGENT_MODELS.find(x => x.id === mid);
        if (target) {
          if (providerMode) providerMode.value = target.providerMode;
          if (toolSelect) toolSelect.value = target.tool;
          updateModelSelect(target.model);
          if (modelSelect) modelSelect.value = target.model;
          persist();
          update();
          closeAgentModelDropdown();
        }
      });
    });

    const manageBtn = agentModelDropdown.querySelector("#agent-model-manage-keys");
    if (manageBtn) {
      manageBtn.addEventListener("click", () => {
        closeAgentModelDropdown();
        const settingsModal = $("settings-modal");
        if (settingsModal) {
          settingsModal.classList.remove("hidden");
          const keysTab = document.querySelector('[data-tab="keys"]');
          if (keysTab) keysTab.click();
        }
      });
    }
  }

  function toggleAgentModelDropdown() {
    if (!agentModelDropdown) return;
    const isHidden = agentModelDropdown.classList.contains("hidden");
    if (isHidden) {
      renderAgentModelDropdown();
      agentModelDropdown.classList.remove("hidden");
      if (agentModelBtn) {
        agentModelBtn.classList.add("active");
        agentModelBtn.setAttribute("aria-expanded", "true");
      }
    } else {
      closeAgentModelDropdown();
    }
  }

  function closeAgentModelDropdown() {
    if (!agentModelDropdown) return;
    agentModelDropdown.classList.add("hidden");
    if (agentModelBtn) {
      agentModelBtn.classList.remove("active");
      agentModelBtn.setAttribute("aria-expanded", "false");
    }
  }

  if (agentModelBtn) {
    agentModelBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleAgentModelDropdown();
    });
  }

  document.addEventListener("click", (e) => {
    if (agentModelDropdown && !agentModelDropdown.classList.contains("hidden")) {
      if (!agentModelDropdown.contains(e.target) && !agentModelBtn.contains(e.target)) {
        closeAgentModelDropdown();
      }
    }
  });

  function update() {
    if (!options) {
      sendButton.disabled = true;
      testButton.disabled = true;
      return false;
    }
    const s = pick(), selectedFiles = files(), busy = Boolean(activeTaskId || pendingApprovalTaskId || sending);
    const selectedAttachments = attachmentFiles.length;
    let reason = "";

    if (taskMode.value === "edit" && category.value === "general") {
      category.value = "coding";
    }
    if (taskMode.value === "edit") {
      if (selectedFiles.length === 0) {
        reason = "Select exactly one Vega source file before requesting an edit proposal.";
      } else if (selectedFiles.length > 1) {
        reason = "Select exactly one Vega source file before requesting an edit proposal.";
      }
    }

    if (providerMode.value === "free_cloud" && (!s.tool || s.local)) {
      reason = "Choose a cloud provider for Free Cloud mode.";
    } else if (providerMode.value === "free_cloud" && s.tool && s.tool.free_quota_status !== "verified") {
      reason = "Free cloud usage for " + s.tool.label + " is unverified. Check the provider's usage dashboard first.";
    } else if (!s.tool) {
      reason = "Choose an agent provider.";
    } else if (providerMode.value === "local" && !s.local) {
      reason = "No local adapter is verified for this agent. Choose Ollama for local offline use.";
    } else if (s.local && s.model && s.model.availability !== "available") {
      reason = "Ollama did not confirm " + s.modelId + ". Start Ollama and refresh.";
    } else if (s.local && s.tool.id !== "ollama" && !s.tool.installed) {
      reason = s.tool.label + " is not installed for this Windows profile.";
    } else if (selectedFiles.length > 5) {
      reason = "Choose up to five project source files.";
    } else if (selectedAttachments > maxAttachmentCount || attachmentBytes() > maxAttachmentTotalBytes) {
      reason = "Remove external files to meet the attachment limit.";
    } else if (busy) {
      reason = "Resolve the current task before starting another.";
    }

    const agentName = s.tool ? s.tool.label : (toolSelect.value === "auto" ? "Auto" : "No agent");
    const modelName = s.modelId || "Auto model";
    const routeReason = toolSelect.value === "auto" ? (s.rec && s.rec.reason || "Auto selects a tested route.") : "Manual agent override.";
    const costStatus = s.local ? "No inference cost (Local loopback Ollama)" : (s.tool && s.tool.free_allowance ? s.tool.free_allowance : "Free Cloud tier");
    const providerLabel = s.connected
      ? (s.local ? "Local Ollama · Verified" : "Free Cloud · Verified")
      : (providerMode.value === "free_cloud" ? "Free Cloud · Unverified" : (s.local ? "Local · Unverified" : "Unverified"));

    const currentModelId = getActiveAgentModelId();
    const activeModelObj = AGENT_MODELS.find(x => x.id === currentModelId) || AGENT_MODELS[0];
    if (agentModelLabel) agentModelLabel.textContent = activeModelObj.label;
    currentBlockReason = reason;
    [category, providerMode, toolSelect, modelSelect, taskMode].forEach(select => { if (select) select.disabled = busy; });
    if (browseFilesBtn) browseFilesBtn.disabled = busy;
    if (quickModeToggle) quickModeToggle.disabled = busy;

    const isEdit = taskMode.value === "edit";
    if (quickModeLabel) {
      quickModeLabel.textContent = isEdit ? "Mode: Edit proposal" : "Mode: Read only";
    }
    if (quickModeToggle) {
      quickModeToggle.classList.toggle("mode-edit-active", isEdit);
    }

    const endpoint = options.endpoint || "127.0.0.1:11434";
    if (localStatus) {
      localStatus.textContent = endpoint + " · selected route " +
        (s.connected ? "verified: " : "not verified: ") + agentName + " · " + modelName;
    }

    if (selectedStatus) {
      const modeTag = isEdit ? "Edit proposal" : "Read only";
      const filesInfo = selectedFiles.length ? " · " + selectedFiles.length + " Vega file(s)" : "";
      const attachInfo = selectedAttachments ? " · " + selectedAttachments + " attachment(s)" : "";
      const pType = s.local ? "Local" : "Cloud";
      const vState = s.connected ? "Verified" : "Unverified";
      selectedStatus.textContent = agentName + " · " + modelName + " · " + pType + " · " + modeTag + " · " + vState + filesInfo + attachInfo;
      selectedStatus.title = reason || (s.connected ? "Route is verified and ready" : "Connection check required");
    }

    if (sourceSummary) {
      sourceSummary.textContent = "(" + selectedFiles.length + ")";
    }

    if (inlineTestBtn) {
      const showInlineTest = Boolean(!s.connected && !busy && s.tool && (s.local || s.tool.installed || s.tool.cloud));
      inlineTestBtn.classList.toggle("hidden", !showInlineTest);
      if (showInlineTest) {
        inlineTestBtn.onclick = () => testConnection();
      }
    }

    if (connectionStatus) {
      const statusText = s.connected
        ? ("Verified " + (s.local ? "local route" : "cloud route") + " · " + (s.tool ? s.tool.label : "Ollama") + " · " + (s.modelId || ""))
        : (s.local ? "Local route requires connection test" : "Cloud route requires connection test");
      connectionStatus.textContent = statusText;
      connectionStatus.className = "agent-connection-hint" + (s.connected ? " text-success" : " text-error");
    }

    if (!busy && taskState) {
      const oldState = taskState.textContent || "";
      if (reason) {
        taskState.textContent = "Not ready: " + reason;
        taskState.className = "agent-task-state text-blocked";
      } else if (oldState === "" || oldState === "No task running" || oldState.startsWith("Ready") || oldState.startsWith("Not") || oldState.startsWith("Select")) {
        taskState.textContent = "Ready to start · " + agentName + " (" + modelName + ")";
        taskState.className = "agent-task-state text-ready";
      }
    }

    sendButton.disabled = Boolean(reason);
    testButton.disabled = busy || !s.tool;
    testButton.textContent = s.tool ? ("Test " + s.tool.label + " with " + s.modelId) : "Test connection";
    cancelButton.disabled = !(activeTaskId || pendingApprovalTaskId);
    return !reason;
  }

  function getOrCreateTaskTurn(task, userPromptText, attachmentsList, filesList) {
    let turn = renderedTasks.get(task.id);
    if (!turn) {
      if (welcomeScreen) welcomeScreen.style.display = "none";
      const wrap = document.createElement("div");
      wrap.className = "agent-task-turn";
      wrap.dataset.taskId = task.id;

      // User prompt bubble
      const userCard = document.createElement("div");
      userCard.className = "agent-user-prompt-card";
      const promptText = userPromptText || (task.prompt && typeof task.prompt === "string" ? task.prompt : (task.title || "Agent task"));

      let badgeHtml = "";
      if (filesList && filesList.length) {
        badgeHtml += filesList.map(f => `<span class="agent-ctx-pill">Project: ${escapeHtml(f)}</span>`).join(" ");
      }
      if (attachmentsList && attachmentsList.length) {
        badgeHtml += attachmentsList.map(a => `<span class="agent-ctx-pill">Attached: ${escapeHtml(typeof a === 'string' ? a : a.name)}</span>`).join(" ");
      }

      userCard.innerHTML = `
        <div class="agent-user-prompt-meta">
          <span class="agent-turn-role">You</span>
          <span class="agent-turn-time">${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
        </div>
        <div class="agent-user-prompt-text">${escapeHtml(promptText)}</div>
        ${badgeHtml ? `<div class="agent-user-prompt-badges">${badgeHtml}</div>` : ""}
      `;

      // Agent response card
      const agentCard = document.createElement("div");
      agentCard.className = "agent-task-card";
      agentCard.innerHTML = `
        <div class="agent-card-header">
          <div class="agent-card-identity">
            <span class="agent-avatar-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg></span>
            <div>
              <span class="agent-card-title">${escapeHtml(task.tool || "Ollama")} · ${escapeHtml(task.model || "model")}</span>
              <span class="agent-card-sub">${task.provider ? escapeHtml(task.provider) : (task.provider_type === "local" ? "Local route · Ollama" : "Cloud AI")}</span>
            </div>
          </div>
          <div class="agent-card-badges">
            <span class="agent-status-badge ${task.status}">${escapeHtml(task.status)}</span>
            <span class="agent-card-elapsed"></span>
          </div>
        </div>
        <div class="agent-card-body">
          <div class="agent-inline-output"></div>
          <div class="agent-diff-slot hidden"></div>
          <div class="agent-error-slot hidden"></div>
        </div>
      `;

      wrap.append(userCard, agentCard);
      tasksList.appendChild(wrap);
      canvasContainer.scrollTop = canvasContainer.scrollHeight;

      turn = { el: wrap, startTime: Date.now() };
      renderedTasks.set(task.id, turn);
    }
    return turn;
  }

  function updateTaskTurn(task) {
    const turn = getOrCreateTaskTurn(task);
    const wrap = turn.el;
    const badge = wrap.querySelector(".agent-status-badge");
    const elapsedEl = wrap.querySelector(".agent-card-elapsed");
    const outputEl = wrap.querySelector(".agent-inline-output");
    const diffSlot = wrap.querySelector(".agent-diff-slot");
    const errorSlot = wrap.querySelector(".agent-error-slot");

    if (badge) {
      badge.textContent = task.status;
      badge.className = "agent-status-badge " + task.status;
    }

    if (elapsedEl && turn.startTime) {
      const sec = Math.max(1, Math.round((Date.now() - turn.startTime) / 1000));
      elapsedEl.textContent = sec + "s";
    }

    if (outputEl) {
      outputEl.innerHTML = formatMarkdown(task.output || (task.status === "running" ? "Thinking & analyzing task…" : "No result text."));
      outputEl.querySelectorAll("pre").forEach(pre => {
        if (!pre.querySelector(".agent-code-copy-btn")) {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "agent-code-copy-btn";
          btn.textContent = "Copy";
          btn.title = "Copy code to clipboard";
          btn.addEventListener("click", async (e) => {
            e.stopPropagation();
            const codeEl = pre.querySelector("code") || pre;
            try {
              await navigator.clipboard.writeText(codeEl.innerText.trim());
              btn.textContent = "Copied ✓";
              setTimeout(() => { btn.textContent = "Copy"; }, 2000);
            } catch (_) {
              btn.textContent = "Failed";
            }
          });
          pre.appendChild(btn);
        }
      });
    }

    // Handle reviewable diff for awaiting_approval
    if (diffSlot) {
      if (task.status === "awaiting_approval" && task.preview) {
        diffSlot.classList.remove("hidden");
        diffSlot.innerHTML = `
          <div class="agent-diff-container">
            <div class="agent-diff-header">
              <strong>Proposed File Edit</strong>
              <span>Review before applying to file:</span>
            </div>
            <pre class="agent-diff-preview">${escapeHtml(task.preview)}</pre>
            <div class="agent-diff-actions">
              <button type="button" class="mini-btn accent agent-btn-approve">Approve & Apply Edit</button>
              <button type="button" class="mini-btn agent-btn-discard">Discard Proposal</button>
            </div>
          </div>
        `;
        const approveBtn = diffSlot.querySelector(".agent-btn-approve");
        const discardBtn = diffSlot.querySelector(".agent-btn-discard");
        if (approveBtn) {
          approveBtn.onclick = async () => {
            approveBtn.disabled = true;
            try {
              const res = await api("/api/agent/tasks/" + encodeURIComponent(task.id) + "/approve", { method: "POST" });
              pendingApprovalTaskId = null;
              showTask(res.task);
              await Promise.all([history(), audit()]);
            } catch (err) {
              if (taskState) taskState.textContent = err.message;
            } finally {
              approveBtn.disabled = false;
            }
          };
        }
        if (discardBtn) {
          discardBtn.onclick = () => cancelButton.click();
        }
      } else {
        diffSlot.classList.add("hidden");
        diffSlot.replaceChildren();
      }
    }

    // Error slot
    if (errorSlot) {
      if (task.error) {
        errorSlot.classList.remove("hidden");
        errorSlot.textContent = task.error;
      } else {
        errorSlot.classList.add("hidden");
      }
    }

    canvasContainer.scrollTop = canvasContainer.scrollHeight;
  }

  function showTask(task) {
    if (!task) return;
    const running = task.status === "queued" || task.status === "running";
    const waiting = task.status === "awaiting_approval" && Boolean(task.preview);

    activeTaskId = running ? task.id : null;
    pendingApprovalTaskId = waiting ? task.id : null;

    if (taskState) {
      taskState.textContent = task.status + (task.error ? " · " + task.error : "");
    }

    updateTaskTurn(task);
    cancelButton.disabled = !(running || waiting);
    cancelButton.title = waiting ? "Discard proposal" : "Cancel task";
    cancelButton.setAttribute("aria-label", waiting ? "Discard" : "Cancel");
    update();
  }

  async function history() {
    try {
      const data = await api("/api/agent/tasks");
      if (!historyList) return;
      historyList.replaceChildren();
      (data.tasks || []).forEach(task => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "agent-history-item";
        item.textContent = (task.title || task.category || "Task") + " · " + task.status;
        item.title = task.tool + " · " + task.model;
        item.addEventListener("click", () => {
          showTask(task);
          if (sidePanel) sidePanel.classList.add("hidden");
        });
        historyList.appendChild(item);
      });
      if (!historyList.children.length) {
        const empty = document.createElement("div");
        empty.textContent = "No tasks yet";
        historyList.appendChild(empty);
      }
    } catch (_) {}
  }

  async function audit() {
    if (!auditOutput) return;
    try {
      const data = await api("/api/agent/audit");
      auditOutput.textContent = (data.records || []).slice().reverse().map(item =>
        new Date(item.time * 1000).toLocaleString() + " · " + item.status + " · " + item.category +
        " · " + item.tool + " · " + item.provider + "/" + item.model + " · " + item.mode).join("\n") || "No audit entries yet.";
    } catch (error) {
      auditOutput.textContent = error.message;
    }
  }

  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  async function poll() {
    const taskId = activeTaskId;
    if (!taskId || pollInFlight) return;
    pollInFlight = true;
    try {
      const data = await api("/api/agent/tasks/" + encodeURIComponent(taskId));
      if (activeTaskId !== taskId || !data.task || data.task.id !== taskId) return;
      pollErrorCount = 0;
      showTask(data.task);
      if (!activeTaskId) {
        stopPolling();
        await Promise.all([history(), audit()]);
      }
    } catch (error) {
      if (activeTaskId !== taskId) return;
      if (error.status === 404) {
        activeTaskId = null;
        pendingApprovalTaskId = null;
        stopPolling();
        if (taskState) taskState.textContent = "Task status is no longer available. Refresh task history before retrying.";
        update();
      } else {
        pollErrorCount += 1;
        if (taskState) {
          taskState.textContent = "Status check failed" + (pollErrorCount > 1 ? " (" + pollErrorCount + " attempts)" : "") +
            "; task tracking and cancellation remain available. Retrying…";
        }
      }
    } finally {
      pollInFlight = false;
    }
  }

  async function loadOptions(refresh) {
    try {
      const response = await api(refresh ? "/api/agent/refresh" : "/api/agent/options", refresh ? { method: "POST" } : undefined);
      if (response && response.error) throw new Error(response.error);
      if (!response || typeof response !== "object" ||
          (!Array.isArray(response.tools) && !Array.isArray(response.agents))) {
        throw new Error("Provider status response is incomplete. Refresh status; nothing was sent.");
      }
      options = Object.assign({}, response, {
        agents: Array.isArray(response.agents) ? response.agents :
          (Array.isArray(response.tools) ? response.tools.filter(agent => agent.agent) : []),
        tools: Array.isArray(response.tools) ? response.tools : [],
        models: Array.isArray(response.models) ? response.models : [],
        source_files: Array.isArray(response.source_files) ? response.source_files : [],
        recommendations: response.recommendations && typeof response.recommendations === "object" ? response.recommendations : {},
        verified_connections: response.verified_connections && typeof response.verified_connections === "object" ? response.verified_connections : {},
        verified_models: response.verified_models && typeof response.verified_models === "object" ? response.verified_models : {}
      });
      fill();
      await Promise.all([history(), audit()]);
    } catch (error) {
      options = null;
      if (localStatus) localStatus.textContent = "Provider status unavailable.";
      if (selectedStatus) selectedStatus.textContent = error.message + " Nothing was sent.";
      update();
    }
  }

  async function runTask() {
    const text = taskPrompt.value.trim();
    if (!text || text.length > 8000) {
      if (taskState) taskState.textContent = "Enter a task from 1 to 8,000 characters.";
      taskPrompt.focus();
      return;
    }
    if (!update()) {
      if (taskState) taskState.textContent = "Not sent: " + (currentBlockReason || "resolve the selected route status.");
      return;
    }
    const s = pick();
    const chosen = {
      category: category.value,
      tool: s.toolId,
      model: s.modelId,
      mode: taskMode.value,
      providerMode: providerMode.value,
      files: files(),
      attachments: attachmentFiles.slice()
    };

    sending = true;
    update();

    let taskError = "";
    try {
      if (!s.connected) {
        if (taskState) taskState.textContent = "Testing " + s.tool.label + " connection with " + s.modelId + "…";
        await testConnection(true);
      }
      if (taskState) taskState.textContent = "Reading selected text files locally…";
      const attachedText = await Promise.all(chosen.attachments.map(async file => ({ name: file.name, text: await file.text() })));
      const request = JSON.stringify({ vega_agent_request: 1, text: text, files: chosen.files, attachments: attachedText });

      if (taskState) taskState.textContent = "Starting the displayed local task…";
      const data = await api("/api/agent/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          category: chosen.category,
          tool: chosen.tool,
          model: chosen.model,
          mode: chosen.mode,
          provider_mode: chosen.providerMode,
          prompt: request
        })
      });

      if (!data.task || typeof data.task.id !== "string" || !data.task.id) {
        await history();
        throw new Error("The task response did not include a task ID. Refresh task history before trying again.");
      }

      taskPrompt.value = "";
      taskPrompt.style.height = "auto";
      activeTaskId = data.task.id;
      attachmentFiles.splice(0, attachmentFiles.length);
      renderAttachments();

      const routeLabel = (s.tool && s.tool.label) || chosen.tool || "Copilot Agent";
      if (taskState) taskState.textContent = "Route: " + routeLabel + " · " + (s.modelId || "cloud");
      getOrCreateTaskTurn(data.task, text, chosen.attachments, chosen.files);
      showTask(data.task);

      stopPolling();
      pollTimer = setInterval(poll, 600);
      await Promise.all([poll(), history()]);
    } catch (error) {
      taskError = error.message;
    } finally {
      sending = false;
      update();
      if (taskError && taskState) {
        taskState.textContent = taskError;
        taskState.className = "agent-task-state text-error";
      }
    }
  }

  async function testConnection(propagateError) {
    const s = pick();
    if (!s.tool) {
      if (connectionStatus) connectionStatus.textContent = "Select an agent to test.";
      if (propagateError) throw new Error("Select an agent to test.");
      return false;
    }
    testButton.disabled = true;
    const isCloud = Boolean(s.tool.cloud || s.tool.provider_type === "free_cloud");
    if (connectionStatus) connectionStatus.textContent = "Testing connection to " + s.tool.label + "…";
    try {
      const result = await api("/api/agent/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tool: s.toolId, model: s.modelId || (isCloud ? "default" : "qwen2.5:3b") })
      });
      if (connectionStatus) connectionStatus.textContent = "Connected: " + (result.connection.agent || s.tool.label) + " · " + (result.connection.provider || "Verified");
      await loadOptions(true);
      return true;
    } catch (error) {
      if (connectionStatus) connectionStatus.textContent = error.message;
      await loadOptions(false);
      if (propagateError) throw error;
      return false;
    } finally {
      testButton.disabled = false;
      update();
    }
  }

  async function practiceState() {
    try {
      const state = await api("/api/practice-lab");
      if (practiceCwd) practiceCwd.textContent = state.cwd;
    } catch (error) {
      if (practiceCwd) practiceCwd.textContent = error.message;
    }
  }

  function showPracticeView(show) {
    if (window.setAppMode) {
      window.setAppMode(show ? "practice" : "agent");
    }
    if (show) practiceState();
  }

  // Event Listeners
  if (quickModeToggle) {
    quickModeToggle.addEventListener("click", () => {
      taskMode.value = taskMode.value === "edit" ? "read_only" : "edit";
      persist();
      update();
    });
  }

  if (taskMode) {
    taskMode.addEventListener("change", () => {
      persist();
      update();
    });
  }

  if (routeConfigBtn && routeConfigPanel) {
    routeConfigBtn.addEventListener("click", () => {
      routeConfigPanel.classList.toggle("hidden");
    });
  }

  if (togglePanelBtn && sidePanel) {
    togglePanelBtn.addEventListener("click", () => {
      sidePanel.classList.toggle("hidden");
    });
  }

  if (closePanelBtn && sidePanel) {
    closePanelBtn.addEventListener("click", () => {
      sidePanel.classList.add("hidden");
    });
  }

  const refreshBtn = $("agent-refresh-status");
  if (refreshBtn) refreshBtn.addEventListener("click", () => loadOptions(true));

  if (browseFilesBtn && attachmentInput) {
    browseFilesBtn.addEventListener("click", () => attachmentInput.click());
    attachmentInput.addEventListener("change", () => {
      addAttachments(attachmentInput.files);
      attachmentInput.value = "";
    });
  }

  if (testButton) testButton.addEventListener("click", testConnection);
  if (sendButton) sendButton.addEventListener("click", runTask);

  if (taskPrompt) {
    taskPrompt.addEventListener("input", () => {
      taskPrompt.style.height = "auto";
      taskPrompt.style.height = Math.min(taskPrompt.scrollHeight, 180) + "px";
      update();
    });
    taskPrompt.addEventListener("keydown", event => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        runTask();
      }
    });
  }

  if (toolSelect) {
    toolSelect.addEventListener("change", () => {
      updateModelSelect();
      persist();
      update();
    });
  }

  [category, providerMode, modelSelect, taskMode].forEach(select => {
    if (select) select.addEventListener("change", () => { persist(); update(); });
  });

  if (cancelButton) {
    cancelButton.addEventListener("click", async () => {
      const id = activeTaskId || pendingApprovalTaskId;
      if (!id) return;
      cancelButton.disabled = true;
      try {
        await api("/api/agent/tasks/" + encodeURIComponent(id) + "/cancel", { method: "POST" });
        if (activeTaskId) {
          if (taskState) taskState.textContent = "Cancelling…";
          setTimeout(poll, 250);
        } else {
          pendingApprovalTaskId = null;
          if (taskState) taskState.textContent = "Edit proposal discarded; no file was changed.";
          await Promise.all([history(), audit()]);
          update();
        }
      } catch (error) {
        if (taskState) taskState.textContent = error.message;
      } finally {
        cancelButton.disabled = !(activeTaskId || pendingApprovalTaskId);
      }
    });
  }

  // Welcome starter cards
  const cardCoding = $("agent-card-coding");
  if (cardCoding) {
    cardCoding.addEventListener("click", () => {
      category.value = "coding";
      taskMode.value = "read_only";
      persist();
      update();
      taskPrompt.focus();
    });
  }

  const cardDevops = $("agent-card-devops");
  if (cardDevops) {
    cardDevops.addEventListener("click", () => {
      category.value = "devops";
      persist();
      update();
      taskPrompt.focus();
    });
  }

  const cardGeneral = $("agent-card-general");
  if (cardGeneral) {
    cardGeneral.addEventListener("click", () => {
      category.value = "general";
      taskMode.value = "read_only";
      persist();
      update();
      taskPrompt.focus();
    });
  }

  const cardPractice = $("agent-card-practice");
  if (cardPractice) {
    cardPractice.addEventListener("click", () => showPracticeView(true));
  }

  const practiceCloseBtn = $("practice-lab-close");
  if (practiceCloseBtn) {
    practiceCloseBtn.addEventListener("click", () => showPracticeView(false));
  }

  // Practice Lab Commands
  const practiceForm = $("practice-lab-form");
  if (practiceForm) {
    practiceForm.addEventListener("submit", async event => {
      event.preventDefault();
      const input = $("practice-lab-command");
      const command = input ? input.value.trim() : "";
      if (!command) return;
      if (input) input.value = "";
      try {
        const data = await api("/api/practice-lab/command", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ command: command })
        });
        practiceTranscript += "\n\n$ " + command + "\n" + data.output;
        if (practiceCwd) practiceCwd.textContent = data.state.cwd;
      } catch (error) {
        practiceTranscript += "\n\n$ " + command + "\n" + error.message;
      }
      practiceTranscript = practiceTranscript.slice(-18000);
      if (practiceOutput) {
        practiceOutput.textContent = practiceTranscript;
        practiceOutput.scrollTop = practiceOutput.scrollHeight;
      }
    });
  }

  const practiceResetBtn = $("practice-lab-reset");
  if (practiceResetBtn) {
    practiceResetBtn.addEventListener("click", async () => {
      try {
        const data = await api("/api/practice-lab/reset", { method: "POST" });
        if (practiceCwd) practiceCwd.textContent = data.state.cwd;
        practiceTranscript = "Simulator reset. Type help for supported commands.";
        if (practiceOutput) practiceOutput.textContent = practiceTranscript;
      } catch (error) {
        practiceTranscript += "\n" + error.message;
        if (practiceOutput) practiceOutput.textContent = practiceTranscript;
      }
    });
  }

  async function onActivated() {
    await loadOptions(false);
    update();
    if (taskPrompt) taskPrompt.focus();
  }

  window.VegaAgentLab = {
    onActivated: onActivated,
    showPractice: () => showPracticeView(true),
    hidePractice: () => showPracticeView(false),
    refresh: () => loadOptions(true)
  };
  window.VegaPracticeLab = {
    onActivated: practiceState
  };

  loadOptions(false);
})();
