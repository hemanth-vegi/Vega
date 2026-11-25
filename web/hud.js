/**
 * web/hud.js - Unified Web Audio Lifecycle Manager & Arc Reactor HUD Visualizer
 * Manages a single shared AudioContext, reference-counted MediaStream sharing
 * between HUD and AI Meeting Notes, and real-time frequency-driven ring visuals.
 */

const ORB_STATE_PROFILES = {
  idle: {
    '--orb-ring-outer-speed': '40s',
    '--orb-ring-mid-speed': '22s',
    '--orb-ring-inner-speed': '12s',
    '--orb-ring-pulse-duration': '6s',
    '--orb-pulse-scale': '1.018',
    '--orb-core-pulse-duration': '4.2s',
    '--orb-core-pulse-scale': '1.035',
    '--orb-glow-blur': '34px'
  },
  listening: {
    '--orb-ring-outer-speed': '22s',
    '--orb-ring-mid-speed': '13s',
    '--orb-ring-inner-speed': '7.5s',
    '--orb-ring-pulse-duration': '2.6s',
    '--orb-pulse-scale': '1.06',
    '--orb-core-pulse-duration': '2.5s',
    '--orb-core-pulse-scale': '1.08',
    '--orb-glow-blur': '62px'
  },
  thinking: {
    '--orb-ring-outer-speed': '32s',
    '--orb-ring-mid-speed': '18s',
    '--orb-ring-inner-speed': '10s',
    '--orb-ring-pulse-duration': '3.6s',
    '--orb-pulse-scale': '1.04',
    '--orb-core-pulse-duration': '3.4s',
    '--orb-core-pulse-scale': '1.055',
    '--orb-glow-blur': '48px'
  },
  speaking: {
    '--orb-ring-outer-speed': '18s',
    '--orb-ring-mid-speed': '10s',
    '--orb-ring-inner-speed': '6s',
    '--orb-ring-pulse-duration': '1.9s',
    '--orb-pulse-scale': '1.075',
    '--orb-core-pulse-duration': '2s',
    '--orb-core-pulse-scale': '1.09',
    '--orb-glow-blur': '72px'
  }
};

class VegaAudioManager {
  constructor() {
    this.sharedStream = null;
    this.consumers = new Set();
    this._unlockHandler = null;
    this.currentTurnId = null;
    this.lastSpokenText = "";
    this.lastSpokenTs = 0;
    this.currentAudio = null;
    this.bindGlobalUnlock();
    this.bindTeardown();
  }

  cancelAudio() {
    if (this.currentAudio) {
      try {
        this.currentAudio.pause();
        this.currentAudio.currentTime = 0;
      } catch (e) {}
      this.currentAudio = null;
    }
    if (window.__vegaAudioPlayer) {
      try {
        window.__vegaAudioPlayer.pause();
        window.__vegaAudioPlayer.currentTime = 0;
      } catch (e) {}
      window.__vegaAudioPlayer = null;
    }
    if (typeof window !== 'undefined' && window.speechSynthesis) {
      try {
        window.speechSynthesis.cancel();
      } catch (e) {}
    }
  }

  shouldPlaySpeech(text, turnId = null) {
    const clean = (text || '').trim();
    if (!clean) return false;
    const now = Date.now();
    if (turnId && this.currentTurnId === turnId && (now - this.lastSpokenTs < 15000)) {
      return false;
    }
    if (clean === this.lastSpokenText && (now - this.lastSpokenTs < 3500)) {
      return false;
    }
    this.currentTurnId = turnId || ('turn_' + now + '_' + Math.random().toString(36).slice(2, 7));
    this.lastSpokenText = clean;
    this.lastSpokenTs = now;
    return true;
  }

  getContext() {
    if (window.audioCtx && window.audioCtx.state === 'closed') {
      window.audioCtx = null;
    }
    return window.audioCtx || null;
  }

  async ensureRunningContext() {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) {
      throw new Error('Web Audio API is not supported in this browser.');
    }

    if (!window.audioCtx || window.audioCtx.state === 'closed') {
      window.audioCtx = new AudioCtx();
    }

    if (window.audioCtx.state === 'suspended') {
      try {
        await window.audioCtx.resume();
        console.log('[QA System] AudioContext globally resumed.');
      } catch (err) {
        console.error('AudioContext resume failed:', err);
        throw err;
      }
    }

    if (window.audioCtx.state !== 'running') {
      throw new Error(`AudioContext is not running: ${window.audioCtx.state}`);
    }

    return window.audioCtx;
  }

  bindGlobalUnlock() {
    // Prevent duplicate global listener registration across re-inits
    if (window.__vegaAudioUnlockBound) return;
    window.__vegaAudioUnlockBound = true;

    const unlockAudio = async () => {
      try {
        if (!window.audioCtx) return;
        if (window.audioCtx.state === 'closed') {
          window.audioCtx = null;
          return;
        }
        if (window.audioCtx.state === 'suspended') {
          await window.audioCtx.resume();
          console.log('[QA System] AudioContext globally resumed.');
        }
        if (window.audioCtx.state === 'running') {
          if (typeof target.removeEventListener === 'function') {
            target.removeEventListener('click', unlockAudio);
            target.removeEventListener('touchstart', unlockAudio);
            target.removeEventListener('keydown', unlockAudio);
          }
        }
      } catch (err) {
        console.warn('AudioContext interaction resume warning:', err);
      }
    };

    this._unlockHandler = unlockAudio;
    const target = document.body || document;
    // Persistent listener (never consumed before AudioContext is lazily created)
    target.addEventListener('click', unlockAudio);
    target.addEventListener('touchstart', unlockAudio, { passive: true });
    target.addEventListener('keydown', unlockAudio);
  }

  async acquireStream(consumerId) {
    if (!consumerId) throw new Error('consumerId is required to acquire microphone stream.');

    if (this.sharedStream && this.sharedStream.active) {
      this.consumers.add(consumerId);
      return this.sharedStream;
    }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error('navigator.mediaDevices.getUserMedia is unavailable in this browser context.');
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      this.sharedStream = stream;
      this.consumers.add(consumerId);
      return stream;
    } catch (micErr) {
      if (micErr.name === 'NotAllowedError' || micErr.name === 'PermissionDeniedError') {
        console.error('DOMException NotAllowedError: Microphone access was denied by user or permission policy.', micErr);
      } else if (micErr.name === 'SecurityError') {
        console.error('DOMException SecurityError: Microphone access requires HTTPS or localhost.', micErr);
      } else if (micErr.name === 'NotFoundError' || micErr.name === 'DevicesNotFoundError') {
        console.error('DOMException NotFoundError: No microphone input hardware found.', micErr);
      } else if (micErr.name === 'NotReadableError' || micErr.name === 'TrackStartError') {
        console.error('DOMException NotReadableError: Microphone is busy or locked by another application.', micErr);
      } else {
        console.error(`Microphone initialization DOMException (${micErr.name || 'Error'}):`, micErr.message || micErr);
      }
      throw micErr;
    }
  }

  releaseStream(consumerId) {
    if (consumerId) {
      this.consumers.delete(consumerId);
    }
    // Only stop hardware tracks when no active consumer (HUD or Meeting Note) needs the mic
    if (this.consumers.size === 0 && this.sharedStream) {
      try {
        this.sharedStream.getTracks().forEach(track => track.stop());
      } catch (e) {
        console.warn('Error stopping shared microphone tracks:', e);
      }
      this.sharedStream = null;
    }
  }

  teardown() {
    this.consumers.clear();
    if (this._unlockHandler) {
      const target = document.body || document;
      if (typeof target.removeEventListener === 'function') {
        target.removeEventListener('click', this._unlockHandler);
        target.removeEventListener('touchstart', this._unlockHandler);
        target.removeEventListener('keydown', this._unlockHandler);
      }
    }
    if (this.sharedStream) {
      try {
        this.sharedStream.getTracks().forEach(track => track.stop());
      } catch (e) {}
      this.sharedStream = null;
    }
    if (window.audioCtx && window.audioCtx.state !== 'closed') {
      try {
        window.audioCtx.close();
      } catch (e) {}
      window.audioCtx = null;
    }
  }

  bindTeardown() {
    if (window.__vegaAudioTeardownBound) return;
    window.__vegaAudioTeardownBound = true;
    window.addEventListener('beforeunload', () => this.teardown());
  }
}

window.VegaAudioManager = window.VegaAudioManager || new VegaAudioManager();

class ArcReactorHUD {
  constructor() {
    this.isActive = false;
    this.audioCtx = null;
    this.sourceNode = null;
    this.analyser = null;
    this.microphoneStream = null;
    this.animFrameId = null;
    this.fallbackFrameId = null;
    this.telemetryInterval = null;
    this.audioDataConfirmed = false;
    this.ws = null;
    this._listeningTimer = null;

    this.initElements();
    this.bindEvents();
    this.fetchPersona();
  }

