"use strict";

const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const fsp = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");

const REPO_ROOT = path.resolve(__dirname, "..");
const IDE_ROOT = path.resolve(REPO_ROOT, "..", "Vega-IDE-Build");
const BLOCKED_DIRS = new Set([
  ".git", ".venv", "venv", "__pycache__", "career_vault", "browser_profiles", "profiles",
  "backups", "backup", "archives", "archive", "logs", "data", "chats", "conversations",
  "memory", "diary", "contacts", "reminders", "progress", "lablog_data", ".codex", ".agents"
]);
const BLOCKED_FILES = new Set([".env", "security.json", "config.json", "credentials.json", "contacts.json"]);

function isWithin(parent, candidate) {
  const relative = path.relative(path.resolve(parent), path.resolve(candidate));
  return relative === "" || (relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative));
}

function removeWithin(parent, candidate) {
  const absoluteParent = path.resolve(parent);
  const absoluteTarget = path.resolve(candidate);
  if (!isWithin(absoluteParent, absoluteTarget) || absoluteTarget === absoluteParent) {
    throw new Error("Refusing to remove an E2E path outside its temporary parent.");
  }
  fs.rmSync(absoluteTarget, { recursive: true, force: true });
}

function gitFiles(repo) {
  const result = spawnSync("git", ["ls-files", "-z"], { cwd: repo, encoding: "buffer", windowsHide: true });
  if (result.status !== 0) throw new Error(`git ls-files failed in ${path.basename(repo)}.`);
  return result.stdout.toString("utf8").split("\0").filter(Boolean);
}

function shouldCopy(relativePath) {
  const parts = relativePath.replace(/\\/g, "/").split("/");
  if (parts.some((part) => BLOCKED_DIRS.has(part.toLowerCase()))) return false;
  const base = parts[parts.length - 1].toLowerCase();
  if (BLOCKED_FILES.has(base)) return false;
  if (base === ".env.example" || base === ".gitignore") return true;
  if (/\.(?:db|sqlite|sqlite3|log|bak\w*|zip|7z|rar|pyc|pyo)$/i.test(base)) return false;
  if (/^(?:vega_error|debug_vega).*\.log$/i.test(base)) return false;
  return true;
}

function safeChildEnv(extra = {}) {
  const env = Object.fromEntries(Object.entries(process.env).filter(([name]) =>
    !/(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)/i.test(name)
  ));
  return { ...env, ...extra };
}

function createSourceSnapshot() {
  const parent = fs.mkdtempSync(path.join(REPO_ROOT, "tests", ".vega-browser-e2e-"));
  const snapshot = path.join(parent, "vega-source");
  fs.mkdirSync(snapshot, { recursive: true });
  const paths = gitFiles(REPO_ROOT).filter(shouldCopy);
  for (const relative of paths) {
    const source = path.join(REPO_ROOT, relative);
    if (!fs.existsSync(source) || !fs.statSync(source).isFile()) continue;
    const destination = path.join(snapshot, relative);
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.copyFileSync(source, destination);
  }
  const newSources = [
    ...fs.readdirSync(path.join(REPO_ROOT, "integrations"), { withFileTypes: true })
      .filter((entry) => entry.isFile() && entry.name.endsWith(".py"))
      .map((entry) => path.join("integrations", entry.name)),
    path.join("web", "spec_compiler.js")
  ];
  for (const relative of newSources) {
    const source = path.join(REPO_ROOT, relative);
    const destination = path.join(snapshot, relative);
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.copyFileSync(source, destination);
  }
  fs.writeFileSync(path.join(snapshot, "config.json"), JSON.stringify({ voice_on: false }, null, 2));
  return { parent, snapshot };
}

function resolvePython() {
  if (process.env.VEGA_PYTHON) return process.env.VEGA_PYTHON;
  const recorded = path.join(REPO_ROOT, "python_path.txt");
  if (fs.existsSync(recorded)) {
    const value = fs.readFileSync(recorded, "utf8").trim();
    if (value && fs.existsSync(value)) return value;
  }
  return process.platform === "win32" ? "python" : "python3";
}

