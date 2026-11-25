/**
 * Vega v2.2 — Career Workbench Module
 * Implements:
 * 1. Feed & Scanner (Public ATS Greenhouse/Lever/Ashby/Remotive/RemoteOK + Adzuna India manual + Ingests)
 * 2. Job Inspector & Resume Tailor (Scam Shield audit breakdown + 1-page ATS PDF + Outreach & Referral Drafter)
 * 3. Application Tracker (SQLite-backed pipeline with status management)
 * 4. Profile Vault (Fact-locked structured form editor, JSON viewer & PDF import)
 * 5. Read-Only Gmail Label Synchronizer & Application Update Tracker
 */

(function () {
  let activeJobId = null;
  let activeResumeData = null;
  let cachedJobs = [];
  let cachedProfile = null;
  let currentFilter = "promising";
  let locationFilter = "all"; // "all" | "india" | "worldwide_remote"
  let indiaRemoteOnly = false; // synced with (locationFilter === "india") for compatibility
  let activeOutreachTab = "cover";
  let activeSkillDecisions = {};
  let activeSelectedProjects = [];

  // Classification helpers for strict Internships, Fresher/Entry, India Only, and Worldwide Remote
  const INTERN_TITLE_REGEX = /\b(intern|internship|summer intern|winter intern|industrial trainee|co-op|coop|apprentice)\b/i;
  const SENIOR_EXCLUDE_REGEX = /\b(senior|sr\.?|staff|principal|lead|manager|director|architect|head of|vp)\b/i;

  const FRESHER_TITLE_REGEX = /\b(fresher|freshers|entry[\s-]?level|new grad|graduate|grad engineer|junior|jr\.?|associate|trainee|sde[\s-]?1|sde[\s-]?i|engineer[\s-]?i|l1|0[\s-]?1\s*y|0[\s-]?2\s*y)\b/i;
  const FRESHER_EXCLUDE_REGEX = /\b(senior|sr\.?|staff|principal|lead|manager|director|architect|distinguished|3\+|4\+|5\+|6\+|7\+|8\+|10\+)\b/i;

  const INDIA_ONLY_REGEX = /\b(india|bengaluru|bangalore|hyderabad|chennai|pune|mumbai|delhi|ncr|noida|gurugram|gurgaon|kolkata|ahmedabad|kochi|coimbatore|jaipur|indore|visakhapatnam|vizag|pan[- ]?india)\b/i;
  const WORLDWIDE_REMOTE_INDICATOR_REGEX = /\b(worldwide|global remote|anywhere|remote\s*-\s*global)\b/i;
  const NON_INDIA_REGION_RESTRICTION_REGEX = /\b(us|u\.s\.|usa|united states|uk|u\.k\.|united kingdom|london|france|paris|germany|berlin|munich|bavaria|estonia|ireland|canada|emea|americas|europe|switzerland|zurich|zürich|singapore|japan|tokyo|china|beijing|taiwan|taipei|san francisco|new york|austin|seattle|boston|denver|palo alto|washington)\b/i;

  function isInternshipJob(job) {
    if (!job) return false;
    if (job.scam_risk === "HIGH_RISK" || job.status === "Ignored_Scam") return false;
    const role = (job.role || "").trim();
    if (SENIOR_EXCLUDE_REGEX.test(role)) return false;
    const jobType = (job.job_type || "").trim();
    return INTERN_TITLE_REGEX.test(role) || INTERN_TITLE_REGEX.test(jobType);
  }

  function isFresherEntryJob(job) {
    if (!job) return false;
    if (job.scam_risk === "HIGH_RISK" || job.status === "Ignored_Scam") return false;
    const role = (job.role || "").trim();
    if (FRESHER_EXCLUDE_REGEX.test(role)) return false;

    // Strict separation: keep Internships strictly for intern/co-op roles
    if (INTERN_TITLE_REGEX.test(role) || INTERN_TITLE_REGEX.test(job.job_type || "")) return false;

    if (FRESHER_TITLE_REGEX.test(role)) return true;

    const raw = (job.raw_text || "").trim();
    if (raw) {
      const explicitEntry = /\b(0[\s-]?1\s*years?|0[\s-]?2\s*years?|entry[\s-]?level|fresher|new grad|batch of 202[4-7])\b/i.test(raw);
      const highExp = /\b([3-9]\+|1\d\+)\s*years?\b/i.test(raw) || FRESHER_EXCLUDE_REGEX.test(raw);
      if (explicitEntry && !highExp) return true;
    }
    return false;
  }

  function isIndiaOnlyJob(job) {
    if (!job) return false;
    const loc = (job.location || "").toLowerCase().trim();
    const role = (job.role || job.title || "").toLowerCase().trim();
    const combined = (loc + " " + role).trim();
    if (!combined) return false;
    return INDIA_ONLY_REGEX.test(combined);
  }

  function isWorldwideRemoteJob(job) {
    if (!job) return false;
    const loc = (job.location || "").toLowerCase().trim();
    const role = (job.role || job.title || "").toLowerCase().trim();
    const combined = (loc + " " + role).trim();
    if (!combined) return false;

    // Must have unrestricted global remote indicator
    if (!WORLDWIDE_REMOTE_INDICATOR_REGEX.test(combined)) {
      return false;
    }

    // If India is explicitly mentioned, it's allowed
    if (INDIA_ONLY_REGEX.test(combined)) {
      return true;
    }

    // Strictly reject if location OR title restricts it to a non-India country/region
    if (NON_INDIA_REGION_RESTRICTION_REGEX.test(loc) || NON_INDIA_REGION_RESTRICTION_REGEX.test(role)) {
      return false;
    }

    return true;
  }

  function matchesLocationFilter(job) {
    if (locationFilter === "india") {
      return isIndiaOnlyJob(job);
    }
    if (locationFilter === "worldwide_remote") {
      return isWorldwideRemoteJob(job);
    }
    return true;
  }

  function isIndiaOrRemoteJob(job) {
    return isIndiaOnlyJob(job) || isWorldwideRemoteJob(job);
  }

  // DOM Sub-view Elements
  let subtabFeed, subtabInspector, subtabTracker, subtabVault;
  let viewFeed, viewInspector, viewTracker, viewVault;

  function initCareerModule() {
    subtabFeed = document.getElementById("career-tab-feed");
    subtabInspector = document.getElementById("career-tab-inspector");
    subtabTracker = document.getElementById("career-tab-tracker");
    subtabVault = document.getElementById("career-tab-vault");

    viewFeed = document.getElementById("career-view-feed");
    viewInspector = document.getElementById("career-view-inspector");
    viewTracker = document.getElementById("career-view-tracker");
    viewVault = document.getElementById("career-view-vault");

    if (subtabFeed) subtabFeed.addEventListener("click", () => switchCareerSubtab("feed"));
    if (subtabInspector) subtabInspector.addEventListener("click", () => switchCareerSubtab("inspector"));
    if (subtabTracker) subtabTracker.addEventListener("click", () => switchCareerSubtab("tracker"));
    if (subtabVault) subtabVault.addEventListener("click", () => switchCareerSubtab("vault"));

    // Feed actions & Filter Pills
    const scanBtn = document.getElementById("career-scan-btn");
    if (scanBtn) scanBtn.addEventListener("click", () => handleScanFeeds(false));

    const analyzeBtn = document.getElementById("career-analyze-btn");
    if (analyzeBtn) analyzeBtn.addEventListener("click", handleAnalyzeInput);

    const jdPdfInput = document.getElementById("career-jd-pdf-input");
    if (jdPdfInput) jdPdfInput.addEventListener("change", handleJdPdfUpload);

    const filterPills = document.querySelectorAll(".career-filter-pill:not(.toggle-pill)");
    filterPills.forEach((pill) => {
      pill.addEventListener("click", () => {
        filterPills.forEach((p) => p.classList.remove("active"));
        pill.classList.add("active");
        currentFilter = pill.dataset.filter || "promising";
        renderFeedCards(cachedJobs);
      });
    });

    const indiaToggle = document.getElementById("career-india-toggle");
    const worldwideToggle = document.getElementById("career-worldwide-remote-toggle");

    function setLocationFilter(mode) {
      if (locationFilter === mode) {
        locationFilter = "all";
      } else {
        locationFilter = mode;
      }
      indiaRemoteOnly = (locationFilter === "india");
      if (indiaToggle) indiaToggle.classList.toggle("active", locationFilter === "india");
      if (worldwideToggle) worldwideToggle.classList.toggle("active", locationFilter === "worldwide_remote");
      renderFeedCards(cachedJobs);
    }

    if (indiaToggle) {
      indiaToggle.addEventListener("click", () => setLocationFilter("india"));
    }
    if (worldwideToggle) {
      worldwideToggle.addEventListener("click", () => setLocationFilter("worldwide_remote"));
    }

    // Inspector actions
    const genResumeBtn = document.getElementById("career-gen-resume-btn");
    if (genResumeBtn) genResumeBtn.addEventListener("click", handleGenerateResume);

    const draftOutreachBtn = document.getElementById("career-draft-outreach-btn");
    if (draftOutreachBtn) draftOutreachBtn.addEventListener("click", handleDraftOutreach);

    const copyNoteBtn = document.getElementById("career-copy-note-btn");
    if (copyNoteBtn) copyNoteBtn.addEventListener("click", handleCopyCoverNote);

    const openApplyBtn = document.getElementById("career-open-apply-btn");
    if (openApplyBtn) openApplyBtn.addEventListener("click", handleOpenApplyLink);

    const startMockBtn = document.getElementById("career-start-mock-btn");
    if (startMockBtn) {
      startMockBtn.addEventListener("click", () => {
        if (activeJobId) {
          startMockInterview(activeJobId);
        }
      });
    }

    // Outreach subtabs
    const outreachTabs = document.querySelectorAll("[data-outreach-tab]");
    outreachTabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        switchOutreachTab(tab.dataset.outreachTab);
      });
    });

    // Vault actions
    const saveProfileBtn = document.getElementById("career-save-profile-btn");
    if (saveProfileBtn) saveProfileBtn.addEventListener("click", handleSaveProfile);

    const importResumeBtn = document.getElementById("career-import-resume-btn");
    const importResumeInput = document.getElementById("career-import-resume-input");
    if (importResumeBtn && importResumeInput) {
      importResumeBtn.addEventListener("click", () => {
        importResumeInput.value = "";
        importResumeInput.click();
      });
    }
    if (importResumeInput) importResumeInput.addEventListener("change", handleImportResumePdf);

    const toggleRawBtn = document.getElementById("vault-toggle-raw-btn");
    const rawContainer = document.getElementById("vault-raw-container");
    if (toggleRawBtn && rawContainer) {
      toggleRawBtn.addEventListener("click", () => {
        rawContainer.classList.toggle("hidden");
      });
    }

    // GitHub Portfolio Sync
    const ghSyncBtn = document.getElementById("career-github-sync-btn");
    if (ghSyncBtn) ghSyncBtn.addEventListener("click", handleSyncGitHubRepos);

    const hydrateCareerData = async () => {
      clearStaleLockScanNotice();
      initGmailSyncBar();
      loadGitHubRepos();
      await loadJobs();
      loadProfile();
      checkAutoScan();
      const compEl = document.getElementById("inspector-company");
      if (compEl && (compEl.textContent === "Select a Job" || compEl.textContent.trim() === "") && cachedJobs && cachedJobs.length > 0) {
        inspectJob(cachedJobs[0].id);
      }
    };

    const clearStaleLockScanNotice = () => {
      const scanStatus = document.getElementById("career-scan-status");
      if (scanStatus && scanStatus.textContent && scanStatus.textContent.toLowerCase().includes("locked")) {
        scanStatus.textContent = "";
      }
    };

    window.addEventListener("vega:unlocked", () => {
      clearStaleLockScanNotice();
      hydrateCareerData();
    });

    if (window.isUnlocked && !window.isUnlocked()) {
      clearStaleLockScanNotice();
    } else {
      hydrateCareerData();
    }
  }

  function switchCareerSubtab(tabName) {
    const tabs = [
      { name: "feed", btn: subtabFeed, view: viewFeed },
      { name: "inspector", btn: subtabInspector, view: viewInspector },
      { name: "tracker", btn: subtabTracker, view: viewTracker },
      { name: "vault", btn: subtabVault, view: viewVault },
    ];

    tabs.forEach((t) => {
      if (t.btn) {
        t.btn.classList.toggle("active", t.name === tabName);
        t.btn.setAttribute("aria-selected", String(t.name === tabName));
      }
      if (t.view) {
        t.view.classList.toggle("hidden", t.name !== tabName);
      }
    });

    if (tabName === "tracker") {
      loadJobs();
    } else if (tabName === "vault") {
      loadProfile();
    }
  }

  // ==========================================
  // 1. FEED & SCANNER
  // ==========================================
  async function loadJobs() {
    try {
      const res = await fetch("/api/career/jobs");
      const data = await res.json();
      if (data.status === "ok") {
        cachedJobs = data.jobs || [];
        renderFeedCards(cachedJobs);
        renderTrackerTable(cachedJobs);
        renderMetrics(data.metrics || {});
      }
    } catch (err) {
      console.warn("Failed to load career jobs:", err);
    }
  }

  function checkAutoScan() {
    try {
      if (window.isUnlocked && !window.isUnlocked()) return;
      const lastScan = parseInt(localStorage.getItem("vega_last_feed_scan_ts") || "0", 10);
      const now = Date.now();
      const sixHours = 6 * 3600 * 1000;
      if (cachedJobs.length === 0 || now - lastScan > sixHours) {
        console.log("Triggering periodic background feed scan (free ATS feeds)...");
        handleScanFeeds(true);
      }
    } catch (e) {
      console.warn("Auto-scan check error:", e);
    }
  }

  async function handleScanFeeds(isBackground = false) {
    if (window.isUnlocked && !window.isUnlocked()) {
      if (!isBackground) {
        const scanStatus = document.getElementById("career-scan-status");
        if (scanStatus) {
          scanStatus.innerHTML = `Scan notice: Vega is locked. <button type="button" class="mini-btn unlock-vega-btn" style="margin-left:6px;" onclick="window.showLockScreen && window.showLockScreen()">Unlock Vega</button>`;
        }
      }
      return;
    }
    const scanBtn = document.getElementById("career-scan-btn");
    const scanStatus = document.getElementById("career-scan-status");
    const adzunaCheckbox = document.getElementById("career-include-adzuna");
    const includeAdzuna = isBackground ? false : Boolean(adzunaCheckbox && adzunaCheckbox.checked);

    if (scanBtn && !isBackground) {
      scanBtn.disabled = true;
      scanBtn.innerHTML = `<span>Scanning...</span>`;
    }
    if (scanStatus) {
      scanStatus.textContent = includeAdzuna
        ? "Querying Greenhouse, Lever, Ashby, Remotive, RemoteOK, and Adzuna India..."
        : "Querying public ATS feeds (Greenhouse, Lever, Ashby, Remotive, RemoteOK)...";
    }

    try {
      const res = await fetch("/api/career/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ include_adzuna: includeAdzuna })
      });
      const data = await res.json();
      if (data.status === "ok") {
        cachedJobs = data.jobs || [];
        renderFeedCards(cachedJobs);
        renderTrackerTable(cachedJobs);
        renderMetrics(data.metrics || {});
        localStorage.setItem("vega_last_feed_scan_ts", Date.now().toString());
        if (scanStatus) {
          scanStatus.textContent = `Discovered ${data.total_discovered || 0} roles. ${data.notice || ""}`;
        }
      } else if (data.status === "already_running") {
        if (scanStatus) scanStatus.textContent = `Notice: ${data.notice || "Feed scan already running in background."}`;
      } else {
        const errMsg = data.error || "No new jobs found";
        if (scanStatus) {
          if (errMsg.toLowerCase().includes("locked")) {
            scanStatus.innerHTML = `Scan notice: Vega is locked. <button type="button" class="mini-btn unlock-vega-btn" style="margin-left:6px;" onclick="window.showLockScreen && window.showLockScreen()">Unlock Vega</button>`;
          } else {
            scanStatus.textContent = `Scan notice: ${errMsg}`;
          }
        }
      }
    } catch (err) {
      if (scanStatus) scanStatus.textContent = `Scan failed: ${err.message}`;
    } finally {
      if (scanBtn && !isBackground) {
        scanBtn.disabled = false;
        scanBtn.innerHTML = `
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 21h5v-5"/></svg>
          <span>Scan Feeds</span>
        `;
      }
    }
  }

  async function handleAnalyzeInput() {
    const urlInput = document.getElementById("career-url-input");
    const textInput = document.getElementById("career-text-input");
    const statusBox = document.getElementById("career-analyze-status");

    const url = urlInput ? urlInput.value.trim() : "";
    const text = textInput ? textInput.value.trim() : "";

    if (!url && !text) {
      if (statusBox) statusBox.textContent = "Please paste a job URL or description text.";
      return;
    }

    if (statusBox) statusBox.textContent = "Running Scam Shield first, then matching skills...";
    try {
      const res = await fetch("/api/career/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, text })
      });
      const data = await res.json();
      if (data.status === "ok") {
        const job = data.job;
        if (statusBox) {
          statusBox.textContent = `Analyzed: ${job.company} - ${job.role} (Risk: ${job.scam_risk}, Fit: ${job.match_score}%)`;
        }
        await loadJobs();
        inspectJob(job.id);
      } else {
        if (data.blocked) {
          if (statusBox) {
            statusBox.innerHTML = `⚠️ <strong>Scraping Blocked by Portal:</strong> Please paste the job description text above or use the 1-Click Brave Extension.`;
          }
        } else {
          if (statusBox) statusBox.textContent = `Analysis error: ${data.error}`;
        }
      }
    } catch (err) {
      if (statusBox) statusBox.textContent = `Error: ${err.message}`;
    }
  }

  async function handleJdPdfUpload(e) {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const statusBox = document.getElementById("career-analyze-status");
    if (statusBox) statusBox.textContent = `Uploading ${file.name}...`;

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch("/api/chat/upload_pdf", { method: "POST", body: formData });
      const data = await res.json();
      if (data.status === "ok" && data.text) {
        const textInput = document.getElementById("career-text-input");
        if (textInput) textInput.value = data.text;
        if (statusBox) statusBox.textContent = `Extracted ${data.pages} pages from PDF. Click "Analyze & Check Scam".`;
      } else {
        if (statusBox) statusBox.textContent = `PDF read error: ${data.error || "Empty text"}`;
      }
    } catch (err) {
      if (statusBox) statusBox.textContent = `Upload failed: ${err.message}`;
    } finally {
      try { e.target.value = ""; } catch (_) {}
    }
  }

  function updateFilterPillCounts(allJobs) {
    const base = allJobs || [];
    const candidateList = (locationFilter !== "all") ? base.filter(matchesLocationFilter) : base;

    const promisingCount = candidateList.filter((j) => j.scam_risk === "LOW_RISK" && j.status !== "Ignored_Scam").length;
    const internCount = candidateList.filter(isInternshipJob).length;
    const fresherCount = candidateList.filter(isFresherEntryJob).length;
    const fakeCount = candidateList.filter((j) => j.scam_risk === "HIGH_RISK" || j.status === "Ignored_Scam").length;
    const allCount = candidateList.length;

    const setBadge = (filterName, count) => {
      const el = document.getElementById(`count-${filterName}`);
      if (el) el.textContent = `(${count})`;
    };

    setBadge("promising", promisingCount);
    setBadge("internships", internCount);
    setBadge("fresher", fresherCount);
    setBadge("fake", fakeCount);
    setBadge("all", allCount);
  }

  function renderFeedCards(jobs) {
    const container = document.getElementById("career-feed-cards");
    if (!container) return;

    updateFilterPillCounts(jobs);

    if (!jobs || jobs.length === 0) {
      container.innerHTML = `
        <div class="career-empty-state">
          <h4>No job postings discovered yet</h4>
          <p>Click "Scan Feeds" above to discover public ATS postings or paste any job URL.</p>
        </div>
      `;
      return;
    }

    let filtered = jobs.filter((job) => {
      if (currentFilter === "promising") {
        if (job.scam_risk !== "LOW_RISK" || job.status !== "Ignored_Scam") return false;
      } else if (currentFilter === "internships") {
        if (!isInternshipJob(job)) return false;
      } else if (currentFilter === "fresher") {
        if (!isFresherEntryJob(job)) return false;
      } else if (currentFilter === "fake") {
        if (job.scam_risk !== "HIGH_RISK" && job.status !== "Ignored_Scam") return false;
      }

      if (locationFilter !== "all" && !matchesLocationFilter(job)) {
        return false;
      }

      return true;
    });

    if (currentFilter === "internships" || currentFilter === "fresher") {
      filtered.sort((a, b) => {
        const aIndia = isIndiaOnlyJob(a) ? 1 : 0;
        const bIndia = isIndiaOnlyJob(b) ? 1 : 0;
        if (aIndia !== bIndia) {
          return bIndia - aIndia; // India-eligible roles first
        }
        const aTime = a.updated_at || a.created_at || a.id || 0;
        const bTime = b.updated_at || b.created_at || b.id || 0;
        if (bTime !== aTime) return bTime > aTime ? 1 : -1;
        return (b.match_score || 0) - (a.match_score || 0);
      });
    }

    if (filtered.length === 0) {
      let filterDisplayName = currentFilter;
      if (currentFilter === "promising") filterDisplayName = "Promising & Safe";
      else if (currentFilter === "internships") filterDisplayName = "Internships";
      else if (currentFilter === "fresher") filterDisplayName = "Fresher / Entry";
      else if (currentFilter === "fake") filterDisplayName = "Fake / Quarantined";
      else if (currentFilter === "all") filterDisplayName = "All";

      let locNotice = "";
      if (locationFilter === "india") locNotice = " (India Only)";
      else if (locationFilter === "worldwide_remote") locNotice = " (Worldwide Remote)";

      let actionButtons = "";
      if (locationFilter !== "all") {
        actionButtons = `
          <button type="button" class="mini-btn primary-btn" id="career-clear-location-filter-btn">Clear Location Filter</button>
          <button type="button" class="mini-btn" id="career-show-all-jobs-btn" style="margin-left:8px;">Show All Jobs</button>
        `;
      } else {
        actionButtons = `
          <button type="button" class="mini-btn primary-btn" id="career-show-all-jobs-btn">Show All Jobs</button>
        `;
      }

      container.innerHTML = `
        <div class="career-empty-state">
          <h4>No jobs match the '${filterDisplayName}' filter${locNotice}</h4>
          <p>${locationFilter !== "all" ? "Try clearing the location filter to view all openings, or switch filter categories." : "Switch filter pills or click 'Scan Feeds' to discover more postings."}</p>
          <div style="margin-top:12px;">${actionButtons}</div>
        </div>
      `;

      const clearLocBtn = document.getElementById("career-clear-location-filter-btn");
      if (clearLocBtn) {
        clearLocBtn.addEventListener("click", () => {
          locationFilter = "all";
          indiaRemoteOnly = false;
          const iToggle = document.getElementById("career-india-toggle");
          const wToggle = document.getElementById("career-worldwide-remote-toggle");
          if (iToggle) iToggle.classList.remove("active");
          if (wToggle) wToggle.classList.remove("active");
          renderFeedCards(cachedJobs);
        });
      }

      const showAllBtn = document.getElementById("career-show-all-jobs-btn");
      if (showAllBtn) {
        showAllBtn.addEventListener("click", () => {
          locationFilter = "all";
          indiaRemoteOnly = false;
          currentFilter = "all";
          const iToggle = document.getElementById("career-india-toggle");
          const wToggle = document.getElementById("career-worldwide-remote-toggle");
          if (iToggle) iToggle.classList.remove("active");
          if (wToggle) wToggle.classList.remove("active");
          const pills = document.querySelectorAll(".career-filter-pill:not(.toggle-pill)");
          pills.forEach((p) => p.classList.toggle("active", p.dataset.filter === "all"));
          renderFeedCards(cachedJobs);
        });
      }
      return;
    }

    container.innerHTML = filtered
      .map((job) => {
        const isScam = job.scam_risk === "HIGH_RISK";
        const riskClass = isScam ? "risk-blocked" : job.scam_risk === "VERIFY_MANUALLY" ? "risk-verify" : "risk-safe";
        const riskLabel = isScam ? "SCAM BLOCKED" : job.scam_risk === "VERIFY_MANUALLY" ? "VERIFY" : "SAFE";
        const fitScore = isScam ? "0" : (job.match_score || 0);

        return `
          <div class="career-job-card ${isScam ? 'card-scam' : ''}" data-job-id="${job.id}">
            <div class="job-card-header">
              <div class="job-company-wrap">
                <span class="job-company">${escapeHtml(job.company || "Company")}</span>
                <span class="job-source">${escapeHtml(job.source || "Feed")}</span>
              </div>
              <div class="job-badges-wrap">
                <span class="risk-badge ${riskClass}">${riskLabel}</span>
                <span class="match-score-badge">${fitScore}% Match</span>
              </div>
            </div>
            <h4 class="job-role-title">${escapeHtml(job.role || "Opportunity")}</h4>
            <div class="job-location">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg>
              <span>${escapeHtml(job.location || "India / Remote")}</span>
            </div>
            <div class="job-card-actions">
              <button class="primary-btn mini-btn inspect-btn" onclick="window.VegaCareer.inspectJob(${job.id})">
                Inspect & Tailor
              </button>
              ${!isScam ? `
              <button class="mini-btn interview-btn" onclick="window.VegaCareer.startMockInterview(${job.id})" title="Start 10-Minute Voice/HUD Mock Interview">
                Mock Interview
              </button>` : ""}
              ${
                job.url && job.url.startsWith("http")
                  ? `<a href="${job.url}" target="_blank" rel="noopener noreferrer" class="link-btn mini-btn">Apply Official</a>`
                  : ""
              }
            </div>
          </div>
        `;
      })
      .join("");
  }

  // ==========================================
  // 2. JOB INSPECTOR & OUTREACH DRAFTER
  // ==========================================
  async function inspectJob(jobId) {
    activeJobId = jobId;
    switchCareerSubtab("inspector");

    const job = cachedJobs.find((j) => j.id === jobId);
    if (!job) return;

    // Populate Inspector UI
    const compEl = document.getElementById("inspector-company");
    const roleEl = document.getElementById("inspector-role");
    const locEl = document.getElementById("inspector-location");
    const scoreEl = document.getElementById("inspector-match-score");
    const riskBadge = document.getElementById("inspector-risk-badge");
    const flagsList = document.getElementById("inspector-scam-flags");
    const matchedList = document.getElementById("inspector-matched-skills");
    const missingList = document.getElementById("inspector-missing-skills");
    const coverText = document.getElementById("inspector-cover-note");
    const questionsList = document.getElementById("inspector-interview-questions");
    const openApplyLink = document.getElementById("career-open-apply-btn");
    const pdfStatus = document.getElementById("career-resume-pdf-status");
    const blockedAlert = document.getElementById("inspector-blocked-alert");
    const outreachStatus = document.getElementById("outreach-status-msg");

    if (compEl) compEl.textContent = job.company;
    if (roleEl) roleEl.textContent = job.role;
    if (locEl) locEl.textContent = job.location || "India / Remote";
    if (scoreEl) scoreEl.textContent = `${job.match_score || 0}%`;
    if (outreachStatus) outreachStatus.textContent = "";

    if (blockedAlert) {
      if (job.blocked) {
        blockedAlert.classList.remove("hidden");
      } else {
        blockedAlert.classList.add("hidden");
      }
    }

    const isScam = job.scam_risk === "HIGH_RISK";
    if (riskBadge) {
      riskBadge.className = `risk-badge ${isScam ? 'risk-blocked' : job.scam_risk === 'VERIFY_MANUALLY' ? 'risk-verify' : 'risk-safe'}`;
      riskBadge.textContent = isScam ? 'SCAM BLOCKED' : job.scam_risk === 'VERIFY_MANUALLY' ? 'VERIFY MANUALLY' : 'SAFE ROLE';
    }

    // Scam flags
    if (flagsList) {
      const flags = job.scam_flags || [];
      flagsList.innerHTML = flags.length
        ? flags.map((f) => `<li class="scam-flag-item">⚠️ ${escapeHtml(f)}</li>`).join("")
        : `<li class="clean-flag-item">✓ Zero deterministic or credibility scam red flags detected.</li>`;
    }

    // Matched skills
    if (matchedList) {
      const matched = job.matched_skills || [];
      matchedList.innerHTML = matched.length
        ? matched.map((s) => `<span class="skill-pill matched-pill">✓ ${escapeHtml(s)}</span>`).join("")
        : `<span class="empty-pill">No direct skill keywords matched</span>`;
    }

    // Missing skills
    if (missingList) {
      const missing = job.missing_skills || [];
      missingList.innerHTML = missing.length
        ? missing.map((s) => `<span class="skill-pill missing-pill">+ ${escapeHtml(s)}</span>`).join("")
        : `<span class="clean-pill">Zero missing core skills</span>`;
    }

    // Cover Note
    if (coverText) {
      coverText.value = job.cover_note || "";
    }

    // Switch outreach subtab back to cover note
    switchOutreachTab("cover");

    // Interview Prep
    if (questionsList) {
      const qs = job.interview_prep || [];
      questionsList.innerHTML = qs.length
        ? qs
            .map(
              (q) => `
          <div class="prep-question-card">
            <div class="prep-question">${escapeHtml(q.question || q)}</div>
            ${q.context ? `<div class="prep-context">${escapeHtml(q.context)}</div>` : ""}
          </div>
        `
            )
            .join("")
        : `<div>Interview preparation generated when role is tailored.</div>`;
    }

    // Official apply link
    if (openApplyLink) {
      if (job.url && job.url.startsWith("http")) {
        openApplyLink.href = job.url;
        openApplyLink.classList.remove("hidden");
      } else {
        openApplyLink.classList.add("hidden");
      }
    }

    // Existing tailored PDF status
    if (pdfStatus) {
      if (job.tailored_pdf_path) {
        const fname = job.tailored_pdf_path.split(/[\\/]/).pop();
        pdfStatus.innerHTML = `
          <span class="pdf-ready-tag">✓ 1-Page ATS PDF Ready:</span>
          <a href="/api/career/download_pdf/${fname}" target="_blank" class="download-link">Download ${fname}</a>
        `;
      } else {
        pdfStatus.textContent = "Click 'Generate 1-Page Tailored PDF' to build ATS resume.";
      }
    }

    // Trigger interactive JD tailoring analysis
    analyzeJdForJob(job);
  }

  async function analyzeJdForJob(job) {
    activeSkillDecisions = {};
    activeSelectedProjects = [];

    const decisionsEl = document.getElementById("inspector-skill-decisions");
    const projsEl = document.getElementById("inspector-recommended-projects");
    if (decisionsEl) decisionsEl.innerHTML = `<span style="font-size:11px; color:#888;">Analyzing JD skills and project fit...</span>`;
    if (projsEl) projsEl.innerHTML = `<span style="font-size:11px; color:#888;">Selecting top projects from portfolio...</span>`;

    try {
      const res = await fetch("/api/career/resume/analyze-jd", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          jd_text: job.raw_text || job.role || "",
          company: job.company || "",
          role: job.role || ""
        })
      });
      const data = await res.json();
      if (data.ok || data.status === "ok") {
        renderJdAnalysis(data, job);
      } else {
        throw new Error(data.error || "Analysis failed");
      }
    } catch (e) {
      console.warn("JD Analysis failed:", e);
      if (decisionsEl) {
        decisionsEl.innerHTML = `
          <div style="font-size:11px; color:#ef4444; margin-bottom:4px;">Failed to analyze JD. ${escapeHtml(e.message || '')}</div>
          <button type="button" class="mini-btn" id="retry-jd-analysis-btn" style="font-size:10px; padding:2px 8px;">Retry Analysis</button>
        `;
        const retryBtn = document.getElementById("retry-jd-analysis-btn");
        if (retryBtn) retryBtn.onclick = () => analyzeJdForJob(job);
      }
      if (projsEl) {
        projsEl.innerHTML = `<span style="font-size:11px; color:#888;">Project recommendations unavailable.</span>`;
      }
    }
  }

  function renderJdAnalysis(data, job) {
    const decisionsEl = document.getElementById("inspector-skill-decisions");
    const projsEl = document.getElementById("inspector-recommended-projects");
    const discussBtn = document.getElementById("career-discuss-chat-btn");
    const questionsList = document.getElementById("inspector-interview-questions");

    if (questionsList && (!job.interview_prep || !job.interview_prep.length) && data.interview_prep && data.interview_prep.length) {
      const qs = data.interview_prep;
      questionsList.innerHTML = qs.map(q => `
        <div class="prep-question-card">
          <div class="prep-question">${escapeHtml(q.question || q)}</div>
          ${q.context ? `<div class="prep-context">${escapeHtml(q.context)}</div>` : ""}
        </div>
      `).join("");
    }

    if (decisionsEl) {
      const missing = data.missing_skills || [];
      if (!missing.length) {
        decisionsEl.innerHTML = `<span style="font-size:11px; color:#10b981;">✓ All core JD skills matched in verified profile.</span>`;
      } else {
        decisionsEl.innerHTML = missing.map(skill => {
          activeSkillDecisions[skill] = activeSkillDecisions[skill] || "learning";
          return `
            <div class="skill-decision-row" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; padding:4px 6px; background:rgba(0,0,0,0.02); border-radius:4px;">
              <span style="font-size:11px; font-weight:600;">${escapeHtml(skill)}</span>
              <div class="decision-pill-group" data-skill="${escapeHtml(skill)}" style="display:flex; gap:4px;">
                <button type="button" class="mini-btn decision-btn ${activeSkillDecisions[skill] === 'know' ? 'active-pill' : ''}" data-state="know" style="font-size:10px; padding:2px 6px;">Know / Used</button>
                <button type="button" class="mini-btn decision-btn ${activeSkillDecisions[skill] === 'learning' ? 'active-pill' : ''}" data-state="learning" style="font-size:10px; padding:2px 6px;">Currently Learning</button>
                <button type="button" class="mini-btn decision-btn ${activeSkillDecisions[skill] === 'skip' ? 'active-pill' : ''}" data-state="skip" style="font-size:10px; padding:2px 6px;">Skip</button>
              </div>
            </div>
          `;
        }).join("");

        decisionsEl.querySelectorAll(".decision-btn").forEach(btn => {
          btn.addEventListener("click", (e) => {
            const group = e.target.closest(".decision-pill-group");
            const skill = group.dataset.skill;
            const state = e.target.dataset.state;
            activeSkillDecisions[skill] = state;
            group.querySelectorAll(".decision-btn").forEach(b => b.classList.remove("active-pill"));
            e.target.classList.add("active-pill");
          });
        });
      }
    }

    if (projsEl) {
      const projs = data.recommended_projects || [];
      activeSelectedProjects = projs.map(p => p.name);
      projsEl.innerHTML = projs.map((p) => `
        <div class="rec-proj-item" style="margin-bottom:6px; padding:4px 8px; background:rgba(0,0,0,0.02); border-left:3px solid #3b82f6; border-radius:3px;">
          <label style="display:flex; align-items:flex-start; gap:6px; cursor:pointer; margin:0;">
            <input type="checkbox" name="rec-proj-check" class="rec-proj-check" data-proj="${escapeHtml(p.name)}" aria-label="${escapeHtml(p.name)}" checked style="margin-top:2px;">
            <div>
              <div style="font-size:11px; font-weight:600;">${escapeHtml(p.name)} <span style="font-weight:normal; color:#666;">(${escapeHtml(p.tech)})</span></div>
              <div style="font-size:10px; color:#444;">💡 ${escapeHtml(p.justification || "Matches core role requirements.")}</div>
            </div>
          </label>
        </div>
      `).join("");

      projsEl.querySelectorAll(".rec-proj-check").forEach(chk => {
        chk.addEventListener("change", (e) => {
          const pName = e.target.dataset.proj;
          if (e.target.checked) {
            if (!activeSelectedProjects.includes(pName)) activeSelectedProjects.push(pName);
          } else {
            activeSelectedProjects = activeSelectedProjects.filter(n => n !== pName);
          }
        });
      });
    }

    if (discussBtn) {
      discussBtn.textContent = "Discuss & Co-Build Resume";
      discussBtn.onclick = async () => {
        const origText = discussBtn.textContent;
        discussBtn.disabled = true;
        discussBtn.textContent = "Starting Session...";
        try {
          const res = await fetch("/api/career/resume/session/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              job_id: (job && job.id) || activeJobId || "",
              company: (job && job.company) || "Target Tech",
              role: (job && job.role) || "DevOps & Cloud Engineer",
              jd_text: (job && (job.raw_text || job.description)) || ""
            })
          });
          const sData = await res.json();
          if (sData && sData.ok && sData.session) {
            const chatTab = document.getElementById("mode-chat-tab");
            if (chatTab) chatTab.click();
            if (typeof window.loadChat === "function") {
              window.loadChat(sData.chat_id);
            }
            if (typeof window.renderLiveResumeDraftBar === "function") {
              window.renderLiveResumeDraftBar(sData.session);
            }
            if (window.appendNotice) {
              window.appendNotice(`Resume Co-Builder active for ${job.role || 'Target Role'}!`, "info");
            }
          } else {
            throw new Error((sData && sData.error) || "Failed to start resume session");
          }
        } catch (e) {
          console.error("Failed to start resume session, falling back to chat prompt", e);
          const matched = data.matched_skills || [];
          const missing = data.missing_skills || [];
          const chatPrompt = `I am preparing for the ${job.role} position at ${job.company || 'innovative engineering teams'}. Matched core skills: ${matched.join(', ')}. Target learning gaps: ${missing.join(', ')}. Let's run a technical drill on these concepts and how my projects prove readiness.`;
          const promptInput = document.getElementById("prompt-input");
          if (promptInput) {
            promptInput.value = chatPrompt;
            promptInput.focus();
          }
          const chatTab = document.getElementById("mode-chat-tab");
          if (chatTab) chatTab.click();
        } finally {
          discussBtn.disabled = false;
          discussBtn.textContent = "Discuss & Co-Build Resume";
        }
      };
    }
  }

  function switchOutreachTab(tabName) {
    activeOutreachTab = tabName;
    const tabs = document.querySelectorAll("[data-outreach-tab]");
    tabs.forEach((t) => t.classList.toggle("active", t.dataset.outreachTab === tabName));

    const coverEl = document.getElementById("inspector-cover-note");
    const liDmEl = document.getElementById("inspector-linkedin-dm");
    const liConnEl = document.getElementById("inspector-connection-note");
    const emailWrap = document.getElementById("inspector-cold-email-wrap");

    if (coverEl) coverEl.classList.toggle("hidden", tabName !== "cover");
    if (liDmEl) liDmEl.classList.toggle("hidden", tabName !== "linkedin");
    if (liConnEl) liConnEl.classList.toggle("hidden", tabName !== "conn");
    if (emailWrap) emailWrap.classList.toggle("hidden", tabName !== "email");

    const copyBtn = document.getElementById("career-copy-note-btn");
    if (copyBtn) {
      if (tabName === "cover") copyBtn.textContent = "Copy Cover Note";
      else if (tabName === "linkedin") copyBtn.textContent = "Copy LinkedIn DM";
      else if (tabName === "conn") copyBtn.textContent = "Copy Connection Note";
      else if (tabName === "email") copyBtn.textContent = "Copy Cold Email";
    }
  }

  async function handleDraftOutreach() {
    if (!activeJobId) return;
    const btn = document.getElementById("career-draft-outreach-btn");
    const statusEl = document.getElementById("outreach-status-msg");

    if (btn) {
      btn.disabled = true;
      btn.innerHTML = `<span>Drafting with 5-brain router...</span>`;
    }
    if (statusEl) statusEl.textContent = "Crafting tailored referral notes & cold email...";

    try {
      const res = await fetch("/api/career/draft-outreach", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: activeJobId })
      });
      const data = await res.json();
      if (data.status === "ok") {
        const liDm = document.getElementById("inspector-linkedin-dm");
        const liConn = document.getElementById("inspector-connection-note");
        const emailSubj = document.getElementById("inspector-email-subject");
        const emailBody = document.getElementById("inspector-email-body");

        if (liDm) liDm.value = data.linkedin_dm || "";
        if (liConn) liConn.value = data.linkedin_connection_note || "";
        if (emailSubj) emailSubj.value = data.cold_email?.subject || "";
        if (emailBody) emailBody.value = data.cold_email?.body || "";

        switchOutreachTab("linkedin");
        if (statusEl) {
          statusEl.textContent = `✓ Drafted referral DM & cold email via ${data.provider}.`;
          setTimeout(() => { if (statusEl) statusEl.textContent = ""; }, 4000);
        }
      } else {
        if (statusEl) statusEl.textContent = `Drafting error: ${data.error}`;
      }
    } catch (err) {
      if (statusEl) statusEl.textContent = `Drafting failed: ${err.message}`;
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = `
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
          <span>Draft Referral / Cold DM</span>
        `;
      }
    }
  }

  function handleCopyCoverNote() {
    let textToCopy = "";
    if (activeOutreachTab === "cover") {
      const el = document.getElementById("inspector-cover-note");
      textToCopy = el ? el.value : "";
    } else if (activeOutreachTab === "linkedin") {
      const el = document.getElementById("inspector-linkedin-dm");
      textToCopy = el ? el.value : "";
    } else if (activeOutreachTab === "conn") {
      const el = document.getElementById("inspector-connection-note");
      textToCopy = el ? el.value : "";
    } else if (activeOutreachTab === "email") {
      const subj = document.getElementById("inspector-email-subject");
      const body = document.getElementById("inspector-email-body");
      textToCopy = `Subject: ${subj ? subj.value : ""}\n\n${body ? body.value : ""}`;
    }

    if (!textToCopy) return;

    const copyBtn = document.getElementById("career-copy-note-btn");
    navigator.clipboard.writeText(textToCopy).then(() => {
      if (copyBtn) {
        const orig = copyBtn.textContent;
        copyBtn.textContent = "Copied!";
        setTimeout(() => (copyBtn.textContent = orig), 2000);
      }
    });
  }

  async function handleGenerateResume() {
    if (!activeJobId) return;
    const btn = document.getElementById("career-gen-resume-btn");
    const pdfStatus = document.getElementById("career-resume-pdf-status");

    if (btn) {
      btn.disabled = true;
      btn.innerHTML = `<span>Generating tailored 1-page PDF...</span>`;
    }

    try {
      const job = cachedJobs.find((j) => j.id === activeJobId) || {};
      const res = await fetch("/api/career/resume/generate-tailored", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job_id: activeJobId,
          jd_text: job.raw_text || job.role || "",
          company: job.company || "",
          role: job.role || "",
          skill_decisions: activeSkillDecisions,
          selected_project_names: activeSelectedProjects
        })
      });
      const data = await res.json();
      if (data.ok || data.status === "ok") {
        if (pdfStatus) {
          pdfStatus.innerHTML = `
            <span class="pdf-ready-tag">✓ Generated Strict 1-Page PDF:</span>
            <a href="${data.download_url}" target="_blank" class="download-link">Download ${data.filename}</a>
          `;
        }
        await loadJobs();
      } else {
        if (pdfStatus) pdfStatus.textContent = `PDF generation failed: ${data.error}`;
      }
    } catch (err) {
      if (pdfStatus) pdfStatus.textContent = `Error: ${err.message}`;
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = `
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/></svg>
          <span>Generate 1-Page Tailored PDF</span>
        `;
      }
    }
  }

  function handleOpenApplyLink(e) {
    const job = cachedJobs.find((j) => j.id === activeJobId);
    if (!job || !job.url || !job.url.startsWith("http")) {
      e.preventDefault();
      alert("No official web URL for this role. Check details in Inspector.");
    }
  }

  // ==========================================
  // 3. APPLICATION TRACKER
  // ==========================================
  function renderMetrics(m) {
    const ids = {
      Total: "metric-total",
      New: "metric-new",
      Tailored: "metric-tailored",
      Applied: "metric-applied",
      Interview: "metric-interview",
      Rejected: "metric-rejected",
      Ignored_Scam: "metric-scam",
    };
    for (const [key, id] of Object.entries(ids)) {
      const el = document.getElementById(id);
      if (el) el.textContent = m[key] || 0;
    }
  }

  function renderTrackerTable(jobs) {
    const tbody = document.getElementById("career-tracker-tbody");
    if (!tbody) return;

    if (!jobs || jobs.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" class="table-empty">No tracked applications yet.</td></tr>`;
      return;
    }

    const statuses = ["New", "Saved", "Tailored", "Applied", "Online Assessment", "Interview", "Offer", "Rejected", "Ignored_Scam"];

    tbody.innerHTML = jobs
      .map((job) => {
        const fname = job.tailored_pdf_path ? job.tailored_pdf_path.split(/[\\/]/).pop() : null;
        return `
          <tr data-job-id="${job.id}">
            <td><strong>${escapeHtml(job.company)}</strong></td>
            <td>${escapeHtml(job.role)}</td>
            <td><span class="match-badge">${job.match_score || 0}%</span></td>
            <td>
              <select class="status-select" onchange="window.VegaCareer.changeJobStatus(${job.id}, this.value)">
                ${statuses
                  .map(
                    (s) =>
                      `<option value="${s}" ${job.status === s ? "selected" : ""}>${s.replace("_", " ")}</option>`
                  )
                  .join("")}
              </select>
            </td>
            <td>
              ${
                fname
                  ? `<a href="/api/career/download_pdf/${fname}" target="_blank" class="table-pdf-link">📄 ${fname}</a>`
                  : `<span class="dimmed-text">-</span>`
              }
            </td>
            <td>
              <button class="table-action-btn" onclick="window.VegaCareer.inspectJob(${job.id})">Inspect</button>
            </td>
          </tr>
        `;
      })
      .join("");
  }

  async function changeJobStatus(jobId, newStatus) {
    try {
      await fetch("/api/career/update_status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobId, status: newStatus })
      });
      await loadJobs();
    } catch (err) {
      console.warn("Status update error:", err);
    }
  }

  // ==========================================
  // 4. PROFILE VAULT (Structured Form + JSON)
  // ==========================================
  async function loadProfile() {
    try {
      const res = await fetch("/api/career/profile");
      const data = await res.json();
      if (data.status === "ok" && data.profile) {
        cachedProfile = data.profile;
        renderProfileVault(cachedProfile);
      }
    } catch (err) {
      console.warn("Failed to load profile:", err);
      renderProfileVault({});
    }
  }

  function renderProfileVault(profile) {
    try {
      const p = profile || {};
      const nameEl = document.getElementById("vault-name");
      const headlineEl = document.getElementById("vault-headline");
      const phoneEl = document.getElementById("vault-phone");
      const emailEl = document.getElementById("vault-email");
      const locEl = document.getElementById("vault-location");
      const ghEl = document.getElementById("vault-github");
      const liEl = document.getElementById("vault-linkedin");
      const sumEl = document.getElementById("vault-summary");
      const eduEl = document.getElementById("vault-education");
      const skillsEl = document.getElementById("vault-skills");
      const projEl = document.getElementById("vault-projects");
      const vaultJson = document.getElementById("career-vault-json");

      if (nameEl) nameEl.value = p.name || "";
      if (headlineEl) headlineEl.value = p.headline || "";
      if (phoneEl) phoneEl.value = p.phone || "";
      if (emailEl) emailEl.value = p.email || "";
      if (locEl) locEl.value = p.location || "";

      const links = p.links || {};
      if (ghEl) ghEl.value = links.github || "";
      if (liEl) liEl.value = links.linkedin || "";
      if (sumEl) sumEl.value = p.summary || "";

      // Format education
      if (eduEl) {
        if (Array.isArray(p.education)) {
          eduEl.value = p.education
            .map((ed) => {
              const inst = ed.institution || ed.school || "";
              const deg = ed.degree || "";
              const years = ed.duration || ed.years || ed.dates || "";
              const gpa = ed.cgpa || ed.gpa || "";
              return `${inst} | ${deg} | ${years} | CGPA: ${gpa}`;
            })
            .join("\n");
        } else if (typeof p.education === "string") {
          eduEl.value = p.education;
        } else {
          eduEl.value = "";
        }
      }

      // Format skills
      if (skillsEl) {
        if (p.verified_skills && typeof p.verified_skills === "object") {
          const lines = [];
          for (const [cat, val] of Object.entries(p.verified_skills)) {
            const formattedCat = cat.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
            const items = Array.isArray(val) ? val.join(", ") : String(val);
            lines.push(`${formattedCat}: ${items}`);
          }
          skillsEl.value = lines.join("\n");
        } else if (Array.isArray(p.skills)) {
          skillsEl.value = p.skills.join(", ");
        } else {
          skillsEl.value = "";
        }
      }

      // Format projects
      if (projEl) {
        if (Array.isArray(p.verified_projects)) {
          projEl.value = p.verified_projects
            .map((pj) => {
              const tech = Array.isArray(pj.technologies)
                ? pj.technologies.join(", ")
                : (pj.tech || "");
              const bullets = Array.isArray(pj.highlights)
                ? pj.highlights.map((h) => `• ${h}`).join("\n")
                : Array.isArray(pj.bullets)
                  ? pj.bullets.map((b) => `• ${b}`).join("\n")
                  : pj.description || "";
              return `${pj.name || "Project"} | ${tech}\n${bullets}`;
            })
            .join("\n\n");
        } else {
          projEl.value = "";
        }
      }

      if (vaultJson) {
        vaultJson.value = JSON.stringify(p, null, 2);
      }
    } catch (err) {
      console.warn("Error rendering profile vault:", err);
    }
  }

  async function handleSaveProfile() {
    const statusEl = document.getElementById("career-vault-status");
    const rawContainer = document.getElementById("vault-raw-container");
    const isRawActive = rawContainer && !rawContainer.classList.contains("hidden");

    let payload = cachedProfile || {};

    try {
      if (isRawActive) {
        const vaultJson = document.getElementById("career-vault-json");
        if (vaultJson && vaultJson.value.trim()) {
          payload = JSON.parse(vaultJson.value);
        }
      } else {
        const nameEl = document.getElementById("vault-name");
        const headlineEl = document.getElementById("vault-headline");
        const phoneEl = document.getElementById("vault-phone");
        const emailEl = document.getElementById("vault-email");
        const locEl = document.getElementById("vault-location");
        const ghEl = document.getElementById("vault-github");
        const liEl = document.getElementById("vault-linkedin");
        const sumEl = document.getElementById("vault-summary");
        const eduEl = document.getElementById("vault-education");
        const skillsEl = document.getElementById("vault-skills");
        const projEl = document.getElementById("vault-projects");

        payload.name = nameEl ? nameEl.value.trim() : payload.name;
        payload.headline = headlineEl ? headlineEl.value.trim() : payload.headline;
        payload.phone = phoneEl ? phoneEl.value.trim() : payload.phone;
        payload.email = emailEl ? emailEl.value.trim() : payload.email;
        payload.location = locEl ? locEl.value.trim() : payload.location;

        payload.links = payload.links || {};
        if (ghEl) payload.links.github = ghEl.value.trim();
        if (liEl) payload.links.linkedin = liEl.value.trim();
        if (sumEl) payload.summary = sumEl.value.trim();

        if (eduEl && eduEl.value.trim()) {
          const eduLines = eduEl.value.trim().split("\n");
          payload.education = eduLines.map(line => {
            const parts = line.split("|").map(s => s.trim());
            const inst = parts[0] || "";
            const deg = parts[1] || "";
            const dur = parts[2] || "";
            let cgpa = parts[3] || "";
            if (cgpa.toLowerCase().startsWith("cgpa:")) {
              cgpa = cgpa.substring(5).trim();
            }
            return {
              degree: deg,
              institution: inst,
              duration: dur,
              dates: dur,
              cgpa: cgpa
            };
          });
        }

        if (skillsEl && skillsEl.value.trim()) {
          const lines = skillsEl.value.trim().split("\n");
          const parsedSkills = {};
          lines.forEach(l => {
            if (l.includes(":")) {
              const [cat, items] = l.split(":", 2);
              parsedSkills[cat.trim()] = items.split(",").map(s => s.trim()).filter(Boolean);
            }
          });
          if (Object.keys(parsedSkills).length > 0) {
            payload.verified_skills = parsedSkills;
          }
        }

        if (projEl && projEl.value.trim()) {
          const blocks = projEl.value.trim().split(/\n\s*\n/);
          payload.verified_projects = blocks.map(block => {
            const lines = block.split("\n").map(s => s.trim()).filter(Boolean);
            if (lines.length === 0) return null;
            const header = lines[0];
            const parts = header.split("|").map(s => s.trim());
            const name = parts[0] || "Project";
            const tech = parts[1] || "";
            const bullets = lines.slice(1).map(b => b.replace(/^•\s*/, "").trim()).filter(Boolean);
            const existing = (cachedProfile.verified_projects || []).find(p => p.name === name);
            return {
              name,
              tech,
              repo: existing ? existing.repo : "",
              bullets
            };
          }).filter(Boolean);
        }

        const vaultJson = document.getElementById("career-vault-json");
        if (vaultJson) {
          vaultJson.value = JSON.stringify(payload, null, 2);
        }
      }

      const res = await fetch("/api/career/profile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (data.status === "ok") {
        cachedProfile = payload;
        renderProfileVault(cachedProfile);
        if (statusEl) statusEl.textContent = "✓ Fact-Locked Profile saved successfully.";
        setTimeout(() => {
          if (statusEl) statusEl.textContent = "";
        }, 3000);
      } else {
        if (statusEl) statusEl.textContent = `Save error: ${data.error}`;
      }
    } catch (err) {
      if (statusEl) statusEl.textContent = `Save error: ${err.message}`;
    }
  }

  async function handleImportResumePdf(e) {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const statusEl = document.getElementById("career-vault-status");
    if (statusEl) statusEl.textContent = `Extracting background facts from ${file.name}...`;

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch("/api/career/profile/import_pdf", { method: "POST", body: formData });
      if (res.status === 423) {
        if (statusEl) {
          statusEl.innerHTML = `⚠️ Vega is locked. Unlock Vega to import resume. <button type="button" class="mini-btn unlock-vega-btn" style="margin-left:6px;" onclick="window.showLockScreen && window.showLockScreen()">Unlock Vega</button>`;
        }
        if (window.showLockScreen) window.showLockScreen();
        return;
      }
      const data = await res.json();
      if (data.status === "ok") {
        if (statusEl) {
          statusEl.textContent = `✓ Imported resume PDF (${data.char_count || 0} chars). Fact-Locked Vault updated.`;
        }
        if (data.profile) {
          cachedProfile = data.profile;
          renderProfileVault(cachedProfile);
        } else {
          await loadProfile();
        }
        setTimeout(() => {
          if (statusEl) statusEl.textContent = "";
        }, 3000);
      } else {
        if (statusEl) statusEl.textContent = `Import error: ${data.error || "Failed to parse PDF."}`;
      }
    } catch (err) {
      if (statusEl) statusEl.textContent = `Import error: ${err.message}`;
    } finally {
      try { e.target.value = ""; } catch (_) {}
    }
  }

  // ==========================================
  // 5. GMAIL LABEL SYNC BAR & ACTIONS
  // ==========================================
  function initGmailSyncBar() {
    const syncBtn = document.getElementById("gmail-sync-btn");
    const labelInput = document.getElementById("gmail-target-label");
    const calToggle = document.getElementById("gmail-calendar-toggle");
    const guideToggleBtn = document.getElementById("gmail-setup-toggle-btn");
    const guideCard = document.getElementById("gmail-setup-guide");

    if (syncBtn) syncBtn.addEventListener("click", handleSyncGmail);

    if (guideToggleBtn && guideCard) {
      guideToggleBtn.addEventListener("click", () => {
        const isHidden = guideCard.classList.contains("hidden");
        guideCard.classList.toggle("hidden", !isHidden);
        guideToggleBtn.textContent = isHidden ? "Setup Guide ▴" : "Setup Guide ▾";
      });
    }

    if (labelInput) {
      labelInput.addEventListener("change", async () => {
        const val = labelInput.value.trim();
        if (!val) return;
        await fetch("/api/career/gmail/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ label: val })
        });
      });
    }

    if (calToggle) {
      calToggle.addEventListener("change", async () => {
        await fetch("/api/career/gmail/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ calendar_sync_enabled: calToggle.checked })
        });
      });
    }

    fetchGmailStatus();
  }

  async function fetchGmailStatus() {
    try {
      const res = await fetch("/api/career/gmail/status");
      const data = await res.json();
      if (data.status === "ok" && data.gmail) {
        const g = data.gmail;
        const pill = document.getElementById("gmail-status-pill");
        const labelInput = document.getElementById("gmail-target-label");
        const calToggle = document.getElementById("gmail-calendar-toggle");

        if (labelInput) labelInput.value = g.label || "Vega-Career";
        if (calToggle) calToggle.checked = Boolean(g.calendar_sync_enabled);

        if (pill) {
          pill.className = `gmail-status-pill status-${g.status}`;
          if (g.connected) {
            pill.textContent = "Connected";
          } else if (g.configured) {
            pill.textContent = "Needs Auth";
          } else {
            pill.textContent = "Unconfigured";
          }
        }
      }
    } catch (err) {
      console.warn("Failed to fetch Gmail status:", err);
    }
  }

  async function handleSyncGmail() {
    const syncBtn = document.getElementById("gmail-sync-btn");
    const scanStatus = document.getElementById("career-scan-status");

    if (syncBtn) {
      syncBtn.disabled = true;
      syncBtn.innerHTML = `<span>Syncing label...</span>`;
    }
    if (scanStatus) {
      scanStatus.textContent = "Scanning configured Gmail label for job alerts & application updates...";
    }

    try {
      const res = await fetch("/api/career/gmail/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ interactive: false })
      });
      const data = await res.json();
      if (data.status === "ok") {
        if (scanStatus) {
          scanStatus.textContent = data.notice || `Synced ${data.synced_count} emails.`;
        }
        await loadJobs();
        await fetchGmailStatus();
      } else if (data.status === "needs_auth") {
        if (scanStatus) {
          scanStatus.textContent = "Notice: Authorization needed. Click Setup Guide for OAuth steps.";
        }
        const guideCard = document.getElementById("gmail-setup-guide");
        if (guideCard) guideCard.classList.remove("hidden");
      } else {
        if (scanStatus) {
          scanStatus.textContent = `Gmail sync notice: ${data.notice || data.error || "Cannot sync"}`;
        }
      }
    } catch (err) {
      if (scanStatus) scanStatus.textContent = `Gmail sync error: ${err.message}`;
    } finally {
      if (syncBtn) {
        syncBtn.disabled = false;
        syncBtn.innerHTML = `
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 21h5v-5"/></svg>
          <span>Sync Label Now</span>
        `;
      }
    }
  }

  async function startMockInterview(jobId) {
    try {
      const res = await fetch("/api/interview/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobId })
      });
      const data = await res.json();
      if (data.status === "ok") {
        if (window.setAppMode) {
          window.setAppMode("hud");
        }
        if (window.arcReactorHUD && typeof window.arcReactorHUD.loadInterview === "function") {
          window.arcReactorHUD.loadInterview(data);
        }
      } else {
        alert("Could not start mock interview: " + (data.error || "Unknown error"));
      }
    } catch (err) {
      alert("Error starting mock interview: " + err.message);
    }
  }

  // ==========================================
  // 6. GITHUB REPOSITORIES SYNC & PORTFOLIO
  // ==========================================
  async function loadGitHubRepos() {
    const listEl = document.getElementById("career-github-repos-list");
    if (!listEl) return;
    try {
      const res = await fetch("/api/career/github/repos");
      const data = await res.json();
      if ((data.ok || data.status === "ok") && data.repos) {
        renderGitHubRepos(data.repos);
      }
    } catch (e) {
      console.warn("GitHub repos load failed:", e);
    }
  }

  function renderGitHubRepos(repos) {
    const listEl = document.getElementById("career-github-repos-list");
    if (!listEl) return;
    if (!repos || !repos.length) {
      listEl.innerHTML = `<div style="font-size:12px; color:#888;">No repositories synced yet. Click "Sync GitHub Portfolio" above.</div>`;
      return;
    }
    listEl.innerHTML = repos.map(r => `
      <div class="github-repo-card" style="padding:8px 10px; margin-bottom:6px; border:1px solid rgba(0,0,0,0.08); border-radius:5px; background:rgba(255,255,255,0.7);">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <div style="font-weight:600; font-size:12px;">
            <a href="${escapeHtml(r.html_url)}" target="_blank" style="color:#2563eb; text-decoration:none;">${escapeHtml(r.name)}</a>
            <span style="font-size:10px; padding:2px 5px; background:rgba(0,0,0,0.05); border-radius:3px; margin-left:6px; color:#475569;">${escapeHtml(r.language || 'Python')}</span>
          </div>
          <label style="font-size:11px; display:flex; align-items:center; gap:4px; cursor:pointer; margin:0;">
            <input type="checkbox" name="github-repo-eligible-toggle" class="github-repo-eligible-toggle" data-repo="${escapeHtml(r.name)}" aria-label="Include ${escapeHtml(r.name)} in Tailoring" ${r.resume_eligible ? 'checked' : ''}>
            <span>Include in Tailoring</span>
          </label>
        </div>
        <p style="margin:3px 0 0; font-size:11px; color:#64748b;">${escapeHtml(r.description || 'Verified engineering project.')}</p>
      </div>
    `).join("");

    listEl.querySelectorAll(".github-repo-eligible-toggle").forEach(chk => {
      chk.addEventListener("change", async (e) => {
        const repoName = e.target.dataset.repo;
        const eligible = e.target.checked;
        await fetch("/api/career/github/repo/toggle", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: repoName, eligible })
        });
      });
    });
  }

  async function handleSyncGitHubRepos() {
    const btn = document.getElementById("career-github-sync-btn");
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = `<span>Syncing GitHub...</span>`;
    }
    try {
      const res = await fetch("/api/career/github/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({})
      });
      const data = await res.json();
      if ((data.ok || data.status === "ok") && data.repos) {
        renderGitHubRepos(data.repos);
      }
    } catch (e) {
      console.warn("GitHub sync error:", e);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = `
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 21h5v-5"/></svg>
          <span>Sync GitHub Portfolio</span>
        `;
      }
    }
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Export public API
  window.VegaCareer = {
    init: initCareerModule,
    switchSubtab: switchCareerSubtab,
    inspectJob: inspectJob,
    changeJobStatus: changeJobStatus,
    loadJobs: loadJobs,
    syncGmail: handleSyncGmail,
    startMockInterview: startMockInterview,
    isInternshipJob: isInternshipJob,
    isFresherEntryJob: isFresherEntryJob,
    isIndiaOrRemoteJob: isIndiaOrRemoteJob,
    isIndiaOnlyJob: isIndiaOnlyJob,
    isWorldwideRemoteJob: isWorldwideRemoteJob,
    matchesLocationFilter: matchesLocationFilter,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initCareerModule);
  } else {
    initCareerModule();
  }
})();
