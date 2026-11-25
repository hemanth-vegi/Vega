// Vega Scam Shield & Job Ingester Popup Script
const VEGA_URL = "http://127.0.0.1:47632";

document.addEventListener("DOMContentLoaded", async () => {
  const statusBadge = document.getElementById("server-status");
  const titleEl = document.getElementById("preview-title");
  const compEl = document.getElementById("preview-company");
  const urlEl = document.getElementById("preview-url");
  const ingestBtn = document.getElementById("ingest-btn");
  const resultsPanel = document.getElementById("results-panel");
  const riskBadge = document.getElementById("result-risk-badge");
  const matchScoreEl = document.getElementById("result-match-score");
  const flagsContainer = document.getElementById("result-flags-container");
  const flagsList = document.getElementById("result-flags-list");
  const openVegaBtn = document.getElementById("open-vega-btn");

  let extractedData = null;

  // 1. Check Vega Backend Status
  try {
    const health = await fetch(`${VEGA_URL}/api/health`, { method: "GET" }).catch(() => null);
    if (health && health.ok) {
      statusBadge.textContent = "Vega Online";
      statusBadge.className = "status-badge";
    } else {
      statusBadge.textContent = "Vega Offline";
      statusBadge.className = "status-badge offline";
    }
  } catch (e) {
    statusBadge.textContent = "Vega Offline";
    statusBadge.className = "status-badge offline";
  }

  // 2. Extract DOM details from active tab
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab && tab.id) {
      urlEl.textContent = tab.url || "";

      const injection = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: extractPageJobDetails,
      });

      if (injection && injection[0] && injection[0].result) {
        extractedData = injection[0].result;
        extractedData.url = tab.url;
        titleEl.textContent = extractedData.title || tab.title || "Job Posting";
        compEl.textContent = extractedData.company || "Target Company";
      } else {
        extractedData = {
          url: tab.url,
          title: tab.title || "Job Posting",
          company: "Company",
          text: ""
        };
        titleEl.textContent = tab.title || "Job Posting";
      }
    }
  } catch (err) {
    titleEl.textContent = "Cannot read tab";
    console.warn("DOM extract error:", err);
  }

  // 3. Ingest and check scam
  ingestBtn.addEventListener("click", async () => {
    if (!extractedData) return;
    ingestBtn.disabled = true;
    ingestBtn.textContent = "Analyzing in Vega...";

    try {
      const res = await fetch(`${VEGA_URL}/api/career/ingest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(extractedData)
      });
      const data = await res.json();

      if (data.status === "ok" && data.job) {
        const job = data.job;
        resultsPanel.classList.remove("hidden");

        const isScam = job.scam_risk === "HIGH_RISK";
        const isVerify = job.scam_risk === "VERIFY_MANUALLY";

        riskBadge.className = `badge ${isScam ? 'danger' : isVerify ? 'verify' : 'safe'}`;
        riskBadge.textContent = isScam ? 'SCAM BLOCKED' : isVerify ? 'VERIFY' : 'SAFE ROLE';

        matchScoreEl.textContent = `${job.match_score || 0}%`;

        if (job.scam_flags && job.scam_flags.length > 0) {
          flagsContainer.classList.remove("hidden");
          flagsList.innerHTML = job.scam_flags.map(f => `<li>⚠️ ${f}</li>`).join("");
        } else {
          flagsContainer.classList.add("hidden");
        }

        openVegaBtn.onclick = (e) => {
          e.preventDefault();
          chrome.tabs.create({ url: `${VEGA_URL}/#career` });
        };

        ingestBtn.textContent = "Captured Successfully ✓";
      } else {
        alert("Vega Ingest Error: " + (data.error || "Unknown error"));
        ingestBtn.disabled = false;
        ingestBtn.textContent = "Capture & Check Scam";
      }
    } catch (err) {
      alert("Failed to connect to Vega at " + VEGA_URL + ". Is Vega running?");
      ingestBtn.disabled = false;
      ingestBtn.textContent = "Capture & Check Scam";
    }
  });
});

// Function injected into target tab DOM
function extractPageJobDetails() {
  const url = window.location.href;
  let title = "";
  let company = "";
  let location = "";

  // 1. Title Heuristics
  const titleSelectors = [
    ".job-details-jobs-unified-top-card__job-title", // LinkedIn
    ".jobs-unified-top-card__job-title", // LinkedIn alt
    "h1.topcard__title", // LinkedIn public
    ".profile_on_detail_page", // Internshala
    ".jd-header-title", // Naukri
    "h1[data-automation-id='jobTitle']", // Workday
    ".app-title", // Greenhouse
    ".posting-headline h2", // Lever
    "h1"
  ];
  for (const sel of titleSelectors) {
    const el = document.querySelector(sel);
    if (el && el.innerText.trim()) {
      title = el.innerText.trim();
      break;
    }
  }

  // 2. Company Heuristics
  const companySelectors = [
    ".job-details-jobs-unified-top-card__company-name", // LinkedIn
    ".jobs-unified-top-card__company-name", // LinkedIn alt
    ".topcard__org-name-link", // LinkedIn public
    ".link_display_like_text", // Internshala
    ".company-name-link", // Naukri
    ".company-name", // Greenhouse
    ".posting-headline h2", // Lever
    "meta[property='og:site_name']"
  ];
  for (const sel of companySelectors) {
    const el = document.querySelector(sel);
    if (el) {
      if (el.tagName === "META") {
        company = el.getAttribute("content") || "";
      } else if (el.innerText.trim()) {
        company = el.innerText.trim();
      }
      if (company) break;
    }
  }

  // Fallbacks
  if (!title) title = document.title;
  const rawText = document.body ? document.body.innerText : "";

  return {
    url: url,
    title: title.slice(0, 100),
    company: company.slice(0, 80),
    location: location.slice(0, 80),
    text: rawText.slice(0, 40000),
    source: "Brave Extension"
  };
}