function startVega(snapshot, bridgeEnabled, ideEndpoint) {
  const script = [
    "import os, sys",
    "root = os.path.abspath(sys.argv[1])",
    "os.chdir(root)",
    "sys.path.insert(0, root)",
    "import vega_webview as v",
    "from storage.db import init_db",
    "init_db()",
    "v.apply_security_middleware(v.bottle.default_app())",
    "server = v.make_server('127.0.0.1', 0, v.bottle.default_app(), server_class=v.ThreadingWSGIServer, handler_class=v.QuietHandler)",
    "print('READY:' + str(server.server_port), flush=True)",
    "server.serve_forever()"
  ].join("\n");
  const env = safeChildEnv({
    HOST: "127.0.0.1",
    PORT: "0",
    VEGA_HEADLESS: "1",
    VEGA_IDE_BRIDGE_ENABLED: bridgeEnabled ? "1" : "0",
    VEGA_IDE_ENDPOINT: ideEndpoint || "http://127.0.0.1:47640",
    VEGA_CI_MODE: "1"
  });
  const child = spawn(resolvePython(), ["-c", script, snapshot], {
    cwd: snapshot,
    env,
    shell: false,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"]
  });
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (chunk) => { stdout += chunk; });
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  const ready = new Promise((resolve, reject) => {
    const deadline = Date.now() + 90_000;
    const poll = () => {
      const match = stdout.match(/(?:^|\n)READY:(\d+)(?:\r?\n|$)/);
      if (match) return resolve({ child, port: Number(match[1]) });
      if (child.exitCode !== null || Date.now() > deadline) {
        return reject(new Error(`Isolated Vega server did not start (stdout: ${stdout.slice(-1000)}; stderr: ${stderr.slice(-2000)}).`));
      }
      setTimeout(poll, 100);
    };
    poll();
  });
  return ready;
}

function startReceiver(workspaceRoot) {
  const receiverPath = path.join(IDE_ROOT, "integrations", "vega_context_provider.js");
  const script = [
    "const { registerVegaContextProvider } = require(process.argv[1]);",
    "const workspaceRoot = process.argv[2];",
    "const vscode = { workspace: { workspaceFolders: [{ uri: { fsPath: workspaceRoot } }] }, window: {}, languages: { getDiagnostics: () => [] } };",
    "const server = registerVegaContextProvider(vscode, { port: 0 });",
    "server.on('listening', () => console.log('READY:' + server.address().port));",
    "process.on('SIGTERM', () => server.close(() => process.exit(0)));"
  ].join("\n");
  const child = spawn(process.execPath, ["-e", script, receiverPath, workspaceRoot], {
    cwd: IDE_ROOT,
    env: safeChildEnv({ VEGA_IDE_BRIDGE_ENABLED: "1" }),
    shell: false,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"]
  });
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (chunk) => { stdout += chunk; });
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + 20_000;
    const poll = () => {
      const match = stdout.match(/(?:^|\n)READY:(\d+)(?:\r?\n|$)/);
      if (match) return resolve({ child, port: Number(match[1]) });
      if (child.exitCode !== null || Date.now() > deadline) {
        return reject(new Error(`Loopback Vega-IDE receiver did not start (stdout: ${stdout.slice(-1000)}; stderr: ${stderr.slice(-2000)}).`));
      }
      setTimeout(poll, 50);
    };
    poll();
  });
}

async function stopChild(child) {
  if (!child || child.exitCode !== null) return;
  if (process.platform === "win32") {
    const killed = spawnSync("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], {
      encoding: "utf8", windowsHide: true, stdio: "ignore"
    });
    if (killed.status !== 0) child.kill();
  } else child.kill("SIGTERM");
  await Promise.race([
    new Promise((resolve) => child.once("exit", resolve)),
    new Promise((resolve) => setTimeout(resolve, 5000))
  ]);
  if (child.exitCode === null && child.signalCode === null) throw new Error("A controlled E2E child process did not stop.");
}

