/* Optional, explicitly-invoked UI helpers for the Vega-IDE spec workflow. */
(function () {
  "use strict";

  let nextInputId = 0;
  const spokenRunIds = new Set();

  function makeElement(tag, className, text) {
    if (typeof document === "undefined" || !document.createElement) return null;
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  }

  function splitSection(markdown, heading) {
    const lines = String(markdown || "").split(/\r?\n/);
    const headingIndex = lines.findIndex((line) => line.trim() === `## ${heading}`);
    if (headingIndex < 0) return [];
    const items = [];
    for (let index = headingIndex + 1; index < lines.length; index += 1) {
      const line = lines[index].trim();
      if (line.startsWith("## ")) break;
      if (line.startsWith("- ") && line !== "- Not specified.") items.push(line.slice(2));
    }
    return items;
  }

  function replaceSectionItems(markdown, heading, items) {
    const lines = String(markdown || "").split(/\r?\n/);
    const start = lines.findIndex((line) => line.trim() === `## ${heading}`);
    if (start < 0) return String(markdown || "");
    let end = start + 1;
    while (end < lines.length && !lines[end].trim().startsWith("## ")) end += 1;
    const content = items.length ? items.map((item) => `- ${item}`) : ["- Not specified."];
    lines.splice(start + 1, end - start - 1, ...content);
    return lines.join("\n");
  }

  function dispatch(name, detail) {
    if (typeof window === "undefined" || typeof window.dispatchEvent !== "function" || typeof CustomEvent === "undefined") return;
    window.dispatchEvent(new CustomEvent(name, { detail }));
  }

  function statusLabel(state) {
    const labels = {
      drafting: "Drafting",
      awaiting_approval: "Awaiting approval",
      running: "Running in Vega-IDE",
      completed: "Completed",
      failed: "Failed"
    };
    return labels[state && state.status] || "Waiting for status";
  }

  function liveVoiceIsAvailable() {
    if (typeof window === "undefined" || typeof document === "undefined") return false;
    if (document.visibilityState !== "visible" || document.hidden) return false;
    if (window.vegaActiveVoiceSurface !== "chat" || window.appIsLocked) return false;
    if (typeof window.isUnlocked === "function" && !window.isUnlocked()) return false;
    const voiceToggle = document.getElementById("voice-toggle-check");
    const speakingButton = document.querySelector("#speaking-toggle-btn.on, .speaking-toggle-btn.on, [data-action='toggle-speaking'].on");
    const hud = window.arcReactorHUD;
    return !!(voiceToggle && voiceToggle.checked && speakingButton && hud
      && hud.state === "idle" && !hud.isSpeaking && !hud.isSleeping
      && typeof hud.synthesizeSpeech === "function");
  }

  function speakDebriefOnce(state, debrief) {
    if (!state || !state.run_id || !debrief || debrief.should_speak !== true) return;
    if (spokenRunIds.has(state.run_id) || !liveVoiceIsAvailable()) return;
    const summary = String(debrief.summary_text || "").trim();
    if (!summary) return;
    spokenRunIds.add(state.run_id);
    Promise.resolve(window.arcReactorHUD.synthesizeSpeech(summary.slice(0, 500))).catch(() => {});
  }

  function renderRunStatus(panel, state, debrief) {
    if (!panel || !state) return;
    panel.replaceChildren();
    const label = makeElement("span", `spec-run-badge ${state.status || "unknown"}`, statusLabel(state));
    const summaryText = (debrief && debrief.summary_text) || state.summary_text || state.check_summary || state.reason || "";
    const summary = makeElement("p", "spec-run-summary", summaryText);
    if (label) panel.appendChild(label);
    if (summary) panel.appendChild(summary);
    if (state.check_summary && state.check_summary !== summaryText) {
      const checks = makeElement("p", "spec-run-checks", `Checks: ${state.check_summary}`);
      if (checks) panel.appendChild(checks);
    }
    if (Array.isArray(state.changed_files) && state.changed_files.length) {
      const heading = makeElement("p", "spec-run-files-heading", "Changed files");
      const list = makeElement("ul", "spec-run-files");
      state.changed_files.slice(0, 30).forEach((file) => {
        const item = makeElement("li", "", file);
        if (item && list) list.appendChild(item);
      });
      if (heading) panel.appendChild(heading);
      if (list) panel.appendChild(list);
    }
    if (Array.isArray(state.checks) && state.checks.length) {
      const checks = makeElement("ul", "spec-run-check-list");
      state.checks.slice(0, 30).forEach((check) => {
        if (!check || typeof check !== "object") return;
        const label = [check.status || "checked", check.file || "", check.check || ""].filter(Boolean).join(" · ");
        const item = makeElement("li", "", label);
        if (item && checks) checks.appendChild(item);
      });
      if (checks) panel.appendChild(checks);
    }
    if (state.rolled_back === true) {
      const rollback = makeElement("p", "spec-run-checks", "Changes rolled back; the workspace is restored.");
      if (rollback) panel.appendChild(rollback);
    }
  }

  async function postJson(path, body) {
    if (typeof window === "undefined" || typeof window.fetch !== "function") return null;
    try {
      const response = await window.fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(body || {})
      });
      return await response.json();
    } catch (_) {
      return null;
    }
  }

  async function getJson(path) {
    if (typeof window === "undefined" || typeof window.fetch !== "function") return null;
    try {
      const response = await window.fetch(path, { method: "GET", credentials: "same-origin" });
      return await response.json();
    } catch (_) {
      return null;
    }
  }

  function renderDraftSpecCard(container, payload, transcript, callbacks) {
    if (!payload || typeof document === "undefined" || !document.createElement) return null;
    let target = container;
    if (typeof target === "string") {
      if (!document.getElementById) return null;
      target = document.getElementById(target);
    }
    if (!target || typeof target.appendChild !== "function") return null;

    const options = callbacks && typeof callbacks === "object" ? callbacks : {};
    let currentRun = null;
    const markdown = typeof payload.readme_spec_markdown === "string" ? payload.readme_spec_markdown : "";
    let currentPayload = payload;
    const card = makeElement("section", "spec-draft-card modal-card");
    if (!card) return null;
    card.setAttribute("aria-label", "Draft feature specification");
    card.dataset.approvedByUser = String(payload.approved_by_user === true);

    const header = makeElement("div", "modal-header");
    const heading = makeElement("h2", "", `Draft Spec: ${payload.title || "Feature Request"}`);
    const version = makeElement("span", "spec-version-badge", `v${Number(payload.spec_version) || 1}`);
    if (header && heading && version) {
      header.appendChild(heading);
      header.appendChild(version);
      card.appendChild(header);
    }

    const note = makeElement("p", "spec-transcript-note");
    const cleanup = String(payload.transcript_cleanup_note || "No transcript cleanup was needed.");
    const interpreted = splitSection(markdown, "Goal").join(" ") || payload.title || "Feature Request";
    note.textContent = `Transcript: ${String(transcript || "(not provided)")}\nInterpreted: ${interpreted}\nCleanup: ${cleanup}`;
    card.appendChild(note);

    const goalSection = makeElement("div", "spec-scope-section");
    const goalHeading = makeElement("h3", "", "Goal");
    const goalText = makeElement("p", "", splitSection(markdown, "Goal").join(" ") || payload.title || "Feature Request");
    if (goalSection && goalHeading && goalText) {
      goalSection.appendChild(goalHeading);
      goalSection.appendChild(goalText);
      card.appendChild(goalSection);
    }
    const approvalBadge = makeElement("span", "spec-approval-badge", payload.approved_by_user === true ? "Approved by user" : "Not approved");
    if (approvalBadge) {
      approvalBadge.dataset.approved = String(payload.approved_by_user === true);
      card.appendChild(approvalBadge);
    }

    const contextBadge = makeElement(
      "span",
      payload.ide_context && payload.ide_context.ide_online ? "spec-context-badge online" : "spec-context-badge offline",
      payload.ide_context && payload.ide_context.ide_online ? "IDE context connected" : "IDE context unavailable"
    );
    if (contextBadge) card.appendChild(contextBadge);

    const runPanel = makeElement("section", "spec-run-panel");
    if (runPanel) {
      runPanel.setAttribute("aria-label", "Vega-IDE handoff status");
      runPanel.setAttribute("aria-live", "polite");
      card.appendChild(runPanel);
    }
    let stopWatching = null;

    const inScope = makeElement("div", "spec-scope-section");
    const inHeading = makeElement("h3", "", "In Scope");
    const inList = makeElement("ul", "");
    splitSection(markdown, "In Scope (This Pass)").forEach((item) => {
      const row = makeElement("li", "", item);
      if (row && inList) inList.appendChild(row);
    });
    if (inScope && inHeading && inList) {
      inScope.appendChild(inHeading);
      inScope.appendChild(inList);
      card.appendChild(inScope);
    }

    const outScope = makeElement("div", "spec-scope-section");
    const outHeading = makeElement("h3", "", "Out of Scope");
    const outList = makeElement("ul", "");
    splitSection(markdown, "Out of Scope / Non-Goals").forEach((item) => {
      const row = makeElement("li", "", item);
      if (row && outList) outList.appendChild(row);
    });
    if (outScope && outHeading && outList) {
      outScope.appendChild(outHeading);
      outScope.appendChild(outList);
      card.appendChild(outScope);
    }

    const details = makeElement("details", "spec-markdown-details");
    const summary = makeElement("summary", "", "README Markdown preview");
    const preview = makeElement("pre", "spec-markdown-preview", markdown);
    if (details && summary && preview) {
      details.appendChild(summary);
      details.appendChild(preview);
      card.appendChild(details);
    }

    const label = makeElement("label", "", "Revision feedback");
    const feedback = makeElement("textarea", "spec-revision-feedback");
    const inputId = `vega-spec-revision-feedback-${++nextInputId}`;
    if (label && feedback) {
      label.setAttribute("for", inputId);
      feedback.id = inputId;
      feedback.name = inputId;
      feedback.rows = 3;
      feedback.setAttribute("aria-label", "Revision feedback");
      card.appendChild(label);
      card.appendChild(feedback);
    }

    const targetLabel = makeElement("label", "", "Target files (one workspace-relative path per line)");
    const targets = makeElement("textarea", "spec-target-files", splitSection(markdown, "Target Files & Interfaces").join("\n"));
    const targetId = `vega-spec-target-files-${++nextInputId}`;
    if (targetLabel && targets) {
      targetLabel.setAttribute("for", targetId);
      targets.id = targetId;
      targets.name = targetId;
      targets.rows = 2;
      targets.setAttribute("aria-label", "Target files, one workspace-relative path per line");
      card.appendChild(targetLabel);
      card.appendChild(targets);
    }

    const operationsLabel = makeElement("label", "", "Proposed file operations (JSON)");
    const operations = makeElement("textarea", "spec-file-operations", JSON.stringify(
      Array.isArray(payload.file_operations) ? payload.file_operations : [], null, 2
    ));
    const operationsId = `vega-spec-file-operations-${++nextInputId}`;
    if (operationsLabel && operations) {
      operationsLabel.setAttribute("for", operationsId);
      operations.id = operationsId;
      operations.name = operationsId;
      operations.rows = 5;
      operations.spellcheck = false;
      operations.setAttribute("aria-label", "Proposed file operations as JSON");
      card.appendChild(operationsLabel);
      card.appendChild(operations);
    }
    const reviewError = makeElement("p", "spec-review-error");
    if (reviewError) {
      reviewError.hidden = true;
      reviewError.setAttribute("role", "alert");
      card.appendChild(reviewError);
    }

    const collectReviewedPayload = () => {
      try {
        const operationList = JSON.parse(operations ? operations.value : "[]");
        if (!Array.isArray(operationList)) throw new Error("File operations must be a JSON array.");
        const targetPaths = (targets ? targets.value : "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
        const updatedMarkdown = replaceSectionItems(currentPayload.readme_spec_markdown, "Target Files & Interfaces", targetPaths);
        if (!targetPaths.length) throw new Error("List at least one target file from the active workspace.");
        if (reviewError) reviewError.hidden = true;
        return { ...currentPayload, readme_spec_markdown: updatedMarkdown, file_operations: operationList };
      } catch (error) {
        if (reviewError) {
          reviewError.textContent = error instanceof SyntaxError ? "File operations must contain valid JSON." : String(error.message || "Review fields are invalid.");
          reviewError.hidden = false;
        }
        return null;
      }
    };

    const actions = makeElement("div", "spec-card-actions");
    const revise = makeElement("button", "modal-action-btn", "Revise");
    const planOnly = makeElement("button", "modal-action-btn", "Run Plan Only");
    const approve = makeElement("button", "modal-action-btn", "Approve & Apply");
    const reject = makeElement("button", "modal-action-btn", "Reject");
    const cancel = makeElement("button", "modal-action-btn", "Cancel Handoff");
    if (cancel) {
      cancel.type = "button";
      cancel.hidden = true;
      cancel.addEventListener("click", async () => {
        cancel.disabled = true;
        const run = currentRun && currentRun.spec_id === payload.spec_id ? currentRun : null;
        if (!run) return;
        const result = await api.cancelSpec(run.spec_id, run.run_id);
        if (result && result.ok) {
          renderRunStatus(runPanel, result, null);
          cancel.disabled = true;
        }
      });
      if (actions) actions.appendChild(cancel);
    }
    const showRunResult = (result) => {
      if (!result || !result.ok || !result.spec_id || !result.run_id) return result;
      currentRun = { spec_id: result.spec_id, run_id: result.run_id };
      if (runPanel) renderRunStatus(runPanel, result, null);
      if (cancel) {
        cancel.hidden = result.status !== "running";
        cancel.disabled = false;
      }
      if (typeof stopWatching === "function") stopWatching();
      stopWatching = api.watchSpecStatus(result.spec_id, result.run_id, (snapshot) => {
        const state = snapshot && snapshot.state;
        if (!state || state.run_id !== result.run_id) return;
        if (runPanel) renderRunStatus(runPanel, state, snapshot.debrief);
        if (cancel) {
          cancel.hidden = state.status !== "running";
          cancel.disabled = false;
        }
        if (state.status === "completed" || state.status === "failed") speakDebriefOnce(state, snapshot.debrief);
      });
      return result;
    };
    if (revise && actions) {
      revise.type = "button";
      revise.addEventListener("click", async () => {
        const reviewedPayload = collectReviewedPayload();
        if (!reviewedPayload) return;
        const handler = options.onRevise;
        let result;
        if (typeof handler === "function") {
          try { result = await handler(reviewedPayload, feedback ? feedback.value : ""); } catch (_) { result = null; }
        } else result = await api.reviseSpec(reviewedPayload, feedback ? feedback.value : "");
        dispatch("vega:spec:revised", result);
        if (result && result.ok && result.payload) {
          const holder = makeElement("div", "");
          const replacement = renderDraftSpecCard(holder, result.payload, transcript, options);
          if (replacement) card.replaceWith(replacement);
        } else if (reviewError) {
          reviewError.textContent = result && result.error || "The spec could not be revised.";
          reviewError.hidden = false;
        }
      });
      actions.appendChild(revise);
    }
    if (approve && actions) {
      approve.type = "button";
      approve.addEventListener("click", async () => {
        const reviewedPayload = collectReviewedPayload();
        if (!reviewedPayload) return;
        const handler = options.onApprove;
        let result;
        try {
          result = typeof handler === "function"
            ? await handler(reviewedPayload)
            : await api.approveAndDispatch(reviewedPayload);
        } catch (_) { result = null; }
        dispatch("vega:spec:approval-result", result);
        if (result && result.ok) showRunResult(result);
        else if (reviewError) {
          reviewError.textContent = result && (result.error || result.reason) || "The approved handoff could not be started.";
          reviewError.hidden = false;
        }
      });
      actions.appendChild(approve);
    }
    if (planOnly && actions) {
      planOnly.type = "button";
      planOnly.addEventListener("click", () => {
        const reviewedPayload = collectReviewedPayload();
        if (!reviewedPayload) return;
        const handler = options.onPlanOnly;
        if (typeof handler === "function") Promise.resolve().then(() => handler(reviewedPayload)).catch(() => {});
        else api.dispatchSpec({
          ...reviewedPayload,
          approved_by_user: false,
          approval_tier: "read_only",
          mode: "plan_only",
          status: "running"
        }).then((result) => {
          dispatch("vega:spec:plan-result", result);
          showRunResult(result);
        });
      });
      actions.insertBefore(planOnly, approve || null);
    }
    if (reject && actions) {
      reject.type = "button";
      reject.addEventListener("click", () => {
        const reviewedPayload = collectReviewedPayload();
        if (!reviewedPayload) return;
        const handler = options.onReject;
        if (typeof handler === "function") Promise.resolve().then(() => handler(reviewedPayload)).catch(() => {});
        else api.approveSpec(reviewedPayload, false).then((result) => dispatch("vega:spec:rejection-result", result));
      });
      actions.appendChild(reject);
    }
    if (actions) card.appendChild(actions);
    target.appendChild(card);
    return card;
  }

  const api = {
    async compileTranscript(transcript, options) {
      const settings = options && typeof options === "object" ? options : {};
      const context = settings.ide_context || await getJson("/api/spec/ide-context") || {};
      return postJson("/api/spec/compile", {
        transcript: String(transcript || ""),
        title: settings.title || "",
        workspace_root: settings.workspace_root || context.workspace_root || "",
        ide_context: context
      });
    },
    reviseSpec: (payload, feedback) => postJson("/api/spec/revise", { payload, feedback: String(feedback || "") }),
    approveSpec: (payload, approved) => postJson("/api/spec/approve", {
      payload,
      approved: approved === true,
      approval_tier: payload && payload.approval_tier ? payload.approval_tier : "apply_edits"
    }),
    dispatchSpec: (payload) => postJson("/api/spec/dispatch", { payload }),
    cancelSpec: (specId, runId) => postJson("/api/spec/cancel", { spec_id: specId, run_id: runId }),
    fetchSpecStatus: (specId) => getJson(`/api/spec/status/${encodeURIComponent(specId)}`),
    async approveAndDispatch(payload) {
      const applyPayload = { ...payload, approval_tier: "apply_edits", mode: "plan_and_apply" };
      const approval = await api.approveSpec(applyPayload, true);
      if (!approval || approval.ok !== true || !approval.payload) return approval || { ok: false, error: "approval_failed" };
      return api.dispatchSpec(approval.payload);
    },
    watchSpecStatus(specId, runId, onUpdate, intervalMs) {
      if (typeof window === "undefined" || typeof window.setInterval !== "function") return () => {};
      let stopped = false;
      let busy = false;
      const poll = async () => {
        if (stopped || busy) return;
        busy = true;
        try {
          const snapshot = await api.fetchSpecStatus(specId);
          if (snapshot && snapshot.ok && snapshot.state && snapshot.state.run_id === runId) {
            if (typeof onUpdate === "function") onUpdate(snapshot);
            if (snapshot.state.status === "completed" || snapshot.state.status === "failed") stop();
          }
        } finally {
          busy = false;
        }
      };
      const timer = window.setInterval(poll, Math.max(500, Number(intervalMs) || 1200));
      const stop = () => {
        stopped = true;
        window.clearInterval(timer);
      };
      poll();
      return stop;
    },
    fetchIdeContext: () => getJson("/api/spec/ide-context"),
    renderDraftSpecCard,
    splitSection
  };

  if (typeof window !== "undefined") window.VegaSpecCompiler = api;
})();