  initElements() {
    this.view = document.getElementById('hud-view');
    this.coreElement = document.getElementById('reactor-core');
    this.ringOuter = document.querySelector('.reactor-ring-outer');
    this.ringMid = document.querySelector('.reactor-ring-mid');
    this.ringInner = document.querySelector('.reactor-ring-inner');

    this.hudCpu = document.getElementById('hud-stat-cpu');
    this.hudRam = document.getElementById('hud-stat-ram');
    this.hudVram = document.getElementById('hud-stat-vram');
    this.hudModel = document.getElementById('hud-stat-model');
    this.hudTranscript = document.getElementById('hud-transcript');
    this.telemetryPill = document.getElementById('telemetry-pill');
    this.telemetryPillText = document.getElementById('telemetry-pill-text');

    // Phase 10: State Badge & Stop Buttons
    this.stateBadge = document.getElementById('hud-state-badge');
    this.interruptBtn = document.getElementById('hud-interrupt-btn');
    this.bottomStopBtn = document.getElementById('hud-bottom-stop-btn');

    // Phase 10: Live Side Strip
    this.stripTasks = document.getElementById('hud-strip-tasks');
    this.stripNetwork = document.getElementById('hud-strip-network');
    this.stripPersona = document.getElementById('hud-strip-persona');
    this.stripCoach = document.getElementById('hud-strip-coach');

    // Phase 10: Quick Action Pills
    this.pillClipboard = document.getElementById('hud-pill-clipboard');
    this.pillScreen = document.getElementById('hud-pill-screen');
    this.pillInterview = document.getElementById('hud-pill-interview');
    this.pillPersona = document.getElementById('hud-pill-persona');
    this.pillCoach = document.getElementById('hud-pill-coach');
    this.statOrchestrator = document.getElementById('hud-stat-orchestrator');

    // Phase 10: Text Input Bar
    this.textInput = document.getElementById('hud-text-input');
    this.textSendBtn = document.getElementById('hud-text-send-btn');
    this.micBtn = document.getElementById('hud-mic-btn');

    // Compact Voice & HUD Control Bar Elements
    this.compactBar = document.getElementById('compact-hud-bar');
    this.compactStatus = document.getElementById('hud-compact-status');
    this.compactStatusText = document.getElementById('hud-compact-status-text');
    this.compactEngine = document.getElementById('hud-compact-engine');
    this.compactCpu = document.getElementById('hud-compact-cpu');
    this.compactRam = document.getElementById('hud-compact-ram');
    this.compactVram = document.getElementById('hud-compact-vram');
    this.compactBrain = document.getElementById('hud-compact-brain');
    this.btnFocusOrb = document.getElementById('hud-btn-focus-orb');
    this.btnBackToChat = document.getElementById('hud-btn-back-to-chat');
    this.pillSleep = document.getElementById('hud-pill-sleep');
    this.isFocusActive = false;

    this.state = 'idle';
    this.personaMode = 'friend';
    this.englishCoachEnabled = false;
    this.isSleeping = false;
    this.silenceTimeoutSec = 90;
    this._silenceTimer = null;
    this._mediaRecorder = null;
    this._recordedChunks = [];
    this._isRecordingUtterance = false;
    this._speechSilenceFrames = 0;
  }