function findBrowser() {
  const candidates = [
    process.env.VEGA_BROWSER,
    process.env.PROGRAMFILES && path.join(process.env.PROGRAMFILES, "Microsoft", "Edge", "Application", "msedge.exe"),
    process.env["PROGRAMFILES(X86)"] && path.join(process.env["PROGRAMFILES(X86)"], "Microsoft", "Edge", "Application", "msedge.exe"),
    process.env.PROGRAMFILES && path.join(process.env.PROGRAMFILES, "Google", "Chrome", "Application", "chrome.exe"),
    process.env["PROGRAMFILES(X86)"] && path.join(process.env["PROGRAMFILES(X86)"], "Google", "Chrome", "Application", "chrome.exe"),
    "/usr/bin/microsoft-edge", "/usr/bin/microsoft-edge-stable", "/usr/bin/chromium", "/usr/bin/google-chrome"
  ].filter(Boolean);
  return candidates.find((candidate) => fs.existsSync(candidate)) || null;
}

async function waitUntil(predicate, description, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const result = await predicate();
      if (result) return result;
    } catch (_) {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for ${description}.`);
}

class DevTools {
  constructor(webSocketUrl) {
    this.ws = new WebSocket(webSocketUrl);
    this.nextId = 1;
    this.sessionId = null;
    this.pending = new Map();
    this.listeners = new Map();
    this.open = new Promise((resolve, reject) => {
      this.ws.addEventListener("open", resolve, { once: true });
      this.ws.addEventListener("error", reject, { once: true });
    });
    this.ws.addEventListener("message", (message) => {
      let item;
      try { item = JSON.parse(String(message.data)); } catch (_) { return; }
      if (item.id && this.pending.has(item.id)) {
        const pending = this.pending.get(item.id);
        this.pending.delete(item.id);
        if (item.error) pending.reject(new Error(item.error.message || "CDP request failed"));
        else pending.resolve(item.result || {});
      } else if (item.method) {
        const params = item.params || {};
        if (item.sessionId) params.sessionId = item.sessionId;
        for (const listener of this.listeners.get(item.method) || []) listener(params);
      }
    });
  }

  on(name, listener) {
    if (!this.listeners.has(name)) this.listeners.set(name, new Set());
    this.listeners.get(name).add(listener);
  }

  send(method, params = {}, timeoutMs = 15_000, sessionId = this.sessionId) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP command timed out: ${method}`));
      }, timeoutMs);
      this.pending.set(id, {
        resolve: (value) => { clearTimeout(timer); resolve(value); },
        reject: (error) => { clearTimeout(timer); reject(error); }
      });
      const command = { id, method, params };
      if (sessionId) command.sessionId = sessionId;
      this.ws.send(JSON.stringify(command));
    });
  }

  async evaluate(expression, timeoutMs = 20_000) {
    let timer;
    const result = await Promise.race([
      this.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true, userGesture: true }),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("Browser evaluation timed out.")), timeoutMs); })
    ]).finally(() => clearTimeout(timer));
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "Browser evaluation failed.");
    return result.result && result.result.value;
  }

  close() {
    try { this.ws.close(); } catch (_) {}
  }
}

