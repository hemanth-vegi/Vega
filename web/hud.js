/**
 * web/hud.js - Arc Reactor Visualizer & Web Audio Pulse Analyzer
 */

class ArcReactorHUD {
  constructor() {
    this.isActive = false;
    this.audioCtx = null;
    this.analyser = null;
    this.microphoneStream = null;
    this.animFrameId = null;
    this.coreElement = null;

    this.initElements();
    this.bindEvents();
  }

  initElements() {
    this.view = document.getElementById('hud-view');
    this.coreElement = document.getElementById('reactor-core');
    this.hudCpu = document.getElementById('hud-stat-cpu');
    this.hudRam = document.getElementById('hud-stat-ram');
    this.hudVram = document.getElementById('hud-stat-vram');
    this.hudModel = document.getElementById('hud-stat-model');
    this.hudTranscript = document.getElementById('hud-transcript');
    this.telemetryPill = document.getElementById('telemetry-pill');
  }

  bindEvents() {
    const tabBtn = document.getElementById('mode-hud-tab');
    if (tabBtn) {
      tabBtn.addEventListener('click', () => this.toggleHUD(true));
    }

    if (this.coreElement) {
      this.coreElement.addEventListener('click', () => {
        // Core click triggers voice listening
        const micBtn = document.getElementById('listening-toggle-btn');
        if (micBtn) micBtn.click();
      });
    }

    // Connect to SSE telemetry stream
    if (window.EventSource) {
      this.initSSEListener();
    }
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
          }
        } catch (err) {}
      };
    } catch (e) {
      console.warn('HUD SSE init:', e);
    }
  }

  updateTelemetry(data) {
    if (!data) return;

    // Header Pill
    if (this.telemetryPill) {
      let vramStr = data.gpu ? ` · VRAM ${Math.round(data.gpu.vram_used_mb / 1024 * 10) / 10}G` : '';
      this.telemetryPill.textContent = `CPU ${data.cpu_percent}% · RAM ${data.ram_used_gb}G${vramStr}`;
    }

    // Inside HUD View
    if (this.hudCpu) this.hudCpu.textContent = `${data.cpu_percent}%`;
    if (this.hudRam) this.hudRam.textContent = `${data.ram_used_gb} / ${data.ram_total_gb} GB (${data.ram_percent}%)`;
    if (this.hudVram && data.gpu) {
      this.hudVram.textContent = `${data.gpu.vram_used_mb} MB / ${data.gpu.vram_total_mb} MB (${data.gpu.utilization_percent}% GPU)`;
    }
  }

  async toggleHUD(enable) {
    this.isActive = enable;
    if (!this.view) return;

    if (this.isActive) {
      this.view.classList.add('active');
      this.startAudioVisualizer();
    } else {
      this.view.classList.remove('active');
      this.stopAudioVisualizer();
    }
  }

  async startAudioVisualizer() {
    if (this.audioCtx) return;
    try {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      this.audioCtx = new AudioContext();
      this.analyser = this.audioCtx.createAnalyser();
      this.analyser.fftSize = 64;

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      this.microphoneStream = stream;
      const source = this.audioCtx.createMediaStreamSource(stream);
      source.connect(this.analyser);

      const bufferLength = this.analyser.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);

      const draw = () => {
        if (!this.isActive) return;
        this.analyser.getByteFrequencyData(dataArray);

        // Compute average volume level
        let sum = 0;
        for (let i = 0; i < bufferLength; i++) {
          sum += dataArray[i];
        }
        const avg = sum / bufferLength;
        const scale = 1.0 + (avg / 255.0) * 0.45;
        const glow = 35 + (avg / 255.0) * 80;

        if (this.coreElement) {
          this.coreElement.style.transform = `scale(${scale})`;
          this.coreElement.style.boxShadow = `0 0 ${glow}px #f43f5e, 0 0 ${glow * 2}px rgba(234, 88, 12, 0.45)`;
        }

        this.animFrameId = requestAnimationFrame(draw);
      };

      draw();
    } catch (err) {
      console.warn('Web Audio microphone access for HUD visualizer:', err);
    }
  }

  stopAudioVisualizer() {
    if (this.animFrameId) {
      cancelAnimationFrame(this.animFrameId);
      this.animFrameId = null;
    }
    if (this.microphoneStream) {
      this.microphoneStream.getTracks().forEach(track => track.stop());
      this.microphoneStream = null;
    }
    if (this.audioCtx) {
      this.audioCtx.close();
      this.audioCtx = null;
    }
    if (this.coreElement) {
      this.coreElement.style.transform = 'scale(1)';
      this.coreElement.style.boxShadow = '0 0 45px #f43f5e';
    }
  }
}

// Auto instantiate on DOM load
window.addEventListener('DOMContentLoaded', () => {
  window.arcReactorHUD = new ArcReactorHUD();
});