  bindEvents() {
    // Focus Orb & Return to Chat Handlers
    if (this.btnFocusOrb) {
      this.btnFocusOrb.addEventListener('click', () => this.toggleFocusMode(!this.isFocusActive));
    }
    if (this.btnBackToChat) {
      this.btnBackToChat.addEventListener('click', () => this.toggleFocusMode(false));
    }
    if (this.pillSleep) {
      this.pillSleep.addEventListener('click', () => this.toggleSleep());
    }
    if (this.compactStatus) {
      this.compactStatus.addEventListener('click', () => this.toggleSleep());
    }

    // Clicking state badge toggles Silent / Sleep mode
    if (this.stateBadge) {
      this.stateBadge.addEventListener('click', () => this.toggleSleep());
    }

    const interviewSubmitBtn = document.getElementById('hud-interview-submit-btn');
    if (interviewSubmitBtn) {
      interviewSubmitBtn.addEventListener('click', () => this.submitInterviewAnswer());
    }
    const interviewNextBtn = document.getElementById('hud-interview-next-btn');
    if (interviewNextBtn) {
      interviewNextBtn.addEventListener('click', () => this.nextInterviewQuestion());
    }
    const interviewEndBtn = document.getElementById('hud-interview-end-btn');
    if (interviewEndBtn) {
      interviewEndBtn.addEventListener('click', () => this.finishInterview());
    }
    const interviewExitBtn = document.getElementById('hud-interview-exit-btn');
    if (interviewExitBtn) {
      interviewExitBtn.addEventListener('click', () => this.exitInterview());
    }

    // Stop / Interrupt Handlers
    if (this.interruptBtn) {
      this.interruptBtn.addEventListener('click', () => this.abortAll());
    }
    if (this.bottomStopBtn) {
      this.bottomStopBtn.addEventListener('click', () => this.abortAll());
    }

    // Quick Action Pills Handlers (Both Compact Bar and Focus Orb)
    document.querySelectorAll('#hud-pill-clipboard, .hud-pill-clipboard-btn').forEach(btn => {
      btn.addEventListener('click', () => this.handleExplainClipboard());
    });
    document.querySelectorAll('#hud-pill-screen, .hud-pill-screen-btn').forEach(btn => {
      btn.addEventListener('click', () => this.handleLookAtScreen());
    });
    document.querySelectorAll('#hud-pill-interview, .hud-pill-interview-btn').forEach(btn => {
      btn.addEventListener('click', () => this.startMockInterview());
    });
    document.querySelectorAll('#hud-pill-persona, .hud-pill-persona-btn').forEach(btn => {
      btn.addEventListener('click', () => this.togglePersona());
    });
    document.querySelectorAll('#hud-pill-coach, .hud-pill-coach-btn').forEach(btn => {
      btn.addEventListener('click', () => this.toggleCoach());
    });
    if (this.stripCoach) {
      this.stripCoach.style.cursor = 'pointer';
      this.stripCoach.title = 'Click to toggle Interview English Coach';
      this.stripCoach.addEventListener('click', () => this.toggleCoach());
    }

    // Text Input Bar Handlers
    if (this.textSendBtn && this.textInput) {
      this.textSendBtn.addEventListener('click', () => this.sendTextMessage());
      this.textInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          this.sendTextMessage();
        }
      });
    }

    // Mic Button in HUD (with local Python mic fallback)
    if (this.micBtn) {
      this.micBtn.addEventListener('click', () => {
        if (!this.microphoneStream || !this.microphoneStream.active) {
          this.triggerLocalMicFallback();
        } else {
          const mainMicBtn = document.getElementById('listening-toggle-btn') || document.getElementById('mic-btn');
          if (mainMicBtn) mainMicBtn.click();
          else this.setListeningState(!this.view.classList.contains('listening'));
        }
      });
    }

    // Settings Modal Sync
    const personaSelect = document.getElementById('persona-mode-select');
    if (personaSelect) {
      personaSelect.addEventListener('change', () => {
        this.savePersonaSettings(personaSelect.value, this.englishCoachEnabled);
      });
    }
    const coachCheck = document.getElementById('coach-toggle-check');
    if (coachCheck) {
      coachCheck.addEventListener('change', () => {
        this.savePersonaSettings(this.personaMode, coachCheck.checked);
      });
    }

    if (this.coreElement) {
      this.coreElement.addEventListener('click', async () => {
        if (this.state === 'speaking') {
          this.abortAll();
          return;
        }
        if (this.state === 'listening') {
          this.stopVoiceCapture();
          return;
        }
        await this.startVoiceCapture();
      });
    }

    const orbModeBadge = document.getElementById('hud-active-mode-badge');
    if (orbModeBadge) {
      orbModeBadge.style.cursor = 'pointer';
      orbModeBadge.addEventListener('click', () => {
        const currentMode = (window.getAppMode ? window.getAppMode() : 'chat').toLowerCase();
        const nextMode = currentMode === 'work' ? 'chat' : 'work';
        if (window.setAppMode) {
          window.setAppMode(nextMode);
        }
        orbModeBadge.textContent = nextMode === 'work' ? 'WORK MODE' : 'CHAT MODE';
      });
    }

    // Initialize telemetry polling (every 1500ms)
    this.initTelemetryPolling();

    // Connect to SSE telemetry stream for live event pushes
    if (window.EventSource) {
      this.initSSEListener();
    }

    // Hydrate upon system lock & unlock
    window.addEventListener('vega:locked', () => {
      this.setState('locked');
      if (this.hudTranscript) {
        this.hudTranscript.textContent = 'Vega is locked. Enter PIN or Voice Unlock to resume.';
      }
    });

    window.addEventListener('vega:unlocked', () => {
      this.setState('idle');
      this.fetchOrchestratorStatus();
      this.initTelemetryPolling();
    });
  }

  initTelemetryPolling() {
    if (this.telemetryInterval) {
      clearInterval(this.telemetryInterval);
    }

    const fetchTelemetry = async () => {
      if (window.isUnlocked && !window.isUnlocked()) return;
      try {
        const fetchFn = window.apiFetch || window.fetch;
        const res = await fetchFn('/api/telemetry');
        if (!res.ok) return;
        const json = await res.json();
        const data = (json && json.data) ? json.data : json;
        if (data && typeof data === 'object') {
          this.updateTelemetry(data);
        }
      } catch (err) {
        // Quiet fallback on network blips
      }
    };

    fetchTelemetry();
    this.telemetryInterval = setInterval(fetchTelemetry, 1500);
  }

  initSSEListener() {
    try {
      const evtSource = new EventSource('/api/events');
      evtSource.onmessage = (e) => {
        try {
          const payload = JSON.parse(e.data);
          const { type, data } = payload;
          if (type === 'telemetry') {
            this.updateTelemetry(data);
          } else if (type === 'lablog_updated') {
            const streakEl = document.getElementById('hud-stat-streak');
            if (streakEl) streakEl.textContent = 'Updated Just Now';
          } else if (type === 'voice_input' || type === 'voice_transcript') {
            if (this.hudTranscript && data && data.text) {
              this.hudTranscript.textContent = `"${data.text}"`;
            }
          } else if (type === 'wake' || type === 'wake_word_detected') {
            console.log("HUD SSE: wake_word_detected event received.");
            this.setListeningState(true);
          } else if (type === 'voice_sleep') {
            this.isSleeping = !!(data && data.sleeping);
            if (this.isSleeping) this.setState('sleeping');
            else if (this.state === 'sleeping') this.setState('idle');
          } else if (type === 'listening') {
            if (data && typeof data.on === 'boolean') {
              this.setListeningState(data.on);
            }
          }
        } catch (err) {}
      };
    } catch (e) {
      console.warn('HUD SSE init:', e);
    }
  }

  initWebSocketListener() {
    // Deprecated in favor of SSE (/api/events) as single unified real-time transport
  }

  setState(state) {
    if (window.appIsLocked && state !== 'locked') {
      state = 'locked';
    }
    this.state = state || 'idle';
    if (!this.view) return;

    // Clear state classes
    this.view.classList.remove('listening', 'thinking', 'speaking', 'offline', 'sleeping', 'locked');
    if (this.state !== 'idle') {
      this.view.classList.add(this.state);
    }
    this.applyOrbProfile(this.state);

    if (this.stateBadge) {
      this.stateBadge.textContent = this.state === 'sleeping' ? 'SLEEP' : this.state.toUpperCase();
      this.stateBadge.className = `hud-state-badge ${this.state}`;
    }

    // Update compact bar pill
    if (this.compactStatus) {
      this.compactStatus.className = `compact-hud-pill ${this.state}`;
    }
    if (this.compactStatusText) {
      const labels = {
        idle: 'Idle',
        listening: 'Listening',
        thinking: 'Thinking',
        speaking: 'Speaking',
        sleeping: 'Silent — Say "Hey Vega"',
        muted: 'Muted',
        locked: 'Locked'
      };
      this.compactStatusText.textContent = labels[this.state] || this.state;
    }

    // Update Silent / Wake pill
    if (this.pillSleep) {
      this.pillSleep.textContent = this.state === 'sleeping' ? '☀️ Wake Vega' : '🌙 Go Silent';
    }

    // Sync header status badge
    if (window.updateStatusBadge) {
      if (this.state === 'locked' || window.appIsLocked) {
        window.updateStatusBadge('Locked', 'locked');
      } else {
        const headerText = this.state === 'sleeping'
          ? 'Silent — Say "Hey Vega"'
          : (this.state === 'listening' ? "Yes? I'm listening…" : (this.state === 'thinking' ? 'Thinking…' : (this.state === 'speaking' ? 'Speaking…' : (this.isFocusActive ? 'Focus Orb Active' : 'Ready'))));
        const badgeClass = this.state === 'sleeping' ? 'ready' : (this.state === 'listening' ? 'listening' : (this.state === 'thinking' ? 'thinking' : 'ready'));
        window.updateStatusBadge(headerText, badgeClass);
      }
    }

    if (this.state === 'locked') {
      this.pauseMediaRecording();
      if (this.hudTranscript) {
        this.hudTranscript.textContent = 'Vega is locked. Enter PIN or Voice Unlock to resume.';
      }
      return;
    }

    // Audio turn-taking: when speaking, ensure mic recording/listening is paused so Vega doesn't echo
    if (this.state === 'speaking') {
      this.pauseMediaRecording();
      const mainMicBtn = document.getElementById('listening-toggle-btn') || document.getElementById('mic-btn');
      if (mainMicBtn && mainMicBtn.classList.contains('recording')) {
        mainMicBtn.click();
      }
    } else if (this.state === 'sleeping') {
      this.isSleeping = true;
      this.pauseMediaRecording();
    } else {
      if (this.isSleeping && this.state !== 'sleeping') {
        this.isSleeping = false;
      }
    }
  }

  applyOrbProfile(state) {
    const profile = ORB_STATE_PROFILES[state] || ORB_STATE_PROFILES.idle;
    for (const [property, value] of Object.entries(profile)) {
      this.view.style.setProperty(property, value);
    }
  }

  setListeningState(active) {
    if (!this.view) return;
    if (active) {
      if (this.isSleeping) {
        this.toggleSleep(false);
      }
      this.setState('listening');
      if (this.hudTranscript) {
        this.hudTranscript.textContent = "Listening… say your command.";
      }
      if (this.isActive && (!this.microphoneStream || !this.microphoneStream.active)) {
        this.startAudioVisualizer();
      }
      this.resetSilenceTimer();
      if (this._listeningTimer) clearTimeout(this._listeningTimer);
      this._listeningTimer = setTimeout(() => {
        this.setListeningState(false);
      }, 10000);
    } else {
      if (this.state === 'listening') {
        this.setState('idle');
      }
      if (this._listeningTimer) {
        clearTimeout(this._listeningTimer);
        this._listeningTimer = null;
      }
    }
  }

  updateTelemetry(data) {
    if (!data) return;

    // Header Pill (preserve inner label structure)
    const pillTarget = this.telemetryPillText || document.getElementById('telemetry-pill-text');
    const vramStr = data.gpu ? ` · VRAM ${Math.round(data.gpu.vram_used_mb / 1024 * 10) / 10}G` : '';
    const telemetryString = `CPU ${data.cpu_percent}% · RAM ${data.ram_used_gb}G${vramStr}`;

    if (pillTarget) {
      pillTarget.textContent = telemetryString;
    } else if (this.telemetryPill) {
      this.telemetryPill.textContent = telemetryString;
    }

    // Compact HUD Bar Telemetry Sync
    if (this.compactCpu && data.cpu_percent !== undefined) {
      this.compactCpu.textContent = `CPU ${data.cpu_percent}%`;
    }
    if (this.compactRam && data.ram_used_gb !== undefined) {
      this.compactRam.textContent = `RAM ${data.ram_used_gb}G`;
    }
    if (this.compactVram) {
      if (data.gpu && data.gpu.vram_used_mb !== undefined) {
        this.compactVram.textContent = `VRAM ${Math.round(data.gpu.vram_used_mb / 1024 * 10) / 10}G`;
      } else {
        this.compactVram.textContent = 'VRAM --';
      }
    }
    if (this.compactBrain && data.model && data.model.name) {
      this.compactBrain.textContent = data.model.name;
    }

    // Inside HUD View
    if (this.hudCpu) this.hudCpu.textContent = `${data.cpu_percent}%`;
    if (this.hudRam) this.hudRam.textContent = `${data.ram_used_gb} / ${data.ram_total_gb} GB (${data.ram_percent}%)`;
    if (this.hudVram) {
      if (data.gpu) {
        this.hudVram.textContent = `${data.gpu.vram_used_mb} MB / ${data.gpu.vram_total_mb} MB (${data.gpu.utilization_percent}% GPU)`;
      } else {
        this.hudVram.textContent = 'Integrated / Ready';
      }
    }

    // Model name, status & token speed
    if (this.hudModel && data.model) {
      const speedStr = data.model.tok_per_sec > 0 ? ` · ${data.model.tok_per_sec} tok/s` : '';
      const vramStr = data.model.vram_mb > 0 ? ` (${data.model.vram_mb} MB VRAM)` : '';
      this.hudModel.textContent = `${data.model.name}${vramStr} · ${data.model.status}${speedStr}`;
    }

    // Live Side Strip Telemetry
    if (this.stripTasks) {
      const count = data.open_tasks_count !== undefined ? data.open_tasks_count : (data.tasks || 0);
      this.stripTasks.textContent = `${count} Pending`;
    }
    if (this.stripNetwork) {
      const isOffline = !!(data.offline_mode || data.private_mode);
      this.stripNetwork.textContent = isOffline ? 'Offline / Local Qwen' : 'Online 5-Brain';
      if (isOffline) this.stripNetwork.classList.add('highlight');
      else this.stripNetwork.classList.remove('highlight');
    }

    // Active mode badge sync
    const modeBadge = document.getElementById('hud-active-mode-badge');
    if (modeBadge) {
      const appMode = (window.getAppMode ? window.getAppMode() : 'chat').toUpperCase();
      modeBadge.textContent = `${appMode} MODE`;
    }

    // Voice Engine badge sync
    const statVoiceEngine = document.getElementById('hud-stat-voice-engine');
    const isOffline = !!(data.offline_mode || data.private_mode);
    const engineLabel = data.voice_engine || (isOffline ? 'Browser Fallback' : 'Edge-TTS (Neural)');
    if (!this.isSpeaking) {
      if (statVoiceEngine) statVoiceEngine.textContent = engineLabel;
      if (this.compactEngine) this.compactEngine.textContent = engineLabel;
    }
  }

  async micArbiter(owner) {
    try {
      const fetchFn = window.apiFetch || window.fetch;
      const response = await fetchFn('/api/voice/mic-arb', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ owner: owner || 'background' })
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      return true;
    } catch (e) {
      console.warn('mic-arb notice:', e);
      return false;
    }
  }

  async toggleFocusMode(enable) {
    const shouldEnable = enable !== undefined ? !!enable : !this.isFocusActive;
    this.isFocusActive = shouldEnable;
    const chatMain = document.getElementById('chat-main');
    if (chatMain) {
      if (shouldEnable) {
        chatMain.classList.add('focus-orb-active');
      } else {
        chatMain.classList.remove('focus-orb-active');
      }
    }
    if (this.btnFocusOrb) {
      this.btnFocusOrb.classList.toggle('primary-glow', shouldEnable);
      this.btnFocusOrb.textContent = shouldEnable ? '← Back to Chat' : '🔮 Focus Orb';
    }
    await this.toggleHUD(shouldEnable);
    return shouldEnable;
  }

  async toggleHUD(enable) {
    this.isActive = !!enable;
    if (!this.view) {
      this.view = document.getElementById('hud-view');
    }
    if (!this.view) {
      console.error('[HUD] #hud-view element not found in DOM');
      return false;
    }

    if (this.isActive) {
      window.vegaActiveVoiceSurface = "orb";
      this.view.classList.add('active');
      this.isFocusActive = true;
      const chatMain = document.getElementById('chat-main');
      if (chatMain) chatMain.classList.add('focus-orb-active');
      if (this.btnFocusOrb) {
        this.btnFocusOrb.classList.add('primary-glow');
        this.btnFocusOrb.textContent = '← Back to Chat';
      }
      if (typeof window.stopChatMic === 'function') {
        window.stopChatMic();
      }
      if (window.VegaAudioManager && typeof window.VegaAudioManager.cancelAudio === 'function') {
        window.VegaAudioManager.cancelAudio();
      }
      try {
        const arbOk = await this.micArbiter('hud');
        if (!arbOk) {
          console.warn('[HUD] Background mic arbitration degraded; proceeding in HUD mode');
        }
      } catch (arbErr) {
        console.warn('[HUD] mic-arb notice:', arbErr);
      }

      try {
        await this.startAudioVisualizer();
      } catch (visErr) {
        console.warn('[HUD] Audio visualizer startup fallback:', visErr);
        this.startFallbackDrawLoop();
      }

      try {
        this.fetchPersona();
        this.fetchSleepState();
        this.resetSilenceTimer();
      } catch (stateErr) {
        console.warn('[HUD] State fetch non-fatal error:', stateErr);
      }
      window.dispatchEvent(new CustomEvent('vega:voice-surface', { detail: { surface: 'orb' } }));
      return true;
    } else {
      window.vegaActiveVoiceSurface = "chat";
      this.view.classList.remove('active');
      this.isFocusActive = false;
      const chatMain = document.getElementById('chat-main');
      if (chatMain) chatMain.classList.remove('focus-orb-active');
      if (this.btnFocusOrb) {
        this.btnFocusOrb.classList.remove('primary-glow');
        this.btnFocusOrb.textContent = '🔮 Focus Orb';
      }
      if (window.VegaAudioManager && typeof window.VegaAudioManager.cancelAudio === 'function') {
        window.VegaAudioManager.cancelAudio();
      }
      try {
        this.stopAudioVisualizer();
        this.pauseMediaRecording();
        this.stopVoiceCapture();
      } catch (stopErr) {
        console.warn('[HUD] Stop visualizer error:', stopErr);
      }
      const released = await this.micArbiter('background');
      if (this._silenceTimer) {
        clearTimeout(this._silenceTimer);
        this._silenceTimer = null;
      }
      window.dispatchEvent(new CustomEvent('vega:voice-surface', { detail: { surface: 'chat' } }));
      return released;
    }
  }

  loadInterview(data) {
    if (!data.company || data.company === "Target Tech" || data.company === "Target Cloud Tech") {
      if (window.__activeResumeDraft && window.__activeResumeDraft.company) {
        data.company = window.__activeResumeDraft.company;
      }
    }
    if (!data.role || data.role === "Target Role") {
      if (window.__activeResumeDraft && window.__activeResumeDraft.target_role) {
        data.role = window.__activeResumeDraft.target_role;
      }
    }

    this.interviewData = data;
    const overlay = document.getElementById('hud-interview-overlay');
    if (!overlay) return;
    overlay.classList.remove('hidden');

    const roleEl = document.getElementById('hud-interview-role');
    const compEl = document.getElementById('hud-interview-company');
    const stepEl = document.getElementById('hud-interview-step');
    const qtypeEl = document.getElementById('hud-interview-qtype');
    const qEl = document.getElementById('hud-interview-question');
    const feedbackEl = document.getElementById('hud-interview-feedback');
    const answerInput = document.getElementById('hud-interview-answer-input');
    const submitBtn = document.getElementById('hud-interview-submit-btn');
    const nextBtn = document.getElementById('hud-interview-next-btn');

    if (roleEl) roleEl.textContent = data.role || "DevOps & Cloud Engineer";
    if (compEl) compEl.textContent = data.company || "Target Cloud Tech";
    const qIdx = (data.current_question_idx || 0) + 1;
    const totalQ = data.total_questions || 5;
    if (stepEl) stepEl.textContent = `Question ${qIdx} / ${totalQ}`;

    const currQ = data.current_question;
    if (currQ) {
      if (qtypeEl) qtypeEl.textContent = currQ.type || "Technical Question";
      if (qEl) qEl.textContent = currQ.question || "";
    }
    if (feedbackEl) {
      feedbackEl.classList.add('hidden');
      feedbackEl.textContent = "";
    }
    if (answerInput) {
      answerInput.classList.remove('hidden');
      answerInput.value = "";
    }
    if (submitBtn) {
      submitBtn.classList.remove('hidden');
      submitBtn.disabled = false;
      submitBtn.textContent = "Submit Answer";
    }
    if (nextBtn) {
      nextBtn.classList.add('hidden');
      nextBtn.disabled = false;
    }

    const modeBadge = document.getElementById('hud-active-mode-badge');
    if (modeBadge) {
      modeBadge.textContent = `MOCK INTERVIEW (Q ${qIdx}/${totalQ})`;
    }

    if (!this.isFocusActive) {
      this.toggleFocusMode(true);
    }

    // Speak the interview question aloud via half-duplex TTS
    if (currQ && currQ.question) {
      setTimeout(() => {
        this.synthesizeSpeech(currQ.question);
      }, 400);
    }
  }

  async submitInterviewAnswer() {
    if (!this.interviewData) return;
    const answerInput = document.getElementById('hud-interview-answer-input');
    const submitBtn = document.getElementById('hud-interview-submit-btn');
    const nextBtn = document.getElementById('hud-interview-next-btn');
    const feedbackEl = document.getElementById('hud-interview-feedback');
    const answer = answerInput ? answerInput.value.trim() : "";

    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Evaluating Answer...";
    }

    try {
      const res = await fetch("/api/interview/answer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: this.interviewData.session_id,
          answer: answer
        })
      });
      const data = await res.json();
      if (data.status === "ok") {
        if (feedbackEl) {
          let fbHtml = `<strong>Feedback (Score: ${data.score}/10):</strong> ${data.feedback || ''}`;
          if (data.phrasing_tip) {
            fbHtml += `<br><span style="color:#38bdf8;font-size:0.85em;">💡 <strong>Interview tip:</strong> ${data.phrasing_tip}</span>`;
          }
          feedbackEl.innerHTML = fbHtml;
          feedbackEl.classList.remove('hidden');
        }

        // Speak the evaluation feedback aloud
        const speechFb = data.feedback ? `Score: ${data.score} out of 10. ${data.feedback}` : `Answer recorded with score ${data.score}.`;
        this.synthesizeSpeech(speechFb);

        if (data.is_complete) {
          const qEl = document.getElementById('hud-interview-question');
          if (qEl) {
            const sc = data.scorecard || {};
            qEl.innerHTML = `🎉 <strong>Interview Completed!</strong><br>Overall Score: ${sc.overall_score || 80}% (Grade: ${sc.letter_grade || 'B'})<br>Summary: ${sc.summary || ''}<br><br><em>Weak areas logged to today's Lab Log.</em>`;
          }
          if (submitBtn) submitBtn.classList.add('hidden');
          if (answerInput) answerInput.classList.add('hidden');
          if (nextBtn) nextBtn.classList.add('hidden');
          const modeBadge = document.getElementById('hud-active-mode-badge');
          if (modeBadge) modeBadge.textContent = "INTERVIEW COMPLETE";
        } else if (data.next_question) {
          this._pendingNextQuestion = data.next_question;
          this._pendingNextQuestionIdx = data.current_question_idx;
          if (nextBtn) {
            nextBtn.classList.remove('hidden');
          }
          // Auto-advance after 5 seconds if user does not manually click Next Question
          this._interviewAutoAdvanceTimer = setTimeout(() => {
            this.nextInterviewQuestion();
          }, 5000);
        }
      }
    } catch (err) {
      console.error("Submit interview answer error:", err);
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = "Submit Answer";
      }
    }
  }

  nextInterviewQuestion() {
    if (this._interviewAutoAdvanceTimer) {
      clearTimeout(this._interviewAutoAdvanceTimer);
      this._interviewAutoAdvanceTimer = null;
    }
    if (!this.interviewData || !this._pendingNextQuestion) return;
    this.interviewData.current_question = this._pendingNextQuestion;
    this.interviewData.current_question_idx = this._pendingNextQuestionIdx;
    this._pendingNextQuestion = null;
    this.loadInterview(this.interviewData);
  }

  async finishInterview() {
    if (this._interviewAutoAdvanceTimer) {
      clearTimeout(this._interviewAutoAdvanceTimer);
      this._interviewAutoAdvanceTimer = null;
    }
    if (!this.interviewData) return;
    try {
      const res = await fetch("/api/interview/finish", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: this.interviewData.session_id })
      });
      const data = await res.json();
      const sc = (data && data.scorecard) || {};
      const qEl = document.getElementById('hud-interview-question');
      if (qEl) {
        qEl.innerHTML = `🏁 <strong>Interview Finished!</strong><br>Overall Score: ${sc.overall_score || 75}% (Grade: ${sc.letter_grade || 'B'})<br>Summary: ${sc.summary || 'Session concluded.'}`;
      }
      const feedbackEl = document.getElementById('hud-interview-feedback');
      if (feedbackEl) feedbackEl.classList.add('hidden');
      const submitBtn = document.getElementById('hud-interview-submit-btn');
      if (submitBtn) submitBtn.classList.add('hidden');
      const nextBtn = document.getElementById('hud-interview-next-btn');
      if (nextBtn) nextBtn.classList.add('hidden');
      const answerInput = document.getElementById('hud-interview-answer-input');
      if (answerInput) answerInput.classList.add('hidden');
      const modeBadge = document.getElementById('hud-active-mode-badge');
      if (modeBadge) modeBadge.textContent = "INTERVIEW COMPLETE";
      this.synthesizeSpeech(`Interview concluded. Your score is ${sc.overall_score || 75} percent.`);
    } catch (e) {
      console.warn('Finish interview error:', e);
      this.exitInterview();
    }
  }

  exitInterview() {
    if (this._interviewAutoAdvanceTimer) {
      clearTimeout(this._interviewAutoAdvanceTimer);
      this._interviewAutoAdvanceTimer = null;
    }
    this.interviewData = null;
    this._pendingNextQuestion = null;
    const overlay = document.getElementById('hud-interview-overlay');
    if (overlay) overlay.classList.add('hidden');
    const modeBadge = document.getElementById('hud-active-mode-badge');
    if (modeBadge) modeBadge.textContent = "READY";
  }

  startFallbackDrawLoop() {
    if (this.animFrameId) {
      cancelAnimationFrame(this.animFrameId);
      this.animFrameId = null;
    }
    if (this.fallbackFrameId) {
      cancelAnimationFrame(this.fallbackFrameId);
      this.fallbackFrameId = null;
    }
    // The CSS keyframes provide the fallback visual without a per-frame paint loop.
  }

  async startAudioVisualizer() {
    try {
      this.audioCtx = await window.VegaAudioManager.ensureRunningContext();

      // If analyser and mic stream already exist and are active, reuse them
      if (this.analyser && this.sourceNode && this.microphoneStream && this.microphoneStream.active) {
        this.startDrawLoop();
        return;
      }

      let stream = null;
      try {
        stream = await window.VegaAudioManager.acquireStream('hud');
      } catch (micErr) {
        console.warn('HUD mic acquisition fallback to ambient animation:', micErr);
        // Start autonomous breathing loop immediately so the orb never freezes
        this.startFallbackDrawLoop();
        if (micErr.name === 'NotAllowedError' || micErr.name === 'PermissionDeniedError') {
          if (this.hudTranscript) this.hudTranscript.textContent = 'Microphone permission denied — ambient breathing mode active.';
        } else if (micErr.name === 'SecurityError') {
          if (this.hudTranscript) this.hudTranscript.textContent = 'Microphone blocked by security policy — ambient breathing mode active.';
        } else if (micErr.name === 'NotFoundError' || micErr.name === 'DevicesNotFoundError') {
          if (this.hudTranscript) this.hudTranscript.textContent = 'No microphone detected — ambient breathing mode active.';
        } else {
          if (this.hudTranscript) this.hudTranscript.textContent = 'Ambient visualizer active.';
        }
        return;
      }

      // Disconnect any stale nodes before wiring new stream
      if (this.sourceNode) {
        try { this.sourceNode.disconnect(); } catch (e) {}
        this.sourceNode = null;
      }
      if (this.analyser) {
        try { this.analyser.disconnect(); } catch (e) {}
        this.analyser = null;
      }

      this.microphoneStream = stream;
      const source = this.audioCtx.createMediaStreamSource(stream);
      this.sourceNode = source;
      this.analyser = this.audioCtx.createAnalyser();
      this.analyser.fftSize = 256;
      source.connect(this.analyser);

      this.startDrawLoop();
    } catch (err) {
      console.error('startAudioVisualizer error:', err);
      this.startFallbackDrawLoop();
      if (this.hudTranscript) {
        this.hudTranscript.textContent = 'Ambient visualizer active. Click core to retry microphone.';
      }
    }
  }

  startDrawLoop() {
    if (this.animFrameId) {
      cancelAnimationFrame(this.animFrameId);
      this.animFrameId = null;
    }
    if (this.fallbackFrameId) {
      cancelAnimationFrame(this.fallbackFrameId);
      this.fallbackFrameId = null;
    }

    if (!this.analyser) {
      this.startFallbackDrawLoop();
      return;
    }

    this.audioDataConfirmed = false;
    const bufferLength = this.analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);
    let visualFrame = 0;

    const draw = () => {
      if (!this.isActive || !this.analyser) return;
      try {
        this.analyser.getByteFrequencyData(dataArray);
      } catch (err) {
        console.error('[QA SYSTEM] HUD audio processing failed:', err);
        this.startFallbackDrawLoop();
        return;
      }

      const sum = dataArray.reduce((a, b) => a + b, 0);
      if (sum > 0 && !this.audioDataConfirmed) {
        console.log("HUD VERIFIED: Active audio data received in render loop.");
        this.audioDataConfirmed = true;
      }

      const avg = sum / (bufferLength || 1);
      if (++visualFrame % 3 === 0) {
        const volume = avg / 255.0;
        if (this.coreElement) this.coreElement.style.scale = `${1.0 + volume * 0.3}`;
        if (this.ringOuter) this.ringOuter.style.scale = `${1.0 + volume * 0.05}`;
        if (this.ringMid) this.ringMid.style.scale = `${1.0 + volume * 0.07}`;
        if (this.ringInner) this.ringInner.style.scale = `${1.0 + volume * 0.09}`;
      }

      // Voice Activity Detection & continuous speech capture with >=350ms energy gate and 900ms post-speech cooldown
      const now = Date.now();
      const inCooldown = (this._postSpeechCooldownUntil && now < this._postSpeechCooldownUntil);
      const isSpeakingState = (this.state === 'speaking' || this.isSpeaking);

      if (inCooldown || isSpeakingState || this.isSleeping) {
        this._speechEnergyFrames = 0;
        this._speechSilenceFrames = 0;
        if (this._isRecordingUtterance) {
          this.pauseMediaRecording();
        }
      } else {
        const speechThreshold = 18;
        if (avg > speechThreshold) {
          this._speechEnergyFrames = (this._speechEnergyFrames || 0) + 1;
          // >=350ms sustained voice energy gate (~20-22 frames at 60fps) before triggering capture
          if (this._speechEnergyFrames >= 20) {
            this._speechSilenceFrames = 0;
            this.resetSilenceTimer();
            if (!this._isRecordingUtterance) {
              this.startMediaRecording();
            }
          }
        } else {
          this._speechEnergyFrames = 0;
          if (this._isRecordingUtterance) {
            this._speechSilenceFrames = (this._speechSilenceFrames || 0) + 1;
            if (this._speechSilenceFrames > 45) { // ~0.75s of silence after speech
              this.stopMediaRecording();
            }
          }
        }
      }

      this.animFrameId = requestAnimationFrame(draw);
    };

    this.animFrameId = requestAnimationFrame(draw);
  }

  stopAudioVisualizer() {
    this.pauseMediaRecording();
    if (this.animFrameId) {
      cancelAnimationFrame(this.animFrameId);
      this.animFrameId = null;
    }
    if (this.fallbackFrameId) {
      cancelAnimationFrame(this.fallbackFrameId);
      this.fallbackFrameId = null;
    }

    // Disconnect audio graph nodes
    if (this.sourceNode) {
      try {
        this.sourceNode.disconnect();
      } catch (e) {}
      this.sourceNode = null;
    }
    if (this.analyser) {
      try {
        this.analyser.disconnect();
      } catch (e) {}
      this.analyser = null;
    }

    // Release HUD microphone consumer
    this.microphoneStream = null;
    if (window.VegaAudioManager) {
      window.VegaAudioManager.releaseStream('hud');
    }

    this.audioDataConfirmed = false;

    // Reset styles
    if (this.coreElement) {
      this.coreElement.style.transform = '';
      this.coreElement.style.scale = '';
      this.coreElement.style.boxShadow = '';
    }
    if (this.ringOuter) {
      this.ringOuter.style.scale = '';
      this.ringOuter.style.boxShadow = '';
    }
    if (this.ringMid) {
      this.ringMid.style.scale = '';
      this.ringMid.style.boxShadow = '';
    }
    if (this.ringInner) {
      this.ringInner.style.scale = '';
      this.ringInner.style.boxShadow = '';
    }
  }

  // Silent / Sleep Mode Lifecycle & Persistence
  async fetchSleepState() {
    try {
      const res = await fetch('/api/voice/sleep');
      const data = await res.json();
      if (data && data.ok) {
        this.isSleeping = !!data.sleeping;
        if (this.isSleeping) {
          this.setState('sleeping');
        }
      }
    } catch (e) {}
  }

  async toggleSleep(forceState) {
    const nextState = forceState !== undefined ? forceState : !this.isSleeping;
    if (nextState) this.abortAll();
    try {
      const res = await fetch('/api/voice/sleep', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sleeping: nextState })
      });
      const data = await res.json();
      if (data && data.ok) {
        this.isSleeping = !!data.sleeping;
        if (this.isSleeping) {
          this.setState('sleeping');
          if (this.hudTranscript) {
            this.hudTranscript.textContent = "Silent Mode active. Vega is resting — say 'Hey Vega' or click to wake.";
          }
        } else {
          this.setState('idle');
          if (this.hudTranscript) {
            this.hudTranscript.textContent = "Vega is awake and listening.";
          }
          this.resetSilenceTimer();
        }
      }
    } catch (e) {
      console.warn('Failed to toggle sleep state:', e);
    }
  }

  resetSilenceTimer() {
    if (this._silenceTimer) {
      clearTimeout(this._silenceTimer);
    }
    if (this.isSleeping || !this.isActive) return;

    this._silenceTimer = setTimeout(() => {
      console.log(`HUD: ${this.silenceTimeoutSec}s silence timeout elapsed without user speech. Entering sleep mode.`);
      this.toggleSleep(true);
    }, this.silenceTimeoutSec * 1000);
  }

  // MediaRecorder Voice Activity Detection & Slicing
  startMediaRecording() {
    if (this._isRecordingUtterance || !this.microphoneStream || !this.microphoneStream.active) return;
    if (typeof MediaRecorder === 'undefined') return;

    try {
      this._recordedChunks = [];
      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : (MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '');
      const opts = mime ? { mimeType: mime } : {};
      this._mediaRecorder = new MediaRecorder(this.microphoneStream, opts);

      this._mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          this._recordedChunks.push(e.data);
        }
      };

      this._mediaRecorder.onstop = async () => {
        const audioBlob = new Blob(this._recordedChunks, { type: this._mediaRecorder.mimeType || 'audio/webm' });
        this._recordedChunks = [];
        this._isRecordingUtterance = false;
        if (audioBlob.size > 1500) {
          await this.processRecordedAudio(audioBlob);
        }
      };

      this._mediaRecorder.start(250);
      this._isRecordingUtterance = true;
      this._speechSilenceFrames = 0;
      if (this.state !== 'speaking' && !this.isSleeping) {
        this.setState('listening');
      }
    } catch (err) {
      console.warn('MediaRecorder start failed:', err);
      this._isRecordingUtterance = false;
    }
  }

  stopMediaRecording() {
    if (this._mediaRecorder && this._isRecordingUtterance && this._mediaRecorder.state === 'recording') {
      try {
        this._mediaRecorder.stop();
      } catch (e) {}
    }
  }

  pauseMediaRecording() {
    this.stopMediaRecording();
    this._isRecordingUtterance = false;
    this._recordedChunks = [];
  }

  async processRecordedAudio(blob) {
    if (this.state === 'speaking') return; // Echo prevention

    this.setState('thinking');
    if (this.hudTranscript) {
      this.hudTranscript.textContent = "Transcribing speech via Groq Whisper...";
    }

    try {
      const formData = new FormData();
      formData.append('audio', blob, 'hud_speech.webm');
      const res = await fetch('/api/voice/transcribe', {
        method: 'POST',
        body: formData
      });
      const data = await res.json();
      if (data && data.ok && data.text) {
        const text = data.text.trim();
        await this.handleTranscribedCommand(text);
      } else {
        if (!this.isSleeping) {
          this.setState('idle');
        }
      }
    } catch (err) {
      console.warn('Audio transcribe failed:', err);
      if (!this.isSleeping) {
        this.setState('idle');
      }
    }
  }

  async handleTranscribedCommand(text) {
    if (!text) {
      this.setState('idle');
      return;
    }

    // Defensive client-side self-echo filtering
    const tNorm = text.toLowerCase().replace(/[^\w\s]/g, ' ').replace(/\s+/g, ' ').trim();
    if (this._recentSpokenReplies && tNorm) {
      for (const r of this._recentSpokenReplies) {
        if (Date.now() - r.time > 12000) continue;
        const rNorm = r.text.replace(/[^\w\s]/g, ' ').replace(/\s+/g, ' ').trim();
        if (rNorm && (rNorm.includes(tNorm) || tNorm.includes(rNorm))) {
          console.log('Self-echo filtered on client:', text);
          this.setState('idle');
          return;
        }
      }
    }

    if (this.hudTranscript) {
      this.hudTranscript.textContent = `Heard: "${text}"`;
    }

    const tLower = text.toLowerCase();

    // Check for sleep / silence commands
    const sleepPhrases = [
      'go to sleep', 'go silent', 'so silent', 'be silent', 'stay silent', 'no silent',
      'go quiet', 'be quiet', 'sleep mode', 'silent mode', 'stop listening', 'stop talking',
      'sleep now', 'good night vega', 'vega go silent', 'vega sleep', 'mute yourself', 'stay quiet', 'hey vega go silent'
    ];
    if (sleepPhrases.some(p => tLower.includes(p))) {
      await this.synthesizeSpeech("Going silent. Say 'Hey Vega' whenever you need me.");
      await this.toggleSleep(true);
      return;
    }

    // Check for wake phrases if sleeping
    if (this.isSleeping) {
      if (/\bhey\s+vega\b/i.test(text)) {
        await this.toggleSleep(false);
        await this.synthesizeSpeech("I'm awake and listening.");
        return;
      } else {
        this.setState('sleeping');
        return;
      }
    }

    // Mock Interview Active: route user answer directly to interview evaluator
    if (this.interviewData) {
      const answerInput = document.getElementById('hud-interview-answer-input');
      if (answerInput) {
        answerInput.value = text;
      }
      if (this.hudTranscript) {
        this.hudTranscript.textContent = `Interview Answer: "${text}"`;
      }
      await this.submitInterviewAnswer();
      return;
    }

    // Normal command execution
    this.resetSilenceTimer();
    if (window.appendMessage) {
      window.appendMessage('you', text);
    }
    if (this.textInput) {
      this.textInput.value = text;
      await this.sendTextMessage(true);
    }
  }

  // Local Microphone Fallback (via Python sounddevice)
  async triggerLocalMicFallback() {
    this.setState('listening');
    if (this.hudTranscript) {
      this.hudTranscript.textContent = "Listening via local system microphone...";
    }
    try {
      const res = await fetch('/api/voice/listen-local', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
      });
      const data = await res.json();
      if (data && data.ok && data.text) {
        await this.handleTranscribedCommand(data.text);
      } else {
        const err = (data && data.error) ? data.error : "Could not hear audio.";
        if (this.hudTranscript) this.hudTranscript.textContent = err;
        this.setState('idle');
      }
    } catch (e) {
      if (this.hudTranscript) this.hudTranscript.textContent = "Local mic error: " + e.message;
      this.setState('idle');
    }
  }

  // Phase 10: Stop / Interrupt
  abortAll() {
    if (window.speechSynthesis) {
      window.speechSynthesis.cancel();
    }
    if (window.__vegaAudioPlayer) {
      try {
        window.__vegaAudioPlayer.pause();
      } catch (e) {}
      window.__vegaAudioPlayer = null;
    }
    this.stopVoiceCapture();
    fetch('/api/stop', { method: 'POST' }).catch(() => {});
    this.setState('idle');
    if (this.hudTranscript) {
      this.hudTranscript.textContent = 'Voice playback and reasoning stopped.';
    }
    if (this.isActive) this.micArbiter('hud'); else this.micArbiter('background');
  }

  async startVoiceCapture() {
    if (window.appIsLocked) {
      if (window.showLockScreen) window.showLockScreen();
      return;
    }
    try {
      this.audioCtx = await window.VegaAudioManager.ensureRunningContext();
      if (this.isActive && (!this.analyser || !this.microphoneStream || !this.microphoneStream.active)) {
        await this.startAudioVisualizer();
      }
    } catch (e) {
      console.warn('AudioContext resume on core click:', e);
    }

    if (this.isSleeping) {
      this.toggleSleep(false);
    }

    this.setState('listening');
    if (this.hudTranscript) {
      this.hudTranscript.textContent = "Listening… speak now.";
    }

    try {
      const res = await fetch("/api/mic/start", { method: "POST" });
      const data = await res.json();
      if (this.state !== 'listening') return;
      if (data && data.ok && data.text) {
        const text = data.text.trim();
        this.setState('thinking');
        if (this.hudTranscript) {
          this.hudTranscript.textContent = `Heard: "${text}"`;
        }
        await this.handleTranscribedCommand(text);
      } else if (data && data.error) {
        if (this.hudTranscript) {
          this.hudTranscript.textContent = "Mic issue: " + data.error;
        }
        this.setState('idle');
      } else {
        if (this.hudTranscript) {
          this.hudTranscript.textContent = "I didn't catch that. Click the orb and speak again.";
        }
        this.setState('idle');
      }
    } catch (err) {
      console.warn("Voice capture API error, falling back to local mic:", err);
      await this.triggerLocalMicFallback();
    }
  }

  stopVoiceCapture() {
    fetch("/api/mic/stop", { method: "POST" }).catch(() => {});
    if (this.state === 'listening') {
      this.setState('idle');
      if (this.hudTranscript) {
        this.hudTranscript.textContent = "Voice listening paused.";
      }
    }
  }

  // Phase 10 & 12: Speech Synthesis via Edge-TTS Neural Voice with Browser Fallback
  async synthesizeSpeech(text, turnId = null) {
    if (!text || typeof text !== 'string') return;

    // Defensively strip coaching tip from spoken TTS audio
    const cleanSpeech = text.replace(/(?:^|\n)\*?\*?Interview (?:phrasing )?tip:\*?\*?[\s\S]*$/i, '').trim();
    if (!cleanSpeech) return;

    // Register clean spoken text for self-echo filtering
    if (!this._recentSpokenReplies) this._recentSpokenReplies = [];
    this._recentSpokenReplies.push({ time: Date.now(), text: cleanSpeech.toLowerCase() });
    this._recentSpokenReplies = this._recentSpokenReplies.filter(r => Date.now() - r.time < 30000).slice(-10);

    // Deduplication check via VegaAudioManager
    if (window.VegaAudioManager) {
      if (!window.VegaAudioManager.shouldPlaySpeech(cleanSpeech, turnId)) {
        console.log('[TTS] Deduplicated playback suppressed for:', cleanSpeech.slice(0, 40));
        return;
      }
      window.VegaAudioManager.cancelAudio();
    }

    // Strict half-duplex mute: halt recording and pause background wake listener
    this.isSpeaking = true;
    this.pauseMediaRecording();
    this.setState('speaking');
    await this.micArbiter('tts');

    try {
      const res = await fetch('/api/voice/speak', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: cleanSpeech, turn_id: turnId || undefined })
      });
      const data = await res.json();

      if (data && data.ok && data.audio_base64) {
        if (window.__vegaAudioPlayer) {
          try { window.__vegaAudioPlayer.pause(); } catch (e) {}
        }

        // Convert base64 to Blob URL for instant, clean, leak-free playback
        const binaryStr = atob(data.audio_base64);
        const bytes = new Uint8Array(binaryStr.length);
        for (let i = 0; i < binaryStr.length; i++) {
          bytes[i] = binaryStr.charCodeAt(i);
        }
        const blob = new Blob([bytes], { type: 'audio/mpeg' });
        const blobUrl = URL.createObjectURL(blob);
        const audio = new Audio(blobUrl);
        window.__vegaAudioPlayer = audio;
        if (window.VegaAudioManager) {
          window.VegaAudioManager.currentAudio = audio;
        }

        const statEngine = document.getElementById('hud-stat-voice-engine');
        if (statEngine) statEngine.textContent = `Edge-TTS (${data.voice || 'Neural'})`;
        if (this.compactEngine) this.compactEngine.textContent = `Edge-TTS (${data.voice || 'Neural'})`;

        const finalizeSpeech = (isError = false) => {
          URL.revokeObjectURL(blobUrl);
          window.__vegaAudioPlayer = null;
          if (window.VegaAudioManager && window.VegaAudioManager.currentAudio === audio) {
            window.VegaAudioManager.currentAudio = null;
          }
          // Enforce strict 1000ms post-speech cooldown against room reverb & echo bleed
          this._postSpeechCooldownUntil = Date.now() + 1000;
          this.isSpeaking = false;
          this.setState('idle');
          if (this.isActive) this.micArbiter('hud'); else this.micArbiter('background');
          if (isError) {
            if (statEngine) statEngine.textContent = 'Browser Fallback';
            if (this.compactEngine) this.compactEngine.textContent = 'Browser Fallback';
            this.fallbackBrowserSpeech(cleanSpeech, 'Audio playback error', turnId);
          }
        };

        audio.onended = () => finalizeSpeech(false);
        audio.onerror = () => finalizeSpeech(true);
        await audio.play();
        return;
      } else {
        const reason = this.ttsFallbackNotice(data && (data.notice || data.error));
        console.warn('[VOICE ENGINE] Edge-TTS synthesis failed:', reason);
        if (this.hudTranscript) {
          this.hudTranscript.textContent = reason;
        }
        this.fallbackBrowserSpeech(cleanSpeech, reason, turnId);
        return;
      }
    } catch (err) {
      console.warn('[VOICE ENGINE] Edge-TTS HTTP/fetch failed, falling back:', err);
      const notice = this.ttsFallbackNotice(err && err.message);
      if (this.hudTranscript) this.hudTranscript.textContent = notice;
      this.fallbackBrowserSpeech(cleanSpeech, notice, turnId);
      return;
    }

    this.fallbackBrowserSpeech(cleanSpeech, "", turnId);
  }

  ttsFallbackNotice(reason) {
    const status = String(reason || '').match(/\bHTTP(?:\s+Error)?\s*[:=]?\s*(\d{3})\b|\b(\d{3})\s+(?:Client|Server)\s+Error\b/i);
    const code = status && (status[1] || status[2]);
    if (code === '403') {
      return 'Edge-TTS unavailable (HTTP 403 — check system clock/network). Using browser voice.';
    }
    return code ? `Edge-TTS unavailable (HTTP ${code}). Using browser voice.`
      : 'Edge-TTS unavailable. Using browser voice.';
  }

  fallbackBrowserSpeech(text, reason = "", turnId = null) {
    if (window.VegaAudioManager) {
      window.VegaAudioManager.cancelAudio();
    }
    const statEngine = document.getElementById('hud-stat-voice-engine');
    if (statEngine) statEngine.textContent = 'Browser Fallback';
    if (this.compactEngine) this.compactEngine.textContent = 'Browser Fallback';
    if (reason) console.warn('[VOICE ENGINE] Using browser SpeechSynthesis fallback due to:', reason);

    this.isSpeaking = true;
    this.pauseMediaRecording();
    this.setState('speaking');

    if (!window.speechSynthesis) {
      this.isSpeaking = false;
      this.setState('idle');
      if (this.isActive) this.micArbiter('hud');
      return;
    }
    window.speechSynthesis.cancel();
    const utt = new SpeechSynthesisUtterance(text);
    utt.rate = 1.05;
    const finalizeFallback = () => {
      this._postSpeechCooldownUntil = Date.now() + 1000;
      this.isSpeaking = false;
      this.setState('idle');
      if (this.isActive) this.micArbiter('hud'); else this.micArbiter('background');
    };
    utt.onend = finalizeFallback;
    utt.onerror = finalizeFallback;
    window.speechSynthesis.speak(utt);
  }

  // Phase 10: Quick Action Pills Handlers
  async handleExplainClipboard() {
    this.setState('thinking');
    if (this.hudTranscript) this.hudTranscript.textContent = 'Inspecting clipboard error/code...';
    try {
      const res = await fetch('/api/workspace/explain-clipboard', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
      });
      const data = await res.json();
      if (data.ok) {
        const text = data.explanation || data.error || 'No explanation returned.';
        if (this.hudTranscript) this.hudTranscript.textContent = text;
        if (window.appendMessage) window.appendMessage('assistant', text, 'Clipboard Diagnosis');
        await this.synthesizeSpeech(text);
      } else {
        const errText = data.error || 'Failed to explain clipboard.';
        if (this.hudTranscript) this.hudTranscript.textContent = errText;
        this.setState('idle');
      }
    } catch (e) {
      if (this.hudTranscript) this.hudTranscript.textContent = 'Error calling clipboard tool: ' + e.message;
      this.setState('idle');
    }
  }

  async handleLookAtScreen() {
    this.setState('thinking');
    if (this.hudTranscript) this.hudTranscript.textContent = 'Capturing screen and analyzing with Gemini Vision...';
    try {
      const res = await fetch('/api/workspace/look-at-screen', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: 'What is visible on the screen, and what errors or active tasks do you see?' })
      });
      const data = await res.json();
      if (data.ok) {
        const text = data.analysis || data.error || 'Screen analyzed.';
        if (this.hudTranscript) this.hudTranscript.textContent = text;
        if (window.appendMessage) window.appendMessage('assistant', text, 'Screen Vision');
        await this.synthesizeSpeech(text);
      } else {
        const errText = data.error || 'Screen capture unavailable.';
        if (this.hudTranscript) this.hudTranscript.textContent = errText;
        this.setState('idle');
      }
    } catch (e) {
      if (this.hudTranscript) this.hudTranscript.textContent = 'Screen tool error: ' + e.message;
      this.setState('idle');
    }
  }

  async startMockInterview() {
    if (window.appIsLocked) {
      if (window.showLockScreen) window.showLockScreen();
      return;
    }
    try {
      let targetRole = 'DevOps & Cloud Engineer';
      let targetCompany = 'Target Cloud Tech';
      if (window.__activeResumeDraft) {
        if (window.__activeResumeDraft.target_role) targetRole = window.__activeResumeDraft.target_role;
        if (window.__activeResumeDraft.company) targetCompany = window.__activeResumeDraft.company;
      } else {
        const draftRoleEl = document.getElementById('resume-draft-role');
        const draftCompEl = document.getElementById('resume-draft-company');
        if (draftRoleEl && draftRoleEl.textContent && !draftRoleEl.textContent.includes('Loading')) {
          targetRole = draftRoleEl.textContent.trim();
        }
        if (draftCompEl && draftCompEl.textContent && !draftCompEl.textContent.includes('Loading')) {
          targetCompany = draftCompEl.textContent.trim();
        }
      }

      const res = await fetch('/api/interview/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: targetRole, company: targetCompany })
      });
      const data = await res.json();
      if (data && data.status === 'ok') {
        if (!data.company) data.company = targetCompany;
        if (!data.role) data.role = targetRole;
        this.loadInterview(data);
      }
    } catch (e) {
      console.warn('Mock interview trigger fallback:', e);
      let targetRole = 'DevOps & Cloud Engineer';
      let targetCompany = 'Target Cloud Tech';
      if (window.__activeResumeDraft) {
        if (window.__activeResumeDraft.target_role) targetRole = window.__activeResumeDraft.target_role;
        if (window.__activeResumeDraft.company) targetCompany = window.__activeResumeDraft.company;
      }
      this.loadInterview({
        role: targetRole,
        company: targetCompany,
        total_questions: 5,
        current_question_idx: 0,
        current_question: {
          type: 'Linux & Troubleshooting',
          question: 'A Linux server has high load average but low CPU utilization. How would you diagnose the bottleneck?'
        }
      });
    }
  }

  async togglePersona() {
    const nextMode = this.personaMode === 'friend' ? 'pro' : 'friend';
    await this.savePersonaSettings(nextMode, this.englishCoachEnabled);
  }

  async toggleCoach() {
    const nextCoach = !this.englishCoachEnabled;
    this.englishCoachEnabled = nextCoach;
    this.updatePersonaUI();
    try {
      await fetch('/api/settings/english-coach', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: nextCoach, english_coach_enabled: nextCoach })
      });
    } catch (e) {
      console.warn('English coach persistence notice:', e);
    }
    await this.savePersonaSettings(this.personaMode, nextCoach);
  }

  async savePersonaSettings(mode, coach) {
    try {
      const res = await fetch('/api/settings/persona', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ persona_mode: mode, english_coach_enabled: coach })
      });
      const data = await res.json();
      if (data.ok) {
        this.personaMode = data.persona_mode;
        this.englishCoachEnabled = !!data.english_coach_enabled;
        this.updatePersonaUI();
        if (this.hudTranscript) {
          const modeMsg = this.personaMode === 'friend'
            ? '₹0 Friend Persona Active (Warm, playful Telugu/Tenglish encouragement).'
            : 'Professional AI Persona Active (Direct, concise engineering tone).';
          const coachMsg = this.englishCoachEnabled ? ' English Coach ON.' : '';
          this.hudTranscript.textContent = modeMsg + coachMsg;
        }
      }
    } catch (e) {
      console.warn('Persona save failed:', e);
    }
  }

  updatePersonaUI() {
    if (this.stripPersona) {
      this.stripPersona.textContent = this.personaMode === 'friend'
        ? '₹0 Friend (Telugu/Tenglish)'
        : 'Professional AI';
    }
    if (this.stripCoach) {
      this.stripCoach.textContent = this.englishCoachEnabled ? 'ON' : 'OFF';
      if (this.englishCoachEnabled) this.stripCoach.classList.add('highlight');
      else this.stripCoach.classList.remove('highlight');
    }
    document.querySelectorAll('#hud-pill-persona, .hud-pill-persona-btn').forEach(pill => {
      pill.textContent = this.personaMode === 'friend' ? '👥 Persona: Friend' : '👥 Persona: Pro';
      if (this.personaMode === 'friend') pill.classList.add('active');
      else pill.classList.remove('active');
    });
    document.querySelectorAll('#hud-pill-coach, .hud-pill-coach-btn').forEach(pill => {
      pill.textContent = this.englishCoachEnabled ? '💡 English Coach: ON' : '💡 English Coach: OFF';
      if (this.englishCoachEnabled) pill.classList.add('active');
      else pill.classList.remove('active');
    });
    const select = document.getElementById('persona-mode-select');
    if (select) select.value = this.personaMode;
    const coachCheck = document.getElementById('coach-toggle-check');
    if (coachCheck) coachCheck.checked = this.englishCoachEnabled;
  }

  async fetchPersona() {
    try {
      const res = await fetch('/api/settings/persona');
      const data = await res.json();
      if (data.ok) {
        this.personaMode = data.persona_mode || 'friend';
        this.englishCoachEnabled = !!data.english_coach_enabled;
        this.updatePersonaUI();
      }
    } catch (e) {}
    this.fetchOrchestratorStatus();
  }

  async fetchOrchestratorStatus() {
    if (window.isUnlocked && !window.isUnlocked()) return;
    try {
      const res = await fetch('/api/orchestrator/status');
      const data = await res.json();
      if (this.statOrchestrator) {
        if (data.online) {
          this.statOrchestrator.textContent = 'Online (Port 8765)';
          this.statOrchestrator.classList.add('highlight');
          if (this.compactBrain) this.compactBrain.textContent = 'Orchestrator (Port 8765)';
        } else {
          this.statOrchestrator.textContent = 'Offline (Direct Fallback)';
          this.statOrchestrator.classList.remove('highlight');
          if (this.compactBrain && this.compactBrain.textContent.includes('Orchestrator')) {
            this.compactBrain.textContent = 'Qwen / Gemini';
          }
        }
      }
    } catch (e) {}
  }

  // Phase 10: HUD Text Input Bar
  async sendTextMessage(spoken = false) {
    if (!this.textInput) return;
    const text = this.textInput.value.trim();
    if (!text) return;
    this.textInput.value = '';

    if (this.hudTranscript) {
      this.hudTranscript.textContent = `You: "${text}"`;
    }
    this.setState('thinking');

    try {
      const activeChatId = (typeof window.getActiveChatId === 'function') ? window.getActiveChatId() : (window.activeChatId || "");
      const res = await (window.apiFetch || fetch)('/api/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: text,
          spoken: spoken,
          surface: "orb",
          chat_id: activeChatId,
          mode: "chat"
        })
      });

      if (!res.ok) throw new Error('HTTP ' + res.status);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let fullText = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split('\n');
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const payload = JSON.parse(line.substring(6));
              if (payload.token) {
                fullText = payload.full || (fullText + payload.token);
                if (this.hudTranscript) this.hudTranscript.textContent = fullText;
              } else if (payload.done) {
                fullText = payload.text || fullText;
              }
            } catch (e) {
              fullText += line.substring(6);
              if (this.hudTranscript) this.hudTranscript.textContent = fullText;
            }
          }
        }
      }

      if (this.hudTranscript) this.hudTranscript.textContent = fullText || 'Done.';
      if (window.appendMessage) window.appendMessage('assistant', fullText, 'Vega');
      if (window.refreshChatList) window.refreshChatList();
      const turnId = 'turn_' + Date.now();
      await this.synthesizeSpeech(fullText, turnId);
    } catch (err) {
      if (this.hudTranscript) this.hudTranscript.textContent = 'Error: ' + err.message;
      this.setState('idle');
    }
  }
}

window.openOrb = () => { if (window.arcReactorHUD) window.arcReactorHUD.toggleFocusMode(true); };
window.closeOrb = () => { if (window.arcReactorHUD) window.arcReactorHUD.toggleFocusMode(false); };

// Auto instantiate safely whether DOM is ready or loading
const initArcReactor = () => {
  if (!window.arcReactorHUD) {
    window.arcReactorHUD = new ArcReactorHUD();
  }
};

if (document.readyState === 'loading') {
  window.addEventListener('DOMContentLoaded', initArcReactor);
} else {
  initArcReactor();
}