async function startBrowser(parent, url) {
  const executable = findBrowser();
  if (!executable) throw new Error("No local Edge or Chrome executable was found for headless CDP.");
  const profile = fs.mkdtempSync(path.join(parent, "edge-profile-"));
  const browserArgs = [
    "--headless", "--no-first-run", "--disable-gpu", "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows", "--disable-background-networking", "--disable-extensions",
    "--disable-sync", "--disable-features=msEdgeSidebarV2", "--no-proxy-server", `--user-data-dir=${profile}`,
    "--remote-debugging-port=0", "about:blank"
  ];
  const child = spawn(executable, [
    ...browserArgs
  ], { cwd: parent, env: safeChildEnv(), shell: false, windowsHide: true, stdio: "ignore" });
  const activePortFile = path.join(profile, "DevToolsActivePort");
  let cdp;
  let browserCdp;
  try {
    await waitUntil(async () => fs.existsSync(activePortFile), "headless browser DevTools port", 30_000);
    const activePortLines = fs.readFileSync(activePortFile, "utf8").split(/\r?\n/).filter(Boolean);
    const portText = activePortLines[0].trim();
    const debuggerUrl = `http://127.0.0.1:${portText}`;
    const expected = new URL(url);
    const browserWebSocketPath = activePortLines[1];
    if (!browserWebSocketPath || !browserWebSocketPath.startsWith("/devtools/browser/")) {
      throw new Error("Headless browser did not publish its browser-level DevTools endpoint.");
    }
    browserCdp = new DevTools(`ws://127.0.0.1:${portText}${browserWebSocketPath}`);
    await browserCdp.open;
    const created = await browserCdp.send("Target.createTarget", { url, background: false });
    if (!created.targetId) throw new Error("Headless browser did not create a Vega page target.");
    let latestTargets = [];
    const target = await waitUntil(async () => {
      const response = await fetch(`${debuggerUrl}/json/list`);
      if (!response.ok) return null;
      latestTargets = await response.json();
      return latestTargets.find((item) => item.type === "page" && item.id === created.targetId);
    }, "headless Vega page target", 30_000).catch(() => {
      const summary = latestTargets.map((item) => ({ type: item.type, url: item.url, title: item.title }));
      throw new Error(`Headless browser did not load ${expected.origin}; targets=${JSON.stringify(summary)}`);
    });
    const attached = await browserCdp.send("Target.attachToTarget", { targetId: target.id, flatten: true });
    if (!attached.sessionId) throw new Error("Headless browser did not attach a CDP page session.");
    cdp = browserCdp;
    cdp.sessionId = attached.sessionId;
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Network.enable");
    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width: 1280, height: 1100, deviceScaleFactor: 1, mobile: false
    });
    const initialState = JSON.parse(await cdp.evaluate("JSON.stringify({ href: location.href, ready: document.readyState })"));
    if (initialState.href !== url) {
      await cdp.send("Page.navigate", { url }, 15_000);
    }
    let latestPageState = null;
    await waitUntil(async () => {
      latestPageState = JSON.parse(await cdp.evaluate("JSON.stringify({ href: location.href, ready: document.readyState, title: document.title, compiler: !!window.VegaSpecCompiler })"));
      return latestPageState.href === url && latestPageState.ready === "complete" && latestPageState.compiler;
    }, "Vega page readiness", 20_000).catch(() => {
      throw new Error(`Vega page did not finish loading; page=${JSON.stringify(latestPageState)}`);
    });
    return { child, profile, cdp, debuggerUrl, browser: path.basename(executable), activeTargetId: target.id };
  } catch (error) {
    if (cdp) cdp.close();
    else if (browserCdp) browserCdp.close();
    try { await stopChild(child); } finally { removeWithin(parent, profile); }
    throw error;
  }
}

function makeCollector(cdp) {
  const issues = [];
  const sockets = [];
  const isActiveSession = (event) => !event.sessionId || event.sessionId === cdp.sessionId;
  cdp.on("Runtime.consoleAPICalled", (event) => {
    if (!isActiveSession(event)) return;
    if (event.type === "error") issues.push({ kind: "console.error", message: (event.args || []).map((arg) => arg.value || arg.description || "").join(" ").slice(0, 500) });
  });
  cdp.on("Runtime.exceptionThrown", (event) => {
    if (!isActiveSession(event)) return;
    issues.push({ kind: "pageerror", message: String(event.exceptionDetails && (event.exceptionDetails.text || event.exceptionDetails.exception && event.exceptionDetails.exception.description) || "uncaught exception").slice(0, 500) });
  });
  cdp.on("Network.responseReceived", (event) => {
    if (!isActiveSession(event)) return;
    if (event.response && event.response.status >= 400) {
      issues.push({ kind: "http", status: event.response.status, url: String(event.response.url || "").replace(/([?&](?:token|key|password)=)[^&]*/ig, "$1[REDACTED]").slice(0, 500) });
    }
  });
  cdp.on("Network.loadingFailed", (event) => {
    if (!isActiveSession(event)) return;
    if (event.type === "WebSocket" || String(event.errorText || "").toLowerCase().includes("websocket")) sockets.push(String(event.errorText || "websocket failed"));
    else issues.push({ kind: "network", message: String(event.errorText || "request failed").slice(0, 300) });
  });
  cdp.on("Network.webSocketCreated", (event) => sockets.push(String(event.url || "websocket")));
  return { issues, sockets };
}

