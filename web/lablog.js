// ===================================================
// VEGA 2.0 - LAB LOG PAGE (like the old Vega window's Lab Log)
// Every skill (Linux, Python, C), ticks, notes, Mark done, Assess my log,
// "Ask Vega about this day" with quick buttons, "How to learn this", and the progress map.
// ===================================================

(function () {
  const page = document.getElementById("lablog-page");
  const wrap = document.getElementById("lab-wrap");
  const messages = document.getElementById("messages-container");
  const input = document.getElementById("input-container");
  const title = document.getElementById("current-chat-title");
  const pinBtn = document.getElementById("pin-chat-btn");

  let track = "linux";
  let day = null;          // null = today's lesson
  let data = null;
  let savedTitle = "";
  let asking = false;
  const history = {};      // "track:day" -> [[question, answer, attachments], ...]
  let labAttachedItems = [];

  function formatBytes(bytes) {
    if (!bytes || bytes <= 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
  }

  function addLabFiles(fileList) {
    if (!fileList || !fileList.length) return;
    for (const file of fileList) {
      const isImage = file.type.startsWith("image/") || /\.(png|jpe?g|webp|gif|bmp|svg)$/i.test(file.name);
      const isPdf = file.type === "application/pdf" || /\.pdf$/i.test(file.name);
      const item = {
        id: "lab_att_" + Date.now() + "_" + Math.random().toString(36).slice(2, 7),
        name: file.name,
        size: file.size,
        isImage: isImage,
        isPdf: isPdf,
        mimeType: file.type || (isImage ? "image/png" : (isPdf ? "application/pdf" : "text/plain")),
        dataUrl: null,
        text: null,
        loaded: false
      };
      labAttachedItems.push(item);

      const reader = new FileReader();
      if (isImage || isPdf) {
        reader.onload = function(evt) {
          item.dataUrl = evt.target.result;
          item.loaded = true;
          renderLabAttachmentPills();
        };
        reader.readAsDataURL(file);
      } else {
        reader.onload = function(evt) {
          item.text = evt.target.result;
          item.loaded = true;
          renderLabAttachmentPills();
        };
        reader.readAsText(file);
      }
      reader.onerror = function() {
        item.loaded = true;
        item.error = "Could not read this file. Remove it and attach it again.";
        renderLabAttachmentPills();
      };
    }
    renderLabAttachmentPills();
  }

  function renderLabAttachmentPills() {
    const preview = wrap.querySelector("#lab-attachment-preview");
    const list = wrap.querySelector("#lab-attachment-pills");
    const clearBtn = wrap.querySelector("#lab-clear-attachments-btn");
    if (!preview || !list) return;

    if (labAttachedItems.length === 0) {
      preview.classList.add("hidden");
      if (clearBtn) clearBtn.classList.add("hidden");
      return;
    }

    preview.classList.remove("hidden");
    if (clearBtn) clearBtn.classList.toggle("hidden", labAttachedItems.length < 2);
    list.innerHTML = "";

    labAttachedItems.forEach((item, idx) => {
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
      rmBtn.onclick = () => {
        labAttachedItems.splice(idx, 1);
        renderLabAttachmentPills();
      };
      pill.appendChild(rmBtn);

      list.appendChild(pill);
    });
  }

  function renderAttachmentsHtml(atts) {
    if (!atts || !atts.length) return "";
    let html = "";
    const imgAtts = atts.filter(a => a.isImage && a.dataUrl);
    if (imgAtts.length > 0) {
      html += `<div class="msg-attachments-gallery" style="margin:4px 0;">`;
      imgAtts.forEach(a => {
        html += `<img class="msg-attachment-img" src="${a.dataUrl}" alt="${esc(a.name)}" title="${esc(a.name)} (Click to view full size)" style="max-height:120px;" onclick="window.open('${a.dataUrl}','_blank')">`;
      });
      html += `</div>`;
    }
    const fileAtts = atts.filter(a => !a.isImage);
    if (fileAtts.length > 0) {
      html += `<div class="msg-files-badges" style="margin:4px 0;">`;
      fileAtts.forEach(a => {
        html += `<div class="msg-file-badge"><span>${a.isPdf ? "PDF" : "FILE"}</span> <span>${esc(a.name)}</span> <span style="opacity:0.6;font-size:10px;">(${formatBytes(a.size)})</span></div>`;
      });
      html += `</div>`;
    }
    return html;
  }

  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"']/g, ch =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const md = (t) => (window.marked ? marked.parse(String(t || "")) : esc(t).replace(/\n/g, "<br>"));
  // `code` in step text -> <code>
  const stepHtml = (t) => esc(t).replace(/`([^`]+)`/g, "<code>$1</code>");

  function post(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })
      .then(r => r.json());
  }

  function openUrl(url) {
    if (url) post("/api/open_url", { url });
  }

  function copy(text, btn) {
    navigator.clipboard.writeText(text).then(() => {
      if (!btn) return;
      const old = btn.textContent;
      btn.textContent = "✓ Copied";
      setTimeout(() => (btn.textContent = old), 1400);
    });
  }

  function open(t, d) {
    if (t) track = t;
    if (d !== undefined) day = d;
    if (page.classList.contains("hidden")) {
      savedTitle = title.textContent;
    }
    // Unconditionally hide all chat and agent containers so Lab Log is strictly exclusive
    messages.classList.add("hidden");
    input.classList.add("hidden");
    const aw = document.getElementById("agent-workspace");
    const ac = document.getElementById("agent-composer");
    const ap = document.getElementById("agent-side-panel");
    if (aw) aw.classList.add("hidden");
    if (ac) ac.classList.add("hidden");
    if (ap) ap.classList.add("hidden");
    page.classList.remove("hidden");
    if (pinBtn) pinBtn.classList.add("hidden");
    title.textContent = "Lab Log";
    load();
  }

  function close() {
    if (page.classList.contains("hidden")) return;
    saveNotes(true);
    page.classList.add("hidden");
    const isAgent = window.getAppMode && window.getAppMode() === "agent";
    const aw = document.getElementById("agent-workspace");
    const ac = document.getElementById("agent-composer");
    const ap = document.getElementById("agent-side-panel");
    if (isAgent) {
      if (aw) aw.classList.remove("hidden");
      if (ac) ac.classList.remove("hidden");
      messages.classList.add("hidden");
      input.classList.add("hidden");
    } else {
      messages.classList.remove("hidden");
      input.classList.remove("hidden");
      if (aw) aw.classList.add("hidden");
      if (ac) ac.classList.add("hidden");
      if (ap) ap.classList.add("hidden");
    }
    if (pinBtn) pinBtn.classList.remove("hidden");
    title.textContent = savedTitle || "New Conversation";
  }

  function load() {
    const q = day === null || day === undefined ? "" : `&day=${day}`;
    fetch(`/api/lablog?track=${encodeURIComponent(track)}${q}`)
      .then(r => r.json())
      .then(res => {
        if (!res || !res.ok) {
          wrap.innerHTML = `<div class="lab-empty">Couldn't load the Lab Log${res && res.error ? ": " + esc(res.error) : ""}.</div>`;
          return;
        }
        data = res;
        day = res.current_idx;
        render();
      })
      .catch(err => (wrap.innerHTML = `<div class="lab-empty">Couldn't load the Lab Log: ${esc(err.message)}</div>`));
  }

  function key() { return `${track}:${day}`; }

  function render() {
    const d = data;
    const st = d.stats || {};
    const whenCls = d.when === "TODAY" ? "today" : d.when === "PAST DAY" ? "past" : "upcoming";
    wrap.innerHTML = `
      <div class="lab-top">
        <button class="mini-btn" id="lab-back">${(window.getAppMode && window.getAppMode() === "agent") ? "&larr; Back to Agent" : "&larr; Back to chat"}</button>
        <div class="lab-tracks">${(d.tracks || []).map(t =>
          `<button class="lab-track ${t.id === track ? "active" : ""}" data-track="${esc(t.id)}">${esc(t.name)}</button>`).join("")}</div>
      </div>

      <div class="lab-nav">
        <button class="mini-btn" id="lab-prev" title="Previous day">◀</button>
        <button class="mini-btn" id="lab-today">Today</button>
        <button class="mini-btn" id="lab-next" title="Next day">▶</button>
        <span class="lab-status ${d.done ? "ok" : ""}">${d.done ? "✓ Done" : "Not done yet"}${d.score != null ? ` · Score ${d.score}/10` : ""}</span>
        <span class="lab-stats">Streak ${st.streak || 0} · best ${st.best || 0} · ${st.done || 0} of ${st.total || 0} done${st.missed ? ` · ${st.missed} carried over` : ""}</span>
      </div>

      <div class="lab-head">${esc(d.track_name)} · ${esc(d.label)} · ${esc(d.date_str)} · <span class="lab-when ${whenCls}">${esc(d.when)}</span>${d.topic ? " · " + esc(d.topic) : ""}</div>
      <h2 class="lab-title">${esc(d.title)}</h2>
      <div class="lab-res">${d.minutes ? `About ${esc(d.minutes)} min · ` : ""}${esc(d.resources || "")}</div>

      <div class="lab-card lab-steps">${(d.steps || []).length ? d.steps.map(s => `
        <label class="lab-step"><input type="checkbox" data-step="${s.num}" ${s.done ? "checked" : ""}>
          <span class="${s.done ? "done" : ""}">${s.num}. ${stepHtml(s.raw || s.text)}</span></label>`).join("")
        : `<div class="lab-muted">No steps for this day.</div>`}</div>

      <div class="lab-label">My lab notes <span class="lab-muted">(what I did, what I learned, errors I hit)</span></div>
      <textarea id="lab-notes" class="lab-notes" placeholder="Type what you ran and what happened. Paste errors here too.">${esc(d.notes)}</textarea>
      <div class="lab-btns">
        <button class="mini-btn" id="lab-save">Save notes</button>
        <button class="mini-btn" id="lab-done">${d.done ? "Mark not done" : "Mark done"}</button>
        <span class="lab-grow"></span>
        <button class="mini-btn accent" id="lab-assess">Assess my log</button>
      </div>
      <div class="lab-label">Vega's review</div>
      <div class="lab-card lab-review" id="lab-review">${d.review ? esc(d.review) :
        `No review yet. Write your notes or tick steps, then press “Assess my log”.${track === "linux" ? " I also review today's log by myself every evening." : ""}`}</div>

      <div class="lab-label lab-ask-title">Ask Vega about this day</div>
      <div class="lab-muted">She knows this day's task, steps and your notes.</div>
      <div class="lab-chips">${(d.quick || []).map(q => `<button class="lab-chip" data-quick="${esc(q[0])}">${esc(q[1])}</button>`).join("")}</div>
      <div class="lab-card lab-chat" id="lab-chat"></div>
      <!-- Attached files/screenshots preview in Lab Log -->
      <div id="lab-attachment-preview" class="lab-attachment-preview hidden">
        <div id="lab-attachment-pills" class="lab-attachment-pills"></div>
        <button id="lab-clear-attachments-btn" class="clear-attachments-btn" type="button" title="Clear all attachments">Clear all</button>
      </div>
      <div class="lab-ask-row">
        <input type="file" id="lab-file-input" style="display: none;" multiple>
        <button type="button" class="lab-attach-btn" id="lab-attach-btn" title="Attach file or screenshot (Ctrl+V to paste)">Attach</button>
        <input id="lab-ask-input" type="text" placeholder="Ask anything about this lesson, attach code/screenshot, or paste an error…">
        <button class="mini-btn accent" id="lab-ask-btn">Ask</button>
      </div>

      <div class="lab-label">${track === "linux" ? "How to learn this" : "How to learn this week"}</div>
      <div id="lab-learn"></div>

      <div class="lab-label">Progress map <span class="lab-muted">(click a square to open that day)</span></div>
      <div class="lab-map" id="lab-map" style="grid-template-rows: repeat(${d.map_rows || 7}, 14px);"></div>
    `;
    wire();
    renderChat();
    renderLearn();
    renderMap();
  }

  function wire() {
    wrap.querySelector("#lab-back").onclick = close;
    wrap.querySelectorAll(".lab-track").forEach(b => (b.onclick = () => { saveNotes(true); track = b.dataset.track; day = null; load(); }));
    wrap.querySelector("#lab-prev").onclick = () => { saveNotes(true); if (day > 0) { day -= 1; load(); } };
    wrap.querySelector("#lab-next").onclick = () => { saveNotes(true); if (day < data.total_lessons - 1) { day += 1; load(); } };
    wrap.querySelector("#lab-today").onclick = () => { saveNotes(true); day = null; load(); };
    wrap.querySelectorAll(".lab-step input").forEach(cb => {
      cb.onchange = () => post("/api/lablog/tick", { track, day, step: +cb.dataset.step, done: cb.checked }).then(res => {
        cb.nextElementSibling.classList.toggle("done", cb.checked);
        if (res && res.message && window.appendNotice) window.appendNotice(res.message);
        if (res && res.ok && res.done !== data.done) load();
      });
    });
    wrap.querySelector("#lab-save").onclick = () => saveNotes(false);
    wrap.querySelector("#lab-done").onclick = () => {
      saveNotes(true).then(saved => {
        if (saved === false) throw new Error("Notes could not be saved.");
        return post("/api/lablog/done", { track, day, done: !data.done });
      }).then(res => {
        if (!res || !res.ok) throw new Error("Lesson status could not be saved.");
        load();
      }).catch(() => flash("#lab-save", "Save failed"));
    };
    wrap.querySelector("#lab-assess").onclick = assess;
    wrap.querySelectorAll(".lab-chip").forEach(b => (b.onclick = () => ask(b.dataset.quick)));

    const inp = wrap.querySelector("#lab-ask-input");
    inp.onkeydown = (e) => { if (e.key === "Enter") ask(); };
    wrap.querySelector("#lab-ask-btn").onclick = () => ask();

    const fileInp = wrap.querySelector("#lab-file-input");
    const attachBtn = wrap.querySelector("#lab-attach-btn");
    const clearAttBtn = wrap.querySelector("#lab-clear-attachments-btn");

    if (attachBtn && fileInp) {
      attachBtn.onclick = () => fileInp.click();
      fileInp.onchange = (e) => {
        if (e.target.files && e.target.files.length) {
          addLabFiles(Array.from(e.target.files));
        }
        fileInp.value = "";
      };
    }

    if (clearAttBtn) {
      clearAttBtn.onclick = () => {
        labAttachedItems = [];
        renderLabAttachmentPills();
      };
    }

    // Paste screenshot into Lab Log
    wrap.onpaste = (e) => {
      const items = e.clipboardData && e.clipboardData.items;
      if (!items) return;
      let imgFiles = [];
      for (let i = 0; i < items.length; i++) {
        if (items[i].type.startsWith("image/")) {
          const file = items[i].getAsFile();
          if (file) {
            const num = labAttachedItems.filter(a => a.isImage).length + imgFiles.length + 1;
            imgFiles.push(new File([file], `Screenshot ${num}.png`, { type: file.type || "image/png" }));
          }
        }
      }
      if (imgFiles.length > 0) {
        e.preventDefault();
        addLabFiles(imgFiles);
      }
    };

    const askRow = wrap.querySelector(".lab-ask-row");
    const chatBox = wrap.querySelector("#lab-chat");
    [askRow, chatBox].forEach(el => {
      if (!el) return;
      el.addEventListener("dragover", (e) => { e.preventDefault(); e.stopPropagation(); });
      el.addEventListener("drop", (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
          addLabFiles(Array.from(e.dataTransfer.files));
        }
      });
    });
  }

  function notesBox() { return wrap.querySelector("#lab-notes"); }

  function saveNotes(quiet) {
    const box = notesBox();
    if (!box || !data) return Promise.resolve(false);
    if (box.value.trim() === (data.notes || "").trim()) {
      if (!quiet) flash("#lab-save", "Saved ✓");
      return Promise.resolve(true);
    }
    const notes = box.value;
    return post("/api/lablog/save_notes", { track, day, notes }).then(res => {
      if (res && res.ok) {
        data.notes = notes;
        if (res.changed) {
          data.review = "";
          const rv = wrap.querySelector("#lab-review");
          if (rv) rv.textContent = "Notes saved. Press “Assess my log” for a new review.";
        }
        if (!quiet) flash("#lab-save", "Saved ✓");
        return true;
      }
      return false;
    });
  }

  function flash(sel, text) {
    const b = wrap.querySelector(sel);
    if (!b) return;
    const old = b.textContent;
    b.textContent = text;
    setTimeout(() => (b.textContent = old), 1400);
  }

  function assess() {
    const btn = wrap.querySelector("#lab-assess");
    const rv = wrap.querySelector("#lab-review");
    btn.disabled = true;
    btn.textContent = "Checking…";
    rv.textContent = "Reading your lab log…";
    const box = notesBox();
    post("/api/lablog/assess", { track, day, notes: box ? box.value : undefined }).then(res => {
      btn.disabled = false;
      btn.textContent = "Assess my log";
      if (res && res.ok) {
        data.notes = box ? box.value : data.notes;
        rv.textContent = res.review || "Done.";
        load();
      } else {
        rv.textContent = (res && res.error) || "I couldn't review it.";
      }
    }).catch(err => { btn.disabled = false; btn.textContent = "Assess my log"; rv.textContent = "Error: " + err.message; });
  }

  // ---------- Ask Vega about this day
  function renderChat(pending, pendingAttachments) {
    const box = wrap.querySelector("#lab-chat");
    if (!box) return;
    const h = history[key()] || [];
    if (!h.length && !pending) {
      box.innerHTML = `<div class="lab-muted">Press a button above, attach a file/screenshot, or type a question. Answers stay here in the Lab Log.</div>`;
      return;
    }
    box.innerHTML = h.map(([q, a, atts], n) => `
      <div class="lab-q">
        <b>You</b>
        ${renderAttachmentsHtml(atts)}
        <div>${esc(q)}</div>
      </div>
      <div class="lab-a">
        <b>Vega</b>
        <div class="message-body">${md(a)}</div>
        <button class="msg-action-btn" data-copy="${n}">Copy answer</button>
      </div>`).join("")
      + (pending ? `
      <div class="lab-q">
        <b>You</b>
        ${renderAttachmentsHtml(pendingAttachments)}
        <div>${esc(pending)}</div>
      </div>
      <div class="lab-a">
        <b>Vega</b>
        <div class="lab-muted">thinking… (inspecting lesson and attachments)</div>
      </div>` : "");
    box.querySelectorAll("[data-copy]").forEach(b => (b.onclick = () => copy(h[+b.dataset.copy][1], b)));
    box.scrollTop = box.scrollHeight;
  }

  function ask(question) {
    const inp = wrap.querySelector("#lab-ask-input");
    let q = (question || (inp ? inp.value : "")).trim();
    if ((!q && labAttachedItems.length === 0) || asking) return;
    if (labAttachedItems.some(item => !item.loaded || item.error)) {
      if (window.appendNotice) window.appendNotice("Wait for attached files to finish reading. Remove and reattach any file that failed to read.", "warn");
      return;
    }
    if (!question && inp) inp.value = "";

    const currentAttachments = [...labAttachedItems];
    labAttachedItems = [];
    renderLabAttachmentPills();

    if (!q) {
      const imgCount = currentAttachments.filter(a => a.isImage).length;
      const fileCount = currentAttachments.length - imgCount;
      if (imgCount > 0 && fileCount === 0) {
        q = imgCount === 1 ? "Please look at this screenshot and help me with this lesson." : `Please look at these ${imgCount} screenshots and help me with this lesson.`;
      } else if (fileCount > 0 && imgCount === 0) {
        q = fileCount === 1 ? "Please review this attached file for this lesson." : `Please review these ${fileCount} attached files for this lesson.`;
      } else {
        q = "Please review the attached screenshots and files for this lesson.";
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

    let fullQuestion = q;
    if (fileTextBlocks.length > 0) {
      fullQuestion = fileTextBlocks.join("\n\n") + "\n\n" + q;
    }

    let shownQuestion = q;
    if (shownBadges.length > 0) {
      shownQuestion = shownBadges.join("  ") + "\n" + q;
    }

    const k = key();
    const quickName = (data.quick || []).find(x => x[0] === q);
    asking = true;
    renderChat(quickName ? quickName[1] : shownQuestion, currentAttachments);
    const askBtn = wrap.querySelector("#lab-ask-btn");
    if (askBtn) askBtn.disabled = true;

    saveNotes(true).then(() => post("/api/lablog/ask", {
      track,
      day,
      question: fullQuestion,
      history: (history[k] || []).map(item => [item[0], item[1]]).slice(-3),
      images: imagesForBackend
    }))
      .then(res => {
        asking = false;
        if (askBtn) askBtn.disabled = false;
        if (res && res.ok) {
          (history[k] = history[k] || []).push([res.shown || shownQuestion, res.answer, currentAttachments]);
          history[k] = history[k].slice(-8);
        } else {
          (history[k] = history[k] || []).push([shownQuestion, (res && res.error) || "I couldn't answer.", currentAttachments]);
        }
        if (key() === k) renderChat();
      })
      .catch(err => {
        asking = false;
        if (askBtn) askBtn.disabled = false;
        (history[k] = history[k] || []).push([shownQuestion, "Error: " + err.message, currentAttachments]);
        renderChat();
      });
  }

  // ---------- How to learn this
  function renderLearn() {
    const box = wrap.querySelector("#lab-learn");
    const L = data.learn;
    if (!L) {
      box.innerHTML = `<div class="lab-muted">No sources for this day yet. Ask Vega above: “how do I learn this?”</div>`;
      return;
    }
    const link = (item) => {
      const [label, url] = item;
      return url ? `<a href="#" class="lab-link" data-url="${esc(url)}">${esc(label)}</a>` : `<span>${esc(label)}</span>`;
    };
    let html = "";
    if (L.course) html += `<div class="lab-card">Course: ${link(L.course)}</div>`;
    (L.topics || []).forEach((tp, n) => {
      html += `<div class="lab-card lab-topic"><div class="lab-topic-name">${track === "linux" ? `Topic ${n + 1}: ` : ""}${esc(tp.name)}</div>`;
      [["Watch", "watch"], ["Read", "read"], ["Practice (free, hands-on)", "practice"]].forEach(([t, k]) => {
        const items = tp[k] || [];
        if (items.length) html += `<div class="lab-sub">${t}</div><ul>${items.map(i => `<li>${link(i)}</li>`).join("")}</ul>`;
      });
      if (tp.ai_job) html += `<div class="lab-sub">How AI is used for this in the job</div><div class="lab-text">${esc(tp.ai_job)}</div>`;
      if ((tp.prompts || []).length) {
        html += `<div class="lab-sub">Ask AI (fill in any [paste] part first)</div>`;
        tp.prompts.forEach(p => {
          html += `<div class="lab-prompt"><span>“${esc(p)}”</span>
            <button class="mini-btn" data-pcopy="${esc(p)}">Copy</button>
            <button class="mini-btn" data-pask="${esc(p)}">Ask Vega</button></div>`;
        });
      }
      if ((tp.interview || []).length) {
        html += `<div class="lab-sub">Interview questions</div><ul>${tp.interview.map(q => `<li>${esc(q)}</li>`).join("")}</ul>`;
      }
      html += `</div>`;
    });
    if (track !== "linux") html += `<div class="lab-muted">Tip: use “How to use AI today” above to get ready-made AI prompts for this lesson.</div>`;
    box.innerHTML = html;
    box.querySelectorAll(".lab-link").forEach(a => (a.onclick = (e) => { e.preventDefault(); openUrl(a.dataset.url); }));
    box.querySelectorAll("[data-pcopy]").forEach(b => (b.onclick = () => copy(b.dataset.pcopy, b)));
    box.querySelectorAll("[data-pask]").forEach(b => (b.onclick = () => {
      const p = b.dataset.pask;
      const inp = wrap.querySelector("#lab-ask-input");
      if (p.includes("[")) {  // he must paste his own output first
        inp.value = p;
        inp.focus();
        inp.setSelectionRange(p.indexOf("["), p.indexOf("]") + 1);
        inp.scrollIntoView({ behavior: "smooth", block: "center" });
      } else {
        wrap.querySelector(".lab-ask-title").scrollIntoView({ behavior: "smooth", block: "start" });
        ask(p);
      }
    }));
  }

  // ---------- progress map
  function renderMap() {
    const box = wrap.querySelector("#lab-map");
    box.innerHTML = (data.map || []).map((m, i) => {
      const cls = i === day ? "cur" : m.done ? "done" : m.past ? "missed" : "";
      const state = m.done ? ", completed" : m.past ? ", missed" : "";
      return `<button type="button" class="lab-cell ${cls}" data-i="${i}" title="Day ${i + 1}${state}" aria-label="Day ${i + 1}${state}" aria-pressed="${i === day}"></button>`;
    }).join("");
    box.querySelectorAll(".lab-cell").forEach(c => (c.onclick = () => { saveNotes(true); day = +c.dataset.i; load(); }));
  }

  window.VegaLabLog = { open, close, isOpen: () => !page.classList.contains("hidden") };
})();
