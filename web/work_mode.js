/**
 * web/work_mode.js - Vega 2.1 Work Mode Manager
 * Gemini Canvas / VS Code style Workbench:
 * - Side-by-side contextual chat and interactive Workbench
 * - File tree explorer and file viewer/editor
 * - Aider-style SEARCH/REPLACE diff viewer with Apply/Reject actions
 * - Terminal execution directly in workspace root
 */

(function () {
  'use strict';

  class WorkModeManager {
    constructor() {
      this.activeFilePath = null;
      this.activeFileContent = "";
      this.contextFilePath = null;
      this.pendingDiff = null;
      this.treeData = null;
      this.isGenerating = false;

      this.initElements();
      this.bindEvents();
    }

    initElements() {
      this.workspace = document.getElementById('work-workspace');
      this.messagesList = document.getElementById('work-messages-list');
      this.promptInput = document.getElementById('work-prompt-input');
      this.sendBtn = document.getElementById('work-send-btn');
      this.clearChatBtn = document.getElementById('work-clear-chat-btn');
      this.activeFileChip = document.getElementById('work-active-file-chip');

      // Workbench Tabs & Viewports
      this.tabFiles = document.getElementById('wb-tab-files');
      this.tabEditor = document.getElementById('wb-tab-editor');
      this.tabDiff = document.getElementById('wb-tab-diff');
      this.tabTerminal = document.getElementById('wb-tab-terminal');

      this.viewFiles = document.getElementById('work-file-tree-view');
      this.viewEditor = document.getElementById('work-editor-view');
      this.viewDiff = document.getElementById('work-diff-view');
      this.viewTerminal = document.getElementById('work-terminal-view');

      // Explorer
      this.fileTreeContainer = document.getElementById('work-file-tree');
      this.refreshTreeBtn = document.getElementById('wb-refresh-tree-btn');

      // Editor
      this.editorFilename = document.getElementById('wb-editor-filename');
      this.editorTabLabel = document.getElementById('wb-editor-tab-label');
      this.codeEditor = document.getElementById('work-code-editor');
      this.saveFileBtn = document.getElementById('wb-save-file-btn');
      this.attachContextBtn = document.getElementById('wb-attach-context-btn');

      // Diff Viewer
      this.diffTargetFile = document.getElementById('wb-diff-target-file');
      this.diffContainer = document.getElementById('work-diff-container');
      this.applyDiffBtn = document.getElementById('wb-apply-diff-btn');
      this.rejectDiffBtn = document.getElementById('wb-reject-diff-btn');

      // Terminal
      this.termOutput = document.getElementById('work-terminal-output');
      this.termInput = document.getElementById('work-terminal-cmd');
      this.termRunBtn = document.getElementById('work-terminal-run-btn');
    }

    bindEvents() {
      // Tab navigation
      if (this.tabFiles) this.tabFiles.addEventListener('click', () => this.switchTab('files'));
      if (this.tabEditor) this.tabEditor.addEventListener('click', () => this.switchTab('editor'));
      if (this.tabDiff) this.tabDiff.addEventListener('click', () => this.switchTab('diff'));
      if (this.tabTerminal) this.tabTerminal.addEventListener('click', () => this.switchTab('terminal'));

      // File actions
      if (this.refreshTreeBtn) this.refreshTreeBtn.addEventListener('click', () => this.loadWorkspaceTree());
      if (this.saveFileBtn) this.saveFileBtn.addEventListener('click', () => this.saveCurrentFile());
      if (this.attachContextBtn) this.attachContextBtn.addEventListener('click', () => this.attachActiveFileToContext());

      // Context chip click to remove
      if (this.activeFileChip) {
        this.activeFileChip.addEventListener('click', () => {
          this.contextFilePath = null;
          this.activeFileChip.classList.add('hidden');
          this.activeFileChip.textContent = 'None';
        });
      }

      // Diff actions
      if (this.applyDiffBtn) this.applyDiffBtn.addEventListener('click', () => this.applyPendingDiff());
      if (this.rejectDiffBtn) this.rejectDiffBtn.addEventListener('click', () => this.rejectPendingDiff());

      // Terminal actions
      if (this.termRunBtn) this.termRunBtn.addEventListener('click', () => this.executeTerminal());
      if (this.termInput) {
        this.termInput.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            this.executeTerminal();
          }
        });
      }

      // Chat actions
      if (this.sendBtn) this.sendBtn.addEventListener('click', () => this.sendMessage());
      if (this.promptInput) {
        this.promptInput.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            this.sendMessage();
          }
        });
      }
      if (this.clearChatBtn) {
        this.clearChatBtn.addEventListener('click', () => {
          if (this.messagesList) {
            this.messagesList.innerHTML = `
              <div class="work-welcome-box">
                <h3>Work Mode Workbench</h3>
                <p>Contextual coding assistant with direct workspace access. Inspect files, generate Aider-style fuzzy diffs, and execute terminal commands.</p>
              </div>`;
          }
        });
      }
    }

    switchTab(tabName) {
      const tabs = [
        { name: 'files', btn: this.tabFiles, view: this.viewFiles },
        { name: 'editor', btn: this.tabEditor, view: this.viewEditor },
        { name: 'diff', btn: this.tabDiff, view: this.viewDiff },
        { name: 'terminal', btn: this.tabTerminal, view: this.viewTerminal }
      ];

      tabs.forEach(t => {
        if (t.btn && t.view) {
          const isActive = t.name === tabName;
          t.btn.classList.toggle('active', isActive);
          t.view.classList.toggle('active', isActive);
          t.view.classList.toggle('hidden', !isActive);
        }
      });
    }

    onActivated() {
      if (!this.treeData) {
        this.loadWorkspaceTree();
      }
    }

    async loadWorkspaceTree() {
      if (!this.fileTreeContainer) return;
      this.fileTreeContainer.innerHTML = '<div class="tree-loading">Scanning workspace files…</div>';

      try {
        const fetchFn = window.apiFetch || window.fetch;
        const res = await fetchFn('/api/workspace/tree');
        if (!res.ok) throw new Error('Failed to load file tree: HTTP ' + res.status);
        const data = await res.json();

        if (data.ok && data.tree) {
          this.treeData = data.tree;
          this.renderFileTree(data.tree, this.fileTreeContainer);
          const rootLabel = document.getElementById('wb-root-path-label');
          if (rootLabel && data.root) rootLabel.textContent = data.root;
        } else {
          this.fileTreeContainer.innerHTML = '<div class="tree-error">Could not scan workspace files.</div>';
        }
      } catch (err) {
        this.fileTreeContainer.innerHTML = `<div class="tree-error">${err.message}</div>`;
      }
    }

    renderFileTree(items, container, depth = 0) {
      container.innerHTML = '';
      if (!items || !items.length) {
        container.innerHTML = '<div class="tree-empty">Folder is empty</div>';
        return;
      }

      items.forEach(item => {
        const row = document.createElement('div');
        row.className = `tree-item ${item.type === 'directory' ? 'tree-folder' : 'tree-file'}`;
        row.style.paddingLeft = `${depth * 14 + 6}px`;

        if (item.type === 'directory') {
          const icon = document.createElement('span');
          icon.className = 'tree-icon folder-icon';
          icon.textContent = '📁';
          const nameSpan = document.createElement('span');
          nameSpan.className = 'tree-name';
          nameSpan.textContent = item.name;

          row.appendChild(icon);
          row.appendChild(nameSpan);

          const childContainer = document.createElement('div');
          childContainer.className = 'tree-children';
          childContainer.style.display = 'none';

          row.addEventListener('click', (e) => {
            e.stopPropagation();
            const isOpen = childContainer.style.display === 'block';
            childContainer.style.display = isOpen ? 'none' : 'block';
            icon.textContent = isOpen ? '📁' : '📂';
          });

          container.appendChild(row);
          container.appendChild(childContainer);

          if (item.children && item.children.length) {
            this.renderFileTree(item.children, childContainer, depth + 1);
          }
        } else {
          const icon = document.createElement('span');
          icon.className = 'tree-icon file-icon';
          icon.textContent = this.getFileIcon(item.name);
          const nameSpan = document.createElement('span');
          nameSpan.className = 'tree-name';
          nameSpan.textContent = item.name;

          row.appendChild(icon);
          row.appendChild(nameSpan);

          row.addEventListener('click', (e) => {
            e.stopPropagation();
            document.querySelectorAll('.tree-file').forEach(el => el.classList.remove('active'));
            row.classList.add('active');
            this.openFile(item.path);
          });

          container.appendChild(row);
        }
      });
    }

    getFileIcon(filename) {
      if (filename.endsWith('.py')) return '🐍';
      if (filename.endsWith('.js') || filename.endsWith('.ts')) return '📜';
      if (filename.endsWith('.html')) return '🌐';
      if (filename.endsWith('.css')) return '🎨';
      if (filename.endsWith('.json') || filename.endsWith('.yaml') || filename.endsWith('.yml')) return '⚙️';
      if (filename.endsWith('.md')) return '📝';
      return '📄';
    }

    async openFile(relPath) {
      try {
        const fetchFn = window.apiFetch || window.fetch;
        const res = await fetchFn(`/api/workspace/file?path=${encodeURIComponent(relPath)}`);
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || 'Failed to read file');

        this.activeFilePath = relPath;
        this.activeFileContent = data.content;

        if (this.codeEditor) this.codeEditor.value = data.content;
        if (this.editorFilename) this.editorFilename.textContent = relPath;
        if (this.editorTabLabel) this.editorTabLabel.textContent = relPath.split('/').pop() || 'Editor';
        if (this.saveFileBtn) this.saveFileBtn.disabled = false;
        if (this.attachContextBtn) this.attachContextBtn.disabled = false;

        this.switchTab('editor');
      } catch (err) {
        alert('Could not open file: ' + err.message);
      }
    }

    async saveCurrentFile() {
      if (!this.activeFilePath || !this.codeEditor) return;
      const content = this.codeEditor.value;

      try {
        const fetchFn = window.apiFetch || window.fetch;
        const res = await fetchFn('/api/workspace/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: this.activeFilePath, content: content })
        });
        const data = await res.json();
        if (data.ok) {
          const originalText = this.saveFileBtn.textContent;
          this.saveFileBtn.textContent = 'Saved! ✓';
          this.saveFileBtn.classList.add('btn-success');
          setTimeout(() => {
            this.saveFileBtn.textContent = originalText;
            this.saveFileBtn.classList.remove('btn-success');
          }, 1800);
        } else {
          alert('Save failed: ' + (data.error || 'Unknown error'));
        }
      } catch (err) {
        alert('Save error: ' + err.message);
      }
    }

    attachActiveFileToContext() {
      if (!this.activeFilePath) return;
      this.contextFilePath = this.activeFilePath;
      if (this.activeFileChip) {
        this.activeFileChip.textContent = this.activeFilePath + ' ✕';
        this.activeFileChip.classList.remove('hidden');
      }
      this.appendSystemMessage(`Attached <code>${this.activeFilePath}</code> as active conversation context.`);
    }

    async sendMessage() {
      if (this.isGenerating || !this.promptInput) return;
      const rawText = this.promptInput.value.trim();
      if (!rawText) return;

      this.promptInput.value = '';
      this.appendUserMessage(rawText);

      // Build context prompt
      let fullPrompt = rawText;
      if (this.contextFilePath && this.codeEditor) {
        const snippet = this.codeEditor.value.slice(0, 5000);
        fullPrompt = `[Context File: ${this.contextFilePath}]\n\`\`\`\n${snippet}\n\`\`\`\n\n${rawText}\n\nNote: If proposing code modifications, use the Aider SEARCH/REPLACE format:\n<<<<<<< SEARCH\n(exact existing code)\n=======\n(updated replacement code)\n>>>>>>> REPLACE`;
      }

      const assistantBubble = this.createAssistantMessageBubble();
      this.isGenerating = true;

      try {
        const fetchFn = window.apiFetch || window.fetch;
        const activeModel = (window.getActiveChatModel ? window.getActiveChatModel() : { provider: 'auto', model: 'auto' });
        const res = await fetchFn('/api/send', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text: fullPrompt,
            shown: rawText,
            mode: 'work',
            chat_id: window.activeChatId || 'work_session',
            provider: activeModel.provider,
            model: activeModel.model
          })
        });

        if (!res.ok) throw new Error('HTTP ' + res.status);

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let fullText = '';
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop();

          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed || trimmed.startsWith(':')) continue;
            if (trimmed.startsWith('data: ')) {
              try {
                const payload = JSON.parse(trimmed.slice(6));
                if (payload.token && payload.full) {
                  fullText = payload.full;
                  assistantBubble.querySelector('.work-msg-content').innerHTML = this.renderMarkdown(fullText);
                  this.scrollToBottom();
                } else if (payload.done) {
                  if (payload.text) fullText = payload.text;
                  assistantBubble.querySelector('.work-msg-content').innerHTML = this.renderMarkdown(fullText);
                  if (payload.vram_warning) {
                    this.appendSystemMessage(`<span class="work-warning-text">⚠️ ${this.escapeHtml(payload.vram_warning)}</span>`);
                  }
                  if (payload.diff_proposal && payload.diff_proposal.search) {
                    const dp = payload.diff_proposal;
                    this.pendingDiff = dp;
                    this.renderDiffProposal(dp.path || this.activeFilePath || "Workspace File", dp.search, dp.replace);
                  } else {
                    this.detectAndOfferDiff(fullText);
                  }
                  this.scrollToBottom();
                  break;
                }
              } catch (e) {}
            }
          }
        }
      } catch (err) {
        assistantBubble.querySelector('.work-msg-content').innerHTML = `<span class="work-error-text">Error: ${err.message}</span>`;
      } finally {
        this.isGenerating = false;
      }
    }

    detectAndOfferDiff(text) {
      if (!text) return;
      const searchReplacePattern = /(?:(?:file|target|path)?\s*[:`*]*\s*([a-zA-Z0-9_\-./\\]+\.[a-zA-Z0-9]+)[`*]*\s*\n+)?<{7}\s*SEARCH\s*\n([\s\S]*?)\n={7}\s*\n([\s\S]*?)\n>{7}\s*REPLACE/gi;
      let match = searchReplacePattern.exec(text);

      if (match) {
        const declaredPath = match[1] ? match[1].trim() : null;
        const searchBlock = match[2];
        const replaceBlock = match[3];
        const targetFile = declaredPath || this.contextFilePath || this.activeFilePath || "Active Workspace File";

        this.pendingDiff = {
          path: targetFile,
          search: searchBlock,
          replace: replaceBlock
        };

        this.renderDiffProposal(targetFile, searchBlock, replaceBlock);
      }
    }

    renderDiffProposal(targetFile, searchBlock, replaceBlock) {
      if (this.diffTargetFile) this.diffTargetFile.textContent = targetFile;
      if (this.applyDiffBtn) this.applyDiffBtn.disabled = false;
      if (this.rejectDiffBtn) this.rejectDiffBtn.disabled = false;

      if (this.diffContainer) {
        const searchLines = searchBlock.split('\n').map(l => `<div class="diff-line diff-search">- ${this.escapeHtml(l)}</div>`).join('');
        const replaceLines = replaceBlock.split('\n').map(l => `<div class="diff-line diff-replace">+ ${this.escapeHtml(l)}</div>`).join('');

        this.diffContainer.innerHTML = `
          <div class="diff-card">
            <div class="diff-card-header">Target: <code>${targetFile}</code></div>
            <div class="diff-code-box">
              <div class="diff-section-label">Existing Code (Search):</div>
              <div class="diff-search-block">${searchLines}</div>
              <div class="diff-section-label">Replacement Code:</div>
              <div class="diff-replace-block">${replaceLines}</div>
            </div>
          </div>
        `;
      }

      this.appendSystemMessage(`
        <strong>Diff Proposal Ready:</strong> Aider-style changes generated for <code>${targetFile}</code>.
        <button class="mini-btn highlight" onclick="window.VegaWorkMode.switchTab('diff')">Review in Diff Viewer</button>
      `);
    }

    async applyPendingDiff() {
      if (!this.pendingDiff || !this.pendingDiff.path) return;
      const { path, search, replace } = this.pendingDiff;

      try {
        const fetchFn = window.apiFetch || window.fetch;
        const res = await fetchFn('/api/workspace/apply_diff', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path, search, replace })
        });
        const data = await res.json();
        if (data.ok && data.applied) {
          if (this.applyDiffBtn) {
            this.applyDiffBtn.textContent = 'Applied! ✓';
            this.applyDiffBtn.disabled = true;
          }
          if (this.rejectDiffBtn) this.rejectDiffBtn.disabled = true;

          this.appendSystemMessage(`✓ Applied diff cleanly to <code>${path}</code> (${data.strategy}).`);
          // Reload file in editor if it was open
          if (this.activeFilePath === path) {
            this.openFile(path);
          }
        } else {
          alert('Diff application failed: ' + (data.error || 'Fuzzy search could not find exact block.'));
        }
      } catch (err) {
        alert('Diff apply error: ' + err.message);
      }
    }

    rejectPendingDiff() {
      this.pendingDiff = null;
      if (this.applyDiffBtn) this.applyDiffBtn.disabled = true;
      if (this.rejectDiffBtn) this.rejectDiffBtn.disabled = true;
      if (this.diffTargetFile) this.diffTargetFile.textContent = 'No active diff proposal';
      if (this.diffContainer) {
        this.diffContainer.innerHTML = '<div class="diff-empty-msg">Diff proposal rejected and discarded.</div>';
      }
      this.appendSystemMessage('Diff proposal rejected.');
    }

    async executeTerminal() {
      if (!this.termInput || !this.termOutput) return;
      const cmd = this.termInput.value.trim();
      if (!cmd) return;

      this.termInput.value = '';
      this.appendTerminalLine(`$ ${cmd}`, 'prompt');

      try {
        const fetchFn = window.apiFetch || window.fetch;
        const res = await fetchFn('/api/workspace/terminal', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ command: cmd })
        });
        const data = await res.json();
        if (data.stdout) this.appendTerminalLine(data.stdout, 'stdout');
        if (data.stderr) this.appendTerminalLine(data.stderr, 'stderr');
        if (!data.stdout && !data.stderr && data.ok) {
          this.appendTerminalLine('(command completed with no output)', 'note');
        }
        if (!data.ok && data.error) {
          this.appendTerminalLine(data.error, 'error');
        }
      } catch (err) {
        this.appendTerminalLine('Terminal error: ' + err.message, 'error');
      }
    }

    appendTerminalLine(text, type = 'stdout') {
      if (!this.termOutput) return;
      const div = document.createElement('div');
      div.className = `term-line ${type}`;
      div.textContent = text;
      this.termOutput.appendChild(div);
      this.termOutput.scrollTop = this.termOutput.scrollHeight;
    }

    appendUserMessage(text) {
      if (!this.messagesList) return;
      const div = document.createElement('div');
      div.className = 'work-msg work-msg-user';
      div.innerHTML = `<div class="work-msg-bubble">${this.escapeHtml(text)}</div>`;
      this.messagesList.appendChild(div);
      this.scrollToBottom();
    }

    createAssistantMessageBubble() {
      const div = document.createElement('div');
      div.className = 'work-msg work-msg-assistant';
      div.innerHTML = `
        <div class="work-msg-avatar">V</div>
        <div class="work-msg-bubble">
          <div class="work-msg-content"><span class="work-typing">Thinking…</span></div>
        </div>`;
      this.messagesList.appendChild(div);
      this.scrollToBottom();
      return div;
    }

    appendSystemMessage(html) {
      if (!this.messagesList) return;
      const div = document.createElement('div');
      div.className = 'work-msg work-msg-system';
      div.innerHTML = `<div class="work-sys-pill">${html}</div>`;
      this.messagesList.appendChild(div);
      this.scrollToBottom();
    }

    scrollToBottom() {
      if (this.messagesList) {
        this.messagesList.scrollTop = this.messagesList.scrollHeight;
      }
    }

    renderMarkdown(text) {
      if (window.renderMarkdown) {
        return window.renderMarkdown(text);
      }
      return this.escapeHtml(text).replace(/\n/g, '<br/>');
    }

    escapeHtml(str) {
      if (!str) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    }
  }

  window.addEventListener('DOMContentLoaded', () => {
    window.VegaWorkMode = new WorkModeManager();
  });
})();