async function getListenerAudit(cdp) {
  const inspect = async (id) => {
    const result = await cdp.send("Runtime.evaluate", {
      expression: `document.getElementById(${JSON.stringify(id)})`,
      returnByValue: false
    });
    const objectId = result.result && result.result.objectId;
    if (!objectId) return [];
    const listeners = await cdp.send("DOMDebugger.getEventListeners", { objectId });
    return (listeners.listeners || []).map((item) => ({ id, type: item.type }));
  };
  return [...await inspect("prompt-input"), ...await inspect("send-btn")];
}

async function auditPage(cdp) {
  return cdp.evaluate(`JSON.stringify((() => {
    const controls = Array.from(document.querySelectorAll('input, textarea, select'));
    const labels = Array.from(document.querySelectorAll('label[for]'));
    const ids = Array.from(document.querySelectorAll('[id]')).map((item) => item.id);
    const duplicates = ids.filter((id, index) => ids.indexOf(id) !== index);
    return {
      title: document.title,
      missingControlNames: controls.filter((item) => !item.id || !item.name).map((item) => item.outerHTML.slice(0, 160)),
      brokenLabels: labels.filter((label) => !document.getElementById(label.htmlFor)).map((label) => label.outerHTML.slice(0, 160)),
      duplicateIds: Array.from(new Set(duplicates)),
      voiceSurface: window.vegaActiveVoiceSurface || null,
      composerReady: !!document.getElementById('prompt-input') && !!document.getElementById('send-btn'),
      specCardCount: document.querySelectorAll('.spec-draft-card').length,
      bodyOverflow: document.body.scrollWidth > document.documentElement.clientWidth + 1
    };
  })())`);
}

async function assertCleanPage(cdp, collector, passName) {
  await new Promise((resolve) => setTimeout(resolve, 600));
  const audit = JSON.parse(await auditPage(cdp));
  audit.listenerAudit = await getListenerAudit(cdp);
  assert.deepEqual(audit.missingControlNames, [], `${passName}: inputs need unique id/name attributes`);
  assert.deepEqual(audit.brokenLabels, [], `${passName}: labels must resolve to controls`);
  assert.deepEqual(audit.duplicateIds, [], `${passName}: DOM ids must be unique`);
  assert.equal(audit.composerReady, true, `${passName}: chat composer is present`);
  assert.ok(audit.listenerAudit.some((item) => item.id === "prompt-input" && item.type === "keydown"), `${passName}: Enter listener is registered`);
  assert.ok(audit.listenerAudit.some((item) => item.id === "send-btn" && item.type === "click"), `${passName}: Send listener is registered`);
  assert.deepEqual(collector.issues, [], `${passName}: browser console, page, HTTP, and network errors`);
  assert.deepEqual(collector.sockets, [], `${passName}: no websocket use or error`);
  return audit;
}

async function captureScreenshot(cdp, destination) {
  const result = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  fs.writeFileSync(destination, Buffer.from(result.data, "base64"));
}

async function main() {
  if (!fs.existsSync(IDE_ROOT)) throw new Error("Vega-IDE-Build repo was not found next to mybot.");
  const sourceTemp = createSourceSnapshot();
  const sandbox = fs.mkdtempSync(path.join(IDE_ROOT, "tests", ".vega-live-sandbox-"));
  const targetFile = path.join(sandbox, "probe.js");
  fs.writeFileSync(targetFile, 'const value = "before";\n');
  const browserTemp = fs.mkdtempSync(path.join(REPO_ROOT, "tests", ".vega-edge-e2e-"));
  let browser;
  let serverA;
  let serverB;
  let receiver;
  let collector;
  const report = { browser: null, passA: null, passB: null };
  try {
    console.log("[e2e] starting isolated Vega with bridge disabled");
    serverA = await startVega(sourceTemp.snapshot, false, "http://127.0.0.1:47640");
    const httpProbe = await fetch(`http://127.0.0.1:${serverA.port}/`);
    console.log(`[e2e] isolated Vega page HTTP ${httpProbe.status}`);
    await httpProbe.text();
    const passAUrl = `http://127.0.0.1:${serverA.port}/`;
    console.log("[e2e] launching headless browser on the served page");
    browser = await startBrowser(browserTemp, passAUrl);
    report.browser = browser.browser;
    console.log("[e2e] headless browser ready");
    collector = makeCollector(browser.cdp);
    const passAUi = await assertCleanPage(browser.cdp, collector, "Pass A");
    const disabled = await browser.cdp.evaluate(`(async () => {
      const result = await window.VegaSpecCompiler.compileTranscript('Um, add a summary card.');
      return JSON.stringify({ result, cardCount: document.querySelectorAll('.spec-draft-card').length });
    })()`);
    const disabledResult = JSON.parse(disabled);
    assert.equal(disabledResult.result.disabled, true, "Pass A bridge routes remain disabled");
    assert.equal(disabledResult.cardCount, 0, "Pass A remains inert");
    await assertCleanPage(browser.cdp, collector, "Pass A final");
    report.passA = {
      consoleErrors: collector.issues.filter((item) => item.kind === "console.error").length,
      pageErrors: collector.issues.filter((item) => item.kind === "pageerror").length,
      httpOrNetworkErrors: collector.issues.filter((item) => item.kind === "http" || item.kind === "network").length,
      websocketErrors: collector.sockets.length,
      specCardCount: disabledResult.cardCount,
      composerListeners: passAUi.listenerAudit
    };
    browser.cdp.close();
    await stopChild(browser.child);
    removeWithin(browserTemp, browser.profile);
    browser = null;
    await stopChild(serverA.child);
    serverA = null;

    console.log("[e2e] starting loopback IDE receiver and enabled Vega");
    receiver = await startReceiver(sandbox);
    serverB = await startVega(sourceTemp.snapshot, true, `http://127.0.0.1:${receiver.port}`);
    browser = await startBrowser(browserTemp, `http://127.0.0.1:${serverB.port}/`);
    report.browser = browser.browser;
    collector = makeCollector(browser.cdp);
    const transcript = "Um, add a dashboard, actually keep it to a summary card instead of a full dashboard.";
    const compile = await browser.cdp.evaluate(`(async () => {
      const transcript = ${JSON.stringify(transcript)};
      const result = await window.VegaSpecCompiler.compileTranscript(transcript);
      if (!result || !result.ok || !result.payload) return JSON.stringify({ result });
      window.__vegaE2ePayload = result.payload;
      const card = window.VegaSpecCompiler.renderDraftSpecCard('messages-container', result.payload, transcript);
      return JSON.stringify({ ok: !!card, version: card && card.querySelector('.spec-version-badge').textContent,
        approved: card && card.dataset.approvedByUser, context: result.payload.ide_context });
    })()`);
    const compileResult = JSON.parse(compile);
    assert.equal(compileResult.ok, true, "Pass B renders the compiled spec card");
    assert.equal(compileResult.version, "v1");
    assert.equal(compileResult.approved, "false");
    assert.equal(compileResult.context.ide_online, true, "Live loopback receiver supplies IDE context");
    assert.equal(compileResult.context.workspace_root, sandbox, "Temp workspace path reaches the reviewed contract");
    const initialCardText = await browser.cdp.evaluate("document.querySelector('.spec-draft-card').innerText");
    for (const text of ["Goal", "In Scope", "Out of Scope", "Transcript:", "Interpreted:", "Not approved"]) {
      assert.ok(initialCardText.includes(text), `Draft card displays ${text}`);
    }

    const reviseSetup = await browser.cdp.evaluate(`(() => {
      const card = document.querySelector('.spec-draft-card');
      card.querySelector('.spec-target-files').value = 'probe.js';
      card.querySelector('.spec-revision-feedback').value = 'Actually use a concise summary card and keep it in scope.';
      window.addEventListener('vega:spec:revised', (event) => {
        if (event.detail && event.detail.payload) window.__vegaE2eRevisedPayload = event.detail.payload;
      }, { once: true });
      const button = Array.from(card.querySelectorAll('button')).find((item) => item.textContent.trim() === 'Revise');
      button.click();
      return 'started';
    })()`);
    assert.equal(reviseSetup, "started");
    await waitUntil(() => browser.cdp.evaluate("document.querySelector('.spec-version-badge')?.textContent === 'v2'"), "v2 revised spec");
    const revisedMeta = JSON.parse(await browser.cdp.evaluate(`JSON.stringify({
      approved: document.querySelector('.spec-draft-card').dataset.approvedByUser,
      target: document.querySelector('.spec-target-files').value,
      payload: window.__vegaE2eRevisedPayload
    })`));
    assert.equal(revisedMeta.approved, "false", "Revision resets approval");
    assert.equal(revisedMeta.target, "probe.js", "Revision retains the reviewed target file");
    assert.equal(revisedMeta.payload.spec_version, 2);
    assert.equal(revisedMeta.payload.ide_context.ide_online, true);

    const blocked = await browser.cdp.evaluate(`(async () => {
      const payload = { ...window.__vegaE2eRevisedPayload, approved_by_user: false,
        approval_tier: 'apply_edits', mode: 'plan_and_apply',
        file_operations: [{ op: 'replace', path: 'probe.js', search: 'before', replace: 'after' }] };
      return JSON.stringify(await window.VegaSpecCompiler.dispatchSpec(payload));
    })()`);
    const blockedResult = JSON.parse(blocked);
    assert.equal(blockedResult.status, "awaiting_approval", "Unapproved apply is stopped by Vega");
    assert.equal(fs.readFileSync(targetFile, "utf8"), 'const value = "before";\n', "Unapproved dispatch performs no write");

    const approve = await browser.cdp.evaluate(`(() => {
      const card = document.querySelector('.spec-draft-card');
      card.querySelector('.spec-file-operations').value = JSON.stringify([{
        op: 'replace', path: 'probe.js', search: 'const value = "before";', replace: 'const value = "after";'
      }]);
      window.addEventListener('vega:spec:approval-result', (event) => {
        window.__vegaE2eApprovedRun = event.detail;
      }, { once: true });
      const button = Array.from(card.querySelectorAll('button')).find((item) => item.textContent.trim() === 'Approve & Apply');
      button.click();
      return 'started';
    })()`);
    assert.equal(approve, "started");
    const finalStatus = await waitUntil(async () => {
      const value = await browser.cdp.evaluate(`(async () => {
        const result = window.__vegaE2eApprovedRun;
        if (!result || !result.ok || !result.spec_id) return '';
        const snapshot = await window.VegaSpecCompiler.fetchSpecStatus(result.spec_id);
        if (!snapshot || !snapshot.state) return '';
        window.__vegaE2eStatus = snapshot;
        window.__vegaE2eSeqs = window.__vegaE2eSeqs || [];
        if (window.__vegaE2eSeqs.at(-1) !== snapshot.state.seq) window.__vegaE2eSeqs.push(snapshot.state.seq);
        return JSON.stringify(snapshot);
      })()`);
      if (!value) return null;
      const snapshot = JSON.parse(value);
      return snapshot.state.status === "completed" || snapshot.state.status === "failed" ? snapshot : null;
    }, "terminal handoff status", 30_000);
    assert.equal(finalStatus.state.status, "completed", JSON.stringify(finalStatus.state));
    const statusSeqs = JSON.parse(await browser.cdp.evaluate("JSON.stringify(window.__vegaE2eSeqs || [])"));
    assert.ok(statusSeqs.length > 0 && statusSeqs.every((seq, index) => index === 0 || seq > statusSeqs[index - 1]), "Status sequences observed by the UI are strictly increasing");
    assert.deepEqual(finalStatus.state.changed_files, ["probe.js"]);
    assert.equal(finalStatus.debrief.should_speak, false, "Silent test configuration suppresses voice debrief");
    assert.ok(finalStatus.state.checks.some((check) => check.check === "node --check" && check.status === "passed"));
    assert.equal(fs.readFileSync(targetFile, "utf8"), 'const value = "after";\n', "Approved loopback executor edits the sandbox file");
    const cardStatusText = await browser.cdp.evaluate("document.querySelector('.spec-run-panel').innerText");
    assert.match(cardStatusText, /Completed/);
    assert.match(cardStatusText, /probe\.js/);
    assert.match(cardStatusText, /node --check/);

    const metrics = JSON.parse(await browser.cdp.evaluate(`JSON.stringify((() => {
      const card = document.querySelector('.spec-draft-card');
      const overflow = [card, ...card.querySelectorAll('*')]
        .filter((element) => element.scrollWidth > element.clientWidth + 1)
        .map((element) => ({ tag: element.tagName, className: String(element.className || '').slice(0, 100), scroll: element.scrollWidth, client: element.clientWidth }));
      return { overflow, bodyOverflow: document.body.scrollWidth > document.documentElement.clientWidth + 1,
        voiceSurface: window.vegaActiveVoiceSurface, cardWidth: card.getBoundingClientRect().width,
        viewportWidth: document.documentElement.clientWidth };
    })())`));
    assert.deepEqual(metrics.overflow, [], "Spec card has no clipped or overflowing child elements");
    assert.equal(metrics.bodyOverflow, false, "Page has no horizontal overflow");
    assert.equal(metrics.voiceSurface, "chat", "Bridge leaves the Focus Orb/chat surface unchanged");
    await captureScreenshot(browser.cdp, path.join(browserTemp, "pass-b-spec-card.png"));
    const finalAudit = await assertCleanPage(browser.cdp, collector, "Pass B final");
    report.passB = {
      consoleErrors: collector.issues.filter((item) => item.kind === "console.error").length,
      pageErrors: collector.issues.filter((item) => item.kind === "pageerror").length,
      httpOrNetworkErrors: collector.issues.filter((item) => item.kind === "http" || item.kind === "network").length,
      websocketErrors: collector.sockets.length,
      compile: { version: compileResult.version, ideOnline: compileResult.context.ide_online },
      revisedVersion: revisedMeta.payload.spec_version,
      unapprovedDispatch: blockedResult.status,
      terminalStatus: finalStatus.state.status,
      statusSeqs,
      changedFiles: finalStatus.state.changed_files,
      checks: finalStatus.state.checks,
      rolledBack: finalStatus.state.rolled_back || false,
      debriefShouldSpeak: finalStatus.debrief.should_speak,
      layout: metrics,
      composerListeners: finalAudit.listenerAudit
    };
    const artifactDirectory = process.env.VEGA_E2E_ARTIFACT_DIR;
    if (artifactDirectory) {
      fs.mkdirSync(artifactDirectory, { recursive: true });
      fs.copyFileSync(path.join(browserTemp, "pass-b-spec-card.png"), path.join(artifactDirectory, "pass-b-spec-card.png"));
    }
    assert.deepEqual(collector.issues, [], "Pass B browser errors remain at zero");
    assert.deepEqual(collector.sockets, [], "Pass B has no WebSocket connection or error");
    console.log(JSON.stringify(report, null, 2));
  } finally {
    if (browser) {
      if (browser.cdp) browser.cdp.close();
    }
    const children = [browser && browser.child, serverA && serverA.child, serverB && serverB.child, receiver && receiver.child];
    for (const child of children) {
      try { await stopChild(child); } catch (_) {}
    }
    for (const [parent, candidate] of [
      [IDE_ROOT, sandbox], [REPO_ROOT, browserTemp], [REPO_ROOT, sourceTemp.parent]
    ]) {
      try { removeWithin(parent, candidate); } catch (_) {}
    }
  }
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exitCode = 1;
});
