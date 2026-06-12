/* Alpine.js 应用主逻辑 */

function vaultApp() {
  return {
    tree: [],
    currentNote: null,
    filter: '',
    sidebarOpen: window.innerWidth >= 768,  // 桌面端默认展开文件树
    noteOpen: false,                        // 右栏笔记阅读，点笔记/来源时展开
    expanded: {},  // 目录展开状态（path -> bool）

    // 主题：auto（跟随系统）| light | dark
    themePref: 'auto',

    // 问答相关
    mode: 'auto',
    chatTab: 'note',         // note | vault | plan
    messages: [],            // 当前 tab 的对话（引用 _noteMsgs 或 _vaultMsgs）
    _noteMsgs: [],
    _vaultMsgs: [],
    _vaultLoaded: false,     // vault 会话是否已从磁盘惰性加载过
    input: '',
    streaming: false,
    _abortCtrl: null,
    // 全库 RAG
    ragStatus: {notes: '-', chunks: '-'},
    graphStats: {edges: '-', connected_notes: '-'},

    // 收件箱处理浮层
    inboxOpen: false,
    inboxInput: '',          // app 内录入框
    inboxAdding: false,
    // 笔记轻量编辑
    editingNote: false,
    editBody: '',
    savingNote: false,
    // 异步任务系统：type(inbox|stars) → {id, status, progress:[{event,data}], result}
    activeJobs: {},
    _jobTimers: {},
    // 清空双链
    linkClearing: false,
    // 通用弹窗（替代原生 confirm/alert，符合 DESIGN.md）
    confirmModal: {open: false, title: '', body: '', confirmText: '确认', cancelText: '取消', danger: false, _resolve: null},
    noticeModal: {open: false, title: '', body: '', kind: 'info'},
    // 首次使用引导（profile 未配置时弹出）
    onboardOpen: false,
    onboarding: false,   // 是否处于引导流程中（画像存完后续接目录体系）
    // 个人画像配置
    profileOpen: false,
    profileForm: {role: '', working_style: '', _cares: '', _interests: '', _dislikes: '', extra: ''},
    profileSaving: false,
    profileSaved: false,
    // 画像 AI 引导
    wizardStep: 0,
    wizardInput: '',
    wizardAnswers: {},
    wizardSteps: [
      {key: 'role', q: '你的职业 / 方向是？', hint: '一句话定位自己，决定 AI 站在什么视角帮你。',
       examples: ['AI 项目牵头人', 'FDE 前端部署工程师', '产品经理', '全栈工程师', '研究员', '创业者']},
      {key: 'working_style', q: '你平时怎么工作 / 产出？', hint: '你的典型工作流，AI 会贴着它给建议。',
       examples: ['理解业务→AI二开→交付落地', '调研选型→写方案', '快速原型验证', '读论文→提炼', '带团队协作']},
      {key: 'cares_about', q: '你最关心什么？', hint: '决定笔记和回答要重点写什么。',
       examples: ['技术选型', '二开成本', '生产可用性', '落地想象', '同类差异化', '商业价值', '架构设计']},
      {key: 'interests', q: '你的兴趣点？', hint: '帮助 AI 推荐和关联。',
       examples: ['前沿趋势', '开源项目', '产品方法', '行业落地', '感悟随笔', '工程实践']},
      {key: 'dislikes', q: '你不想看到什么？', hint: '决定 AI 要过滤掉什么。',
       examples: ['营销话术', '浅层介绍', '套话总结', '罗列要点不提炼', '过时信息', '纯理论无落地']},
    ],
    profileDrafting: false,
    profileDraftText: '',
    profileDraftDone: false,
    _draftPersona: null,
    // 画像 modal 分页：persona（画像）| taxonomy（目录体系）
    profileTab: 'persona',
    // 目录体系（taxonomy）
    taxonomyForm: {dirs: [], default: ''},
    taxonomyLoading: false,
    taxonomySaving: false,
    taxonomySaved: false,
    taxonomySuggesting: false,
    taxonomySuggestText: '',
    taxonomySuggestDone: false,
    _suggestTaxonomy: null,
    // 单篇重新归类
    reclassifyModal: {open: false, notePath: '', noteName: '', currentDir: '', targetDir: '', reason: '', dirs: [], loading: false, moving: false},
    // 知识缺口补全
    gapModal: {open: false, loading: false, filling: false, candidates: [], progress: [], created: []},
    // 方案规划
    planStreaming: false,
    planHtml: '',
    planMeta: {title: '', summary: ''},
    planInput: '',
    _planContext: null,   // 从问答触发时带的知识库上下文（含完整 answer + 来源 chunk）
    // GitHub Stars 导入（进度走 activeJobs['stars']）
    starsModal: {open: false, username: '', token: '', saveToken: true, hasToken: false, editToken: false, mode: 'latest', limit: 30},
    // 三栏可拖拽宽度（桌面端，持久化到 localStorage）
    treeW: parseInt(localStorage.getItem('treeW'), 10) || 288,
    noteW: parseInt(localStorage.getItem('noteW'), 10) || 480,
    _resizing: null,
    // 知识图谱
    graphBigOpen: false,
    graphStatsBig: {nodes: '-', edges: '-'},
    _miniGraphCtl: null,
    _bigGraphCtl: null,

    async loadTree() {
      try {
        const r = await fetch('/api/files');
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        this.tree = data.tree || [];
        // 默认展开第一层
        this.tree.forEach(n => { if (n.is_dir) this.expanded[n.path] = true; });
      } catch (e) {
        console.error('文件树加载失败', e);
        this.showNotice({title: '文件树加载失败', body: this.escapeHtml(e.message), kind: 'danger'});
      }
    },

    // ----------------- 主题 -----------------

    initTheme() {
      this.themePref = localStorage.getItem('themePref') || 'auto';
      this.applyTheme();
      // auto 模式下跟随系统切换
      const mq = window.matchMedia('(prefers-color-scheme: dark)');
      mq.addEventListener('change', () => { if (this.themePref === 'auto') this.applyTheme(); });
    },

    applyTheme() {
      let effective = this.themePref;
      if (effective === 'auto') {
        effective = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
      }
      document.documentElement.setAttribute('data-theme', effective);
      // 图谱颜色读自 CSS 变量，主题切换后重渲染以适配
      this._rerenderGraphs();
    },

    cycleTheme() {
      this.themePref = ({auto: 'light', light: 'dark', dark: 'auto'})[this.themePref];
      localStorage.setItem('themePref', this.themePref);
      this.applyTheme();
    },

    themeLabel() {
      return ({auto: '跟随系统', light: '白天', dark: '夜间'})[this.themePref];
    },

    themeIcon() {
      const sun = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" stroke-linecap="round"/></svg>';
      const moon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
      const auto = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" stroke="none"/></svg>';
      return ({auto, light: sun, dark: moon})[this.themePref];
    },

    nodeMatchesFilter(node, kw) {
      const lower = kw.toLowerCase();
      if (node.name.toLowerCase().includes(lower)) return true;
      if (node.is_dir && node.children) {
        return node.children.some(c => this.nodeMatchesFilter(c, kw));
      }
      return false;
    },

    renderNode(node, depth) {
      const pad = depth * 16 + 8;  // 加大每级缩进，层级更清晰
      let html = '';

      // Lucide-style SVG 图标（24x24 stroke）
      const ICON = {
        chevronDown: '<svg class="tree-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M6 9l6 6 6-6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        chevronRight: '<svg class="tree-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 6l6 6-6 6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        folder: '<svg class="tree-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
        file: '<svg class="tree-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>',
      };

      if (node.is_dir) {
        const isOpen = this.expanded[node.path] !== false;
        html += `<div class="tree-item tree-folder" style="padding-left:${pad + 8}px"
                      onclick="window._vault.toggle('${this.escapePath(node.path)}')">
                   ${isOpen ? ICON.chevronDown : ICON.chevronRight}
                   ${ICON.folder}
                   <span>${this.escapeHtml(node.name)}</span>
                 </div>`;
        if (isOpen) {
          (node.children || []).forEach(child => {
            if (this.filter === '' || this.nodeMatchesFilter(child, this.filter)) {
              html += this.renderNode(child, depth + 1);
            }
          });
        }
      } else {
        const active = this.currentNote && this.currentNote.path === node.path ? ' active' : '';
        const display = node.name.replace(/^(\d{4}-\d{2}-\d{2})\s+/, '').replace(/\.md$/, '');
        html += `<div class="tree-item tree-file${active}" style="padding-left:${pad + 8}px"
                      onclick="window._vault.openNote('${this.escapePath(node.path)}')"
                      title="${this.escapeHtml(node.name)}">
                   ${ICON.file}
                   <span>${this.escapeHtml(display)}</span>
                 </div>`;
      }
      return html;
    },

    escapeHtml(s) {
      return String(s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    },

    escapePath(p) {
      return p.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    },

    toggle(path) {
      this.expanded[path] = this.expanded[path] === false ? true : false;
    },

    async openNote(path) {
      try {
        const r = await fetch('/api/note?path=' + encodeURIComponent(path));
        if (!r.ok) {
          const err = await r.json().catch(() => ({}));
          throw new Error(err.detail || `HTTP ${r.status}`);
        }
        this.currentNote = await r.json();
        this.noteOpen = true;  // 展开右栏笔记阅读
        this.editingNote = false;  // 切笔记退出编辑态
        // note tab 先清空当前对话，避免回填前串笔记
        if (this.chatTab === 'note') this.messages = [];
        // 总是为新笔记异步拉持久会话到 _noteMsgs（note tab 同时回填 messages；从 vault 点来源开的笔记也预备好，之后切 tab 即正确）
        this._loadConversation('note', this.currentNote.path);
        // 笔记栏滚到顶 + 高亮代码
        setTimeout(() => {
          const aside = document.querySelector('aside.col-read');
          if (aside) aside.scrollTop = 0;
          if (window.Prism) window.Prism.highlightAll();
        }, 50);
        // 移动端打开笔记后关文件树侧栏
        if (window.innerWidth < 768) this.sidebarOpen = false;
        // 图谱联动：高亮当前笔记 + 邻居
        this._highlightGraphs();
      } catch (e) {
        console.error('打开笔记失败', e);
        this.showNotice({title: '打开笔记失败', body: this.escapeHtml(e.message), kind: 'danger'});
      }
    },

    // ----------------- 问答 -----------------

    modeLabel() {
      return ({auto: '综合', note: '仅笔记', deepwiki: 'DeepWiki'})[this.mode] || this.mode;
    },

    canUseDeepWiki() {
      return this.currentNote && this.currentNote.is_github;
    },

    canSend() {
      return this.chatTab === 'vault' ? true : !!this.currentNote;
    },

    switchTab(tab) {
      if (this.chatTab === tab) return;
      // 保存当前 tab 对话
      if (this.chatTab === 'note') this._noteMsgs = this.messages;
      if (this.chatTab === 'vault') this._vaultMsgs = this.messages;
      // 切换
      this.chatTab = tab;
      // 恢复目标 tab 对话
      if (tab === 'note') this.messages = this._noteMsgs;
      else if (tab === 'vault') {
        this.messages = this._vaultMsgs;
        this.loadRagStatus();
        if (!this._vaultLoaded) this._loadConversation('vault', null);  // 首切惰性加载持久会话
      }
      else if (tab === 'plan') { this.planHtml = ''; this.planMeta = {title:'',summary:''}; this.planInput = ''; }
    },

    // ----------------- 对话持久化 -----------------

    // 当前上下文的会话 key：vault→全局；note 且有笔记→该笔记；否则不持久化（plan/无笔记）
    _convKey() {
      if (this.chatTab === 'vault') return {kind: 'vault', note_path: null};
      if (this.chatTab === 'note' && this.currentNote) return {kind: 'note', note_path: this.currentNote.path};
      return null;
    },

    // 惰性拉某上下文的持久会话；回填前校验上下文未变（防快速切换错位）
    async _loadConversation(kind, notePath) {
      try {
        const qs = '?kind=' + kind + (notePath ? '&note_path=' + encodeURIComponent(notePath) : '');
        const r = await fetch('/api/conversation' + qs);
        if (!r.ok) return;
        const data = await r.json();
        const msgs = data.messages || [];
        if (kind === 'note') {
          // 校验加载期间笔记没被切走
          if (!this.currentNote || this.currentNote.path !== notePath) return;
          this._noteMsgs = msgs;
          if (this.chatTab === 'note') this.messages = msgs;  // 仅在 note tab 才回填可见对话
        } else {
          this._vaultLoaded = true;
          this._vaultMsgs = msgs;
          if (this.chatTab === 'vault') this.messages = msgs;
        }
      } catch (e) { /* best-effort，失败保持空对话 */ }
    },

    // 整条会话覆盖写（send 的 finally 调，成功/报错/中断都落盘一次）。
    // key/msgs 显式传入发起时锁定的上下文（msgs 与 _noteMsgs/_vaultMsgs 同一引用，
    // 流式就地 push 已让缓存同步，无需回写）；不传则用当前上下文（best-effort）。
    _persistConversation(key, msgs) {
      key = key || this._convKey();
      if (!key) return;
      msgs = msgs || this.messages;
      fetch('/api/conversation/save', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({kind: key.kind, note_path: key.note_path, messages: msgs}),
      }).catch(() => {});
    },

    async clearConversation() {
      const key = this._convKey();
      if (!key || !this.messages.length) return;
      const ok = await this.askConfirm({
        title: '清空对话', body: '将删除当前上下文的全部对话记录，不可恢复。',
        confirmText: '清空', danger: true,
      });
      if (!ok) return;
      try {
        await fetch('/api/conversation/clear', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({kind: key.kind, note_path: key.note_path}),
        });
      } catch (e) { /* 忽略，前端照样清空 */ }
      this.messages = [];
      if (key.kind === 'note') this._noteMsgs = []; else this._vaultMsgs = [];
    },

    async exportConversation() {
      const key = this._convKey();
      if (!key || !this.messages.length) return;
      try {
        const r = await fetch('/api/conversation/export', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({kind: key.kind, note_path: key.note_path, messages: this.messages}),
        });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
        const data = await r.json();
        this.loadTree();
        this.showNotice({title: '已导出为笔记', body: this.escapeHtml(data.created.path), kind: 'info'});
      } catch (e) {
        this.showNotice({title: '导出失败', body: this.escapeHtml(e.message), kind: 'danger'});
      }
    },

    async loadRagStatus() {
      try {
        const r = await fetch('/api/rag/status');
        if (r.ok) {
          const d = await r.json();
          this.ragStatus = {notes: d.notes, chunks: d.chunks};
          if (d.graph) this.graphStats = d.graph;
        }
      } catch (e) { /* 忽略 */ }
    },

    renderMd(text) {
      if (!text) return '';
      try {
        const html = window.marked ? window.marked.parse(text, {breaks: true, gfm: true}) : text;
        return window.DOMPurify ? window.DOMPurify.sanitize(html) : html;
      } catch (e) {
        return this.escapeHtml(text);
      }
    },

    // 渲染 AI 回答：markdown + 把 [来源N]/来源N 包成可点徽标（点击高亮对应来源卡片）
    renderAnswer(text) {
      let html = this.renderMd(text);
      // 在已 sanitize 的 HTML 上把"来源N"替换为徽标 span（带 data-n）
      html = html.replace(/\[?来源\s*(\d+)\]?/g,
        (m, n) => `<span class="cite-badge" data-n="${n}" onclick="window._vault &amp;&amp; window._vault.focusSource(${n})">来源${n}</span>`);
      return html;
    },

    // 点击回答里的来源徽标 → 高亮并滚到对应来源 chip
    focusSource(n) {
      const el = document.querySelector(`.chip-src[data-n="${n}"]`);
      if (!el) return;
      el.classList.add('chip-flash');
      el.scrollIntoView({behavior: 'smooth', block: 'nearest', inline: 'center'});
      setTimeout(() => el.classList.remove('chip-flash'), 1200);
    },

    toggleRecall(assistantIdx) {
      const a = this.messages[assistantIdx];
      if (a) a.recallOpen = !a.recallOpen;
    },

    async send() {
      const q = this.input.trim();
      if (!q || !this.canSend() || this.streaming) return;

      this.messages.push({role: 'user', text: q, done: true});
      const aIdx = this.messages.length;
      this.messages.push({role: 'assistant', text: '', events: [], sources: [], recall: [], cited: [], recallOpen: false, newLinks: [], done: false});

      // 锁定本轮的会话上下文（数组引用 + 持久化 key）：用户流式中途切 tab/笔记时，
      // 答案仍落进发起时的数组、并按发起时的 key 持久化，不会串到新上下文。
      const msgsRef = this.messages;
      const convKey = this._convKey();

      this.input = '';
      this.streaming = true;
      this._abortCtrl = new AbortController();

      let url, payload;
      if (this.chatTab === 'vault') {
        url = '/api/rag/chat';
        // 带上之前的对话作为多轮上下文（排除刚 push 的这一对）
        const history = this.messages.slice(0, aIdx - 1)
          .filter(m => m.text)
          .map(m => ({role: m.role, content: m.text}));
        payload = {question: q, history};
      } else {
        let effMode = this.mode;
        if (effMode === 'deepwiki' && !this.canUseDeepWiki()) effMode = 'note';
        url = '/api/chat';
        payload = {question: q, note_path: this.currentNote.path, mode: effMode};
      }

      try {
        const r = await fetch(url, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload),
          signal: this._abortCtrl.signal,
        });
        if (!r.ok) {
          const err = await r.json().catch(() => ({detail: r.statusText}));
          throw new Error(err.detail || `HTTP ${r.status}`);
        }
        await this._consumeSSE(r, aIdx, msgsRef);
      } catch (e) {
        console.error('问答失败', e);
        const a = msgsRef[aIdx];
        a.events.push({id: Date.now(), type: 'error', text: e.message});
        a.done = true;
      } finally {
        this.streaming = false;
        this._abortCtrl = null;
        this._scrollChatToBottom();
        this._persistConversation(convKey, msgsRef);  // 成功/报错/中断都落盘一次（partial 已 done=true）
      }
    },

    async _consumeSSE(response, aIdx, msgs) {
      const reader = response.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buf = '';
      const a = msgs[aIdx];
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buf += decoder.decode(value, {stream: true});
        // 按 \n\n 分割完整事件
        let sep;
        while ((sep = buf.indexOf('\n\n')) >= 0) {
          const raw = buf.slice(0, sep);
          buf = buf.slice(sep + 2);
          this._dispatchEvent(raw, a);
        }
      }
      // flush 最后一段
      if (buf.trim()) this._dispatchEvent(buf, a);
      a.done = true;
    },

    _dispatchEvent(raw, assistant) {
      const lines = raw.split('\n');
      let event = 'message', dataStr = '';
      for (const line of lines) {
        if (line.startsWith('event: ')) event = line.slice(7).trim();
        else if (line.startsWith('data: ')) dataStr += line.slice(6);
      }
      if (!dataStr) return;
      let data;
      try { data = JSON.parse(dataStr); } catch { return; }

      const evId = Date.now() + Math.random();
      switch (event) {
        case 'start':
          assistant.events.push({id: evId, type: 'info', text: `模式: ${data.mode}`});
          break;
        case 'sources':
          assistant.sources = data.hits || [];
          this._scrollChatToBottom();
          break;
        case 'recall':
          assistant.recall = data.notes || [];
          break;
        case 'delta':
          assistant.text += data.text || '';
          this._scrollChatToBottom();
          break;
        case 'tool_call':
          assistant.events.push({id: evId, type: 'tool_call', name: data.name, args: data.args});
          this._scrollChatToBottom();
          break;
        case 'tool_done':
          assistant.events.push({id: evId, type: 'tool_done', name: data.name});
          this._scrollChatToBottom();
          break;
        case 'info':
          assistant.events.push({id: evId, type: 'info', text: data.message});
          break;
        case 'error':
          assistant.events.push({id: evId, type: 'error', text: data.message});
          break;
        case 'links':
          assistant.newLinks = data.new || [];
          // 刷新突触统计 + 图谱（长出新边）+ 若正看着某篇被连的笔记，刷新它
          this.loadRagStatus();
          if (assistant.newLinks.length) this._rerenderGraphs();
          if (this.currentNote && assistant.newLinks.some(p => p.some(n => this.currentNote.path.includes(n)))) {
            this.openNote(this.currentNote.path);
          }
          break;
        case 'end':
          if (Array.isArray(data.cited)) assistant.cited = data.cited;
          assistant.done = true;
          break;
      }
    },

    _scrollChatToBottom() {
      setTimeout(() => {
        const log = document.getElementById('chat-log');
        if (log) log.scrollTop = log.scrollHeight;
      }, 30);
    },

    // 找紧邻在 assistant 之前的 user message
    _findUserQuestion(assistantIdx) {
      for (let i = assistantIdx - 1; i >= 0; i--) {
        if (this.messages[i].role === 'user') return this.messages[i].text;
      }
      return '';
    },

    async appendInsight(assistantIdx) {
      const a = this.messages[assistantIdx];
      const q = this._findUserQuestion(assistantIdx);
      if (!a || !a.text || !this.currentNote) return;
      try {
        const r = await fetch('/api/insight/append', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({note_path: this.currentNote.path, question: q, answer: a.text}),
        });
        if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`);
        this.currentNote = await r.json();
        setTimeout(() => { if (window.Prism) window.Prism.highlightAll(); }, 50);
        a.events.push({id: Date.now(), type: 'info', text: '✓ 已追加到原笔记「衍生思考」'});
      } catch (e) {
        a.events.push({id: Date.now(), type: 'error', text: '追加失败: ' + e.message});
      }
    },

    async createFlashNote(assistantIdx) {
      const a = this.messages[assistantIdx];
      const q = this._findUserQuestion(assistantIdx);
      if (!a || !a.text) return;
      // 来源区分：当前笔记问答绑那篇；全库问答绑本次引用的来源笔记
      let payload;
      if (this.chatTab === 'note' && this.currentNote) {
        payload = {question: q, answer: a.text, mode: 'note', note_path: this.currentNote.path};
      } else {
        payload = {question: q, answer: a.text, mode: 'vault', sources: (a.sources || []).map(s => s.note_path)};
      }
      try {
        const r = await fetch('/api/insight/flash', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload),
        });
        if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`);
        const data = await r.json();
        a.events.push({id: Date.now(), type: 'info', text: '✓ 已生成闪念: ' + data.created.path});
        this.loadTree();
      } catch (e) {
        a.events.push({id: Date.now(), type: 'error', text: '生成闪念失败: ' + e.message});
      }
    },

    // ----------------- 异步任务系统（后台线程 + 轮询） -----------------

    isJobRunning(type) {
      const j = this.activeJobs[type];
      return !!(j && j.status === 'running');
    },

    async startJob(type, params) {
      // 已在跑则不重复启动（后端也有单例约束）
      if (this.isJobRunning(type)) return;
      try {
        const r = await fetch('/api/jobs/start', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({type, params: params || {}}),
        });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
        const data = await r.json();
        this.activeJobs[type] = {id: data.job_id, status: 'running', progress: [], result: {}};
        this._startPoll(type);
      } catch (e) {
        this.showNotice({title: '启动任务失败', body: this.escapeHtml(e.message), kind: 'danger'});
      }
    },

    _startPoll(type) {
      if (this._jobTimers[type]) clearInterval(this._jobTimers[type]);
      this._jobTimers[type] = setInterval(() => this._pollJob(type), 1500);
      this._pollJob(type);  // 立即拉一次
    },

    async _pollJob(type) {
      const job = this.activeJobs[type];
      if (!job || !job.id) { this._stopPoll(type); return; }
      try {
        const r = await fetch('/api/jobs/' + job.id);
        if (!r.ok) { this._stopPoll(type); return; }
        const data = await r.json();
        job.progress = data.progress || [];
        job.result = data.result || {};
        const prevStatus = job.status;
        job.status = data.status;
        this._scrollJobLog(type);
        if (data.status !== 'running') {
          this._stopPoll(type);
          if (prevStatus === 'running') {
            // 任务刚结束：刷新文件树/图谱/索引状态
            this.loadTree();
            this.loadRagStatus();
            this._rerenderGraphs();
            // stars 任务若因 token 失效失败，重置 hasToken 让用户重填
            if (type === 'stars') {
              const has401 = (data.progress || []).some(p =>
                p.event === 'error' && /401|token|无效|过期/i.test(JSON.stringify(p.data || {})));
              if (has401 && this.starsModal.open) {
                this.starsModal.hasToken = false;
                this.starsModal.editToken = true;
                this.starsModal.token = '';
                this.showNotice({title: 'GitHub Token 无效', body: '请填入有效的 token（需 read:user 权限）后重试。', kind: 'danger'});
              }
            }
          }
        }
      } catch (e) { /* 网络抖动，下次再轮询 */ }
    },

    _stopPoll(type) {
      if (this._jobTimers[type]) { clearInterval(this._jobTimers[type]); this._jobTimers[type] = null; }
    },

    async loadActiveJobs() {
      // 页面加载时恢复运行中的任务轮询（刷新浏览器后接回）
      try {
        const r = await fetch('/api/jobs/active');
        if (!r.ok) return;
        const data = await r.json();
        for (const j of (data.jobs || [])) {
          this.activeJobs[j.type] = {id: j.id, status: 'running', progress: [], result: {}};
          this._startPoll(j.type);
        }
      } catch (e) { /* 忽略 */ }
    },

    _scrollJobLog(type) {
      setTimeout(() => {
        const el = document.getElementById(type + '-log');
        if (el) el.scrollTop = el.scrollHeight;
      }, 20);
    },

    // 把 job.progress 的 {event,data} 转成收件箱展示行
    inboxRows() {
      const job = this.activeJobs['inbox'];
      if (!job) return [];
      const rows = [];
      for (const p of job.progress) {
        const d = p.data || {};
        if (p.event === 'log') rows.push({kind: 'line', text: d.line});
        else if (p.event === 'error') rows.push({kind: 'err', text: '✗ ' + (d.message || '')});
        else if (p.event === 'end') rows.push({kind: 'end', text: d.code === 0 ? '✓ 完成' : `⚠ 异常（退出码 ${d.code}）`});
      }
      return rows;
    },

    // ----------------- 收件箱处理 -----------------

    openInbox() {
      // 只开窗口，不自动跑管道：现在这里也是录入入口，用户可能只想录入
      this.inboxOpen = true;
    },

    runInbox() {
      // 手动触发处理（开始 / 重新处理）
      if (!this.isJobRunning('inbox')) this.startJob('inbox', {});
    },

    // app 内录入：把链接/文字写进当日收件箱文件（取代在 Obsidian 里手动编辑）
    async addToInbox() {
      const text = (this.inboxInput || '').trim();
      if (!text || this.inboxAdding) return;
      this.inboxAdding = true;
      try {
        const r = await fetch('/api/inbox/add', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({text}),
        });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
        const data = await r.json();
        this.inboxInput = '';
        this.loadTree();  // 收件箱新增了内容，刷新文件树
        this.showNotice({title: '已加入收件箱', body: '写入 ' + this.escapeHtml(data.file) + '，点「开始处理」即抓取消化归位。', kind: 'info'});
      } catch (e) {
        this.showNotice({title: '录入失败', body: this.escapeHtml(e.message), kind: 'danger'});
      } finally {
        this.inboxAdding = false;
      }
    },

    // ----------------- 笔记轻量编辑 -----------------

    startEditNote() {
      if (!this.currentNote || this.currentNote.is_html) return;
      this.editBody = this.currentNote.body || '';
      this.editingNote = true;
    },

    cancelEditNote() {
      this.editingNote = false;
    },

    async saveNote() {
      if (!this.currentNote || this.savingNote) return;
      this.savingNote = true;
      try {
        const r = await fetch('/api/note/save', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({path: this.currentNote.path, body: this.editBody}),
        });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
        this.currentNote = await r.json();
        this.editingNote = false;
        setTimeout(() => { if (window.Prism) window.Prism.highlightAll(); }, 50);
      } catch (e) {
        this.showNotice({title: '保存失败', body: this.escapeHtml(e.message), kind: 'danger'});
      } finally {
        this.savingNote = false;
      }
    },

    // ----------------- 清空双链 -----------------

    async clearLinks() {
      if (!this.currentNote || this.linkClearing) return;
      const name = this.currentNote.name.replace(/\.md$/, '');
      const ok = await this.askConfirm({
        title: '清空双链',
        body: `将移除《${this.escapeHtml(name)}》的「🔗 相关笔记」章节，并删除其它笔记中指向它的链接。<br>此操作会改写文件，且不可撤销。`,
        confirmText: '清空',
        cancelText: '取消',
        danger: true,
      });
      if (!ok) return;
      this.linkClearing = true;
      try {
        const r = await fetch('/api/links/unlink', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({path: this.currentNote.path}),
        });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
        const data = await r.json();
        await this.openNote(this.currentNote.path);  // 刷新当前笔记（🔗 章节已消失）
        this.loadRagStatus();
        this._rerenderGraphs();  // 双链已删，重新拉图谱数据重渲染（该节点的边消失）
        const n = (data.neighbors_cleared || []).length + (data.scanned_removed || []).length;
        this.showNotice({
          title: '已清空双链',
          body: `本笔记章节${data.self_cleared ? '已删除' : '本无'}，移除 <b>${n}</b> 处指向它的关联。`,
          kind: 'success',
        });
      } catch (e) {
        console.error('清空双链失败', e);
        this.showNotice({title: '清空双链失败', body: this.escapeHtml(e.message), kind: 'danger'});
      } finally {
        this.linkClearing = false;
      }
    },

    // ----------------- 通用弹窗（confirm / notice） -----------------

    // 返回 Promise<boolean>，替代原生 confirm
    askConfirm(opts) {
      return new Promise((resolve) => {
        this.confirmModal = {
          open: true,
          title: opts.title || '确认操作',
          body: opts.body || '',
          confirmText: opts.confirmText || '确认',
          cancelText: opts.cancelText || '取消',
          danger: !!opts.danger,
          _resolve: resolve,
        };
      });
    },

    resolveConfirm(val) {
      const r = this.confirmModal._resolve;
      this.confirmModal.open = false;
      this.confirmModal._resolve = null;
      if (r) r(val);
    },

    // 提示弹窗，替代原生 alert。kind: info | success | danger
    showNotice(opts) {
      this.noticeModal = {
        open: true,
        title: opts.title || '提示',
        body: opts.body || '',
        kind: opts.kind || 'info',
      };
    },

    modalGlyph(kind) {
      const check = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M8.5 12.5l2.5 2.5 4.5-5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      const warn = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01" stroke-linecap="round"/></svg>';
      const info = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01" stroke-linecap="round"/></svg>';
      return ({success: check, danger: warn, info})[kind] || info;
    },

    // ----------------- 首次使用引导 -----------------

    async checkOnboarding() {
      // 画像未配置且本机未忽略过 → 弹首次引导
      if (localStorage.getItem('kx_onboard_dismissed')) return;
      try {
        const r = await fetch('/api/profile/status');
        if (!r.ok) return;
        const d = await r.json();
        if (!d.configured) this.onboardOpen = true;
      } catch (e) { /* 静默：引导非关键路径 */ }
    },

    startOnboarding() {
      // 进入画像配置，停在「画像」页（右侧即 AI 引导向导）
      this.onboardOpen = false;
      this.openProfile();
      this.onboarding = true;  // 标记引导中：存完画像会自动续接「目录体系」
    },

    dismissOnboarding() {
      this.onboardOpen = false;
      localStorage.setItem('kx_onboard_dismissed', '1');  // 本机不再自动弹
    },

    // ----------------- 个人画像 -----------------

    async openProfile() {
      this.profileOpen = true;
      this.profileSaved = false;
      this.onboarding = false;  // 默认非引导（从顶栏「画像」直接打开）；startOnboarding 会再置 true
      this.profileTab = 'persona';
      this.resetWizard();
      this.resetTaxonomySuggest();
      await Promise.all([this.loadProfile(), this.loadTaxonomy()]);
    },

    async loadProfile() {
      try {
        const r = await fetch('/api/profile');
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const p = await r.json();
        this.profileForm = {
          role: p.role || '',
          working_style: p.working_style || '',
          _cares: (p.cares_about || []).join(', '),
          _interests: (p.interests || []).join(', '),
          _dislikes: (p.dislikes || []).join(', '),
          extra: p.extra || '',
        };
      } catch (e) {
        this.showNotice({title: '画像加载失败', body: this.escapeHtml(e.message), kind: 'danger'});
      }
    },

    _splitList(s) {
      return String(s || '').split(/[,，、]/).map(x => x.trim()).filter(Boolean);
    },

    async saveProfile() {
      this.profileSaving = true;
      this.profileSaved = false;
      try {
        const persona = {
          role: this.profileForm.role.trim(),
          working_style: this.profileForm.working_style.trim(),
          cares_about: this._splitList(this.profileForm._cares),
          interests: this._splitList(this.profileForm._interests),
          dislikes: this._splitList(this.profileForm._dislikes),
          extra: (this.profileForm.extra || '').trim(),
        };
        const r = await fetch('/api/profile', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({persona}),
        });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
        this.profileSaved = true;
        setTimeout(() => { this.profileSaved = false; }, 3000);
        if (this.onboarding) {
          // 引导流程：画像存好后续接「目录体系」——这一步决定笔记往哪归位
          this.profileTab = 'taxonomy';
          await this.loadTaxonomy();
          this.showNotice({title: '画像已保存 ✓', body: '下一步：确认归位的「目录体系」，可点「AI 推荐」按你的画像生成。', kind: 'success'});
        }
      } catch (e) {
        this.showNotice({title: '保存画像失败', body: this.escapeHtml(e.message), kind: 'danger'});
      } finally {
        this.profileSaving = false;
      }
    },

    // ---- AI 引导 ----
    resetWizard() {
      this.wizardStep = 0;
      this.wizardInput = '';
      this.wizardAnswers = {};
      this.profileDrafting = false;
      this.profileDraftText = '';
      this.profileDraftDone = false;
      this._draftPersona = null;
    },

    toggleExample(ex) {
      const key = this.wizardSteps[this.wizardStep].key;
      const cur = this.wizardAnswers[key] || [];
      this.wizardAnswers[key] = cur.includes(ex) ? cur.filter(x => x !== ex) : [...cur, ex];
    },

    addCustomExample() {
      const v = this.wizardInput.trim();
      if (!v) return;
      const key = this.wizardSteps[this.wizardStep].key;
      const cur = this.wizardAnswers[key] || [];
      if (!cur.includes(v)) this.wizardAnswers[key] = [...cur, v];
      this.wizardInput = '';
    },

    wizardNext() { if (this.wizardStep < this.wizardSteps.length - 1) this.wizardStep++; this.wizardInput = ''; },
    wizardPrev() { if (this.wizardStep > 0) this.wizardStep--; this.wizardInput = ''; },

    async draftFromWizard() {
      this.addCustomExample();  // 把残留输入也收进去
      this.profileDrafting = true;
      this.profileDraftText = '';
      this.profileDraftDone = false;
      this._draftPersona = null;
      try {
        const r = await fetch('/api/profile/draft', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({answers: this.wizardAnswers}),
        });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
        await this._consumeDraftSSE(r);
      } catch (e) {
        this.showNotice({title: 'AI 提炼失败', body: this.escapeHtml(e.message), kind: 'danger'});
      } finally {
        this.profileDrafting = false;
      }
    },

    async _consumeDraftSSE(response) {
      const reader = response.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buf = '';
      const handle = (raw) => {
        const lines = raw.split('\n');
        let event = 'message', dataStr = '';
        for (const line of lines) {
          if (line.startsWith('event: ')) event = line.slice(7).trim();
          else if (line.startsWith('data: ')) dataStr += line.slice(6);
        }
        if (!dataStr) return;
        let data; try { data = JSON.parse(dataStr); } catch { return; }
        if (event === 'delta') this.profileDraftText += data.text || '';
        else if (event === 'profile') { this._draftPersona = data.persona || null; this.profileDraftDone = true; }
        else if (event === 'error') this.showNotice({title: 'AI 提炼出错', body: this.escapeHtml(data.message), kind: 'danger'});
      };
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buf += decoder.decode(value, {stream: true});
        let sep;
        while ((sep = buf.indexOf('\n\n')) >= 0) { handle(buf.slice(0, sep)); buf = buf.slice(sep + 2); }
      }
      if (buf.trim()) handle(buf);
    },

    adoptDraft() {
      const p = this._draftPersona;
      if (!p) return;
      this.profileForm = {
        role: p.role || this.profileForm.role,
        working_style: p.working_style || this.profileForm.working_style,
        _cares: (p.cares_about || []).join(', '),
        _interests: (p.interests || []).join(', '),
        _dislikes: (p.dislikes || []).join(', '),
        extra: this.profileForm.extra,
      };
      this.showNotice({title: '已填入左侧', body: '可微调后点「保存画像」生效。', kind: 'success'});
    },

    // ----------------- 目录体系（taxonomy） -----------------

    async loadTaxonomy() {
      this.taxonomyLoading = true;
      try {
        const r = await fetch('/api/taxonomy');
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const t = await r.json();
        this.taxonomyForm = {
          dirs: (t.dirs || []).map(d => ({path: d.path || '', desc: d.desc || ''})),
          default: t.default || '',
        };
      } catch (e) {
        this.showNotice({title: '目录体系加载失败', body: this.escapeHtml(e.message), kind: 'danger'});
      } finally {
        this.taxonomyLoading = false;
      }
    },

    addTaxonomyDir() {
      this.taxonomyForm.dirs.push({path: '', desc: ''});
    },

    removeTaxonomyDir(i) {
      this.taxonomyForm.dirs.splice(i, 1);
    },

    async saveTaxonomy() {
      // 过滤空 path 行
      const dirs = this.taxonomyForm.dirs
        .map(d => ({path: (d.path || '').trim().replace(/\/+$/, ''), desc: (d.desc || '').trim()}))
        .filter(d => d.path);
      if (!dirs.length) {
        this.showNotice({title: '无法保存', body: '至少保留一个目录。', kind: 'danger'});
        return;
      }
      this.taxonomySaving = true;
      this.taxonomySaved = false;
      try {
        const r = await fetch('/api/taxonomy', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({taxonomy: {dirs, default: (this.taxonomyForm.default || '').trim()}}),
        });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
        const t = await r.json();
        this.taxonomyForm = {
          dirs: (t.dirs || []).map(d => ({path: d.path || '', desc: d.desc || ''})),
          default: t.default || '',
        };
        this.taxonomySaved = true;
        setTimeout(() => { this.taxonomySaved = false; }, 3000);
        if (this.onboarding) {
          // 引导收尾：画像 + 目录都已就绪
          this.onboarding = false;
          this.profileOpen = false;
          this.showNotice({title: '🎉 初始化完成', body: '画像与目录已就绪。现在去「收件箱」贴几条链接，点「处理收件箱」试试吧。', kind: 'success'});
        }
      } catch (e) {
        this.showNotice({title: '保存目录失败', body: this.escapeHtml(e.message), kind: 'danger'});
      } finally {
        this.taxonomySaving = false;
      }
    },

    resetTaxonomySuggest() {
      this.taxonomySuggesting = false;
      this.taxonomySuggestText = '';
      this.taxonomySuggestDone = false;
      this._suggestTaxonomy = null;
    },

    async suggestTaxonomy() {
      this.resetTaxonomySuggest();
      this.taxonomySuggesting = true;
      // 带上当前画像表单（可能未保存）作为推荐依据
      const persona = {
        role: this.profileForm.role.trim(),
        working_style: this.profileForm.working_style.trim(),
        cares_about: this._splitList(this.profileForm._cares),
        interests: this._splitList(this.profileForm._interests),
        dislikes: this._splitList(this.profileForm._dislikes),
        extra: (this.profileForm.extra || '').trim(),
      };
      try {
        const r = await fetch('/api/taxonomy/suggest', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({persona}),
        });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
        await this._pumpSSE(r, (event, data) => {
          if (event === 'delta') this.taxonomySuggestText += data.text || '';
          else if (event === 'taxonomy') { this._suggestTaxonomy = data.taxonomy || null; this.taxonomySuggestDone = true; }
          else if (event === 'error') this.showNotice({title: '推荐出错', body: this.escapeHtml(data.message), kind: 'danger'});
        });
      } catch (e) {
        this.showNotice({title: 'AI 推荐失败', body: this.escapeHtml(e.message), kind: 'danger'});
      } finally {
        this.taxonomySuggesting = false;
      }
    },

    adoptTaxonomySuggestion() {
      const t = this._suggestTaxonomy;
      if (!t || !Array.isArray(t.dirs)) return;
      this.taxonomyForm = {
        dirs: t.dirs.filter(d => d && d.path).map(d => ({path: d.path, desc: d.desc || ''})),
        default: t.default || this.taxonomyForm.default,
      };
      this.resetTaxonomySuggest();
      this.showNotice({title: '已填入目录列表', body: '可微调后点「保存目录」生效。', kind: 'success'});
    },

    // 通用 SSE 消费：onEvent(event, data) 回调
    async _pumpSSE(response, onEvent) {
      const reader = response.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buf = '';
      const handle = (raw) => {
        const lines = raw.split('\n');
        let event = 'message', dataStr = '';
        for (const line of lines) {
          if (line.startsWith('event: ')) event = line.slice(7).trim();
          else if (line.startsWith('data: ')) dataStr += line.slice(6);
        }
        if (!dataStr) return;
        let data; try { data = JSON.parse(dataStr); } catch { return; }
        onEvent(event, data);
      };
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buf += decoder.decode(value, {stream: true});
        let sep;
        while ((sep = buf.indexOf('\n\n')) >= 0) { handle(buf.slice(0, sep)); buf = buf.slice(sep + 2); }
      }
      if (buf.trim()) handle(buf);
    },

    // ----------------- 单篇重新归类 -----------------

    async openReclassify() {
      if (!this.currentNote) return;
      const name = this.currentNote.name.replace(/\.md$/, '');
      this.reclassifyModal = {
        open: true, notePath: this.currentNote.path, noteName: name,
        currentDir: '', targetDir: '', reason: '', dirs: [], loading: true, moving: false,
      };
      try {
        // 并行：拿目录列表 + AI 建议
        const [taxR, recR] = await Promise.all([
          fetch('/api/taxonomy'),
          fetch('/api/note/reclassify', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({note_path: this.currentNote.path}),
          }),
        ]);
        if (taxR.ok) {
          const t = await taxR.json();
          this.reclassifyModal.dirs = (t.dirs || []).map(d => d.path).filter(Boolean);
        }
        if (!recR.ok) throw new Error((await recR.json().catch(() => ({}))).detail || `HTTP ${recR.status}`);
        const rec = await recR.json();
        this.reclassifyModal.currentDir = rec.current_dir || '';
        this.reclassifyModal.targetDir = rec.target_dir || '';
        this.reclassifyModal.reason = rec.reason || '';
        // 建议目录不在列表里也补进去，保证 select 能选中
        if (rec.target_dir && !this.reclassifyModal.dirs.includes(rec.target_dir)) {
          this.reclassifyModal.dirs.unshift(rec.target_dir);
        }
      } catch (e) {
        this.reclassifyModal.open = false;
        this.showNotice({title: '获取归类建议失败', body: this.escapeHtml(e.message), kind: 'danger'});
      } finally {
        this.reclassifyModal.loading = false;
      }
    },

    async confirmReclassify() {
      const m = this.reclassifyModal;
      if (!m.notePath || !m.targetDir) return;
      if (m.targetDir === m.currentDir) {
        this.showNotice({title: '无需移动', body: '目标目录与当前目录相同。', kind: 'info'});
        return;
      }
      m.moving = true;
      try {
        const r = await fetch('/api/note/move', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({note_path: m.notePath, target_dir: m.targetDir}),
        });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
        const data = await r.json();
        m.open = false;
        await this.loadTree();
        if (data.new_path) await this.openNote(data.new_path);
        this.loadRagStatus();
        this._rerenderGraphs();
        this.showNotice({
          title: '已重新归类',
          body: `已移动到 <b>${this.escapeHtml(m.targetDir)}</b>。`,
          kind: 'success',
        });
      } catch (e) {
        this.showNotice({title: '移动失败', body: this.escapeHtml(e.message), kind: 'danger'});
      } finally {
        m.moving = false;
      }
    },

    // ----------------- 知识缺口补全 -----------------

    async openGapFill(assistantIdx) {
      const a = this.messages[assistantIdx];
      const q = this._findUserQuestion(assistantIdx);
      if (!q) return;
      const titles = (a && a.sources ? a.sources : []).map(s => s.note_title).filter(Boolean);
      this.gapModal = {open: true, loading: true, filling: false, candidates: [], progress: [], created: [], question: q};
      try {
        const r = await fetch('/api/gap/suggest', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({question: q, recalled_titles: titles}),
        });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
        const data = await r.json();
        this.gapModal.candidates = (data.candidates || []).map(c => ({...c, _checked: true}));
      } catch (e) {
        this.gapModal.open = false;
        this.showNotice({title: '推荐失败', body: this.escapeHtml(e.message), kind: 'danger'});
      } finally {
        this.gapModal.loading = false;
      }
    },

    async fillGaps() {
      const repos = this.gapModal.candidates.filter(c => c._checked);
      if (!repos.length) return;
      this.gapModal.filling = true;
      try {
        const r = await fetch('/api/gap/fill', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({repos, question: this.gapModal.question || ''}),
        });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
        const data = await r.json();
        this.gapModal.open = false;
        this.loadTree();  // 收件箱新增了一个 .md，刷新文件树
        const ok = await this.askConfirm({
          title: '已加入收件箱',
          body: `已把 <b>${data.written}</b> 个仓库写入收件箱<br><span class="mono" style="font-size:11px">${this.escapeHtml(data.file || '')}</span><br><br>现在就抓取消化吗？（也可稍后自己点顶栏「处理收件箱」）`,
          confirmText: '立即处理',
          cancelText: '稍后',
        });
        if (ok) this.openInbox();
      } catch (e) {
        this.showNotice({title: '加入收件箱失败', body: this.escapeHtml(e.message), kind: 'danger'});
      } finally {
        this.gapModal.filling = false;
      }
    },

    // ----------------- 方案规划 -----------------

    canSendPlan() { return this.planInput.trim() && !this.planStreaming; },

    switchToPlan(contextFromQA) {
      // 必须在切 chatTab 之前抓当前对话（此刻 this.messages 还是 vault 的）
      const curMsgs = this.messages;
      this.chatTab = 'plan';
      this.planHtml = '';
      this.planMeta = {title: '', summary: ''};
      this.planInput = '';
      this._planContext = null;
      if (contextFromQA) {
        this.planInput = '基于以下问答内容生成项目方案。\n\n## 问题\n' + (contextFromQA.question || '');
        // 知识库上下文：完整 AI 回答 + 召回笔记正文（不是标题），让方案规划拿到实体洞察
        let ctx = '## 用户原始问题\n' + (contextFromQA.question || '') + '\n\n';
        ctx += '## AI 综合回答（含知识库洞察）\n' + (contextFromQA.answer || '') + '\n\n';
        // 优先用按钮传入的 recall（最可靠），回退到从当前对话里找
        let recall = contextFromQA.recall;
        if (!recall || !recall.length) {
          const lastAsst = [...(curMsgs || [])].reverse().find(m => m.role === 'assistant' && m.recall && m.recall.length);
          recall = lastAsst ? lastAsst.recall : [];
        }
        if (recall && recall.length) {
          ctx += '## 知识库参考笔记正文\n\n';
          for (const note of recall) {
            ctx += '### ' + (note.note_title || note.note_path || '') + '\n';
            for (const c of (note.chunks || [])) {
              ctx += (c.section ? '（' + c.section + '）' : '') + (c.text_preview || c.text || '') + '\n\n';
            }
          }
        }
        this._planContext = ctx;
      }
    },

    async sendPlan() {
      if (!this.canSendPlan()) return;
      this.planStreaming = true;
      this.planHtml = '';
      this.planMeta = {title: '', summary: ''};
      try {
        const r = await fetch('/api/plan/generate', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({requirements: this.planInput, knowledge_context: this._planContext || null}),
          signal: this._abortCtrl ? this._abortCtrl.signal : undefined,
        });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
        await this._pumpSSE(r, (event, data) => {
          if (event === 'delta') { this.planHtml += data.text || ''; }
          else if (event === 'plan_meta') this.planMeta = {title: data.title || '', summary: data.summary || ''};
          else if (event === 'error') this.showNotice({title: '方案生成出错', body: this.escapeHtml(data.message), kind: 'danger'});
        });
      } catch (e) {
        this.showNotice({title: '方案生成失败', body: this.escapeHtml(e.message), kind: 'danger'});
      } finally {
        this.planStreaming = false;
      }
    },

    async savePlan() {
      if (!this.planHtml) { this.showNotice({title: '无法保存', body: '尚未生成方案内容。', kind: 'danger'}); return; }
      if (!this.planMeta.title) { this.showNotice({title: '无法保存', body: '方案标题为空，请重新生成。', kind: 'danger'}); return; }
      const ok = await this.askConfirm({
        title: '保存方案',
        body: `将保存「<b>${this.escapeHtml(this.planMeta.title)}</b>」到 04-项目/ 目录。`,
        confirmText: '保存',
        cancelText: '取消',
      });
      if (!ok) return;
      try {
        const r = await fetch('/api/plan/save', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({html: this.planHtml, title: this.planMeta.title, summary: this.planMeta.summary}),
        });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
        const data = await r.json();
        await this.loadTree();
        this.showNotice({title: '已保存', body: `<span class="mono" style="font-size:11px">${this.escapeHtml(data.path)}</span>`, kind: 'success'});
      } catch (e) {
        this.showNotice({title: '保存失败', body: this.escapeHtml(e.message), kind: 'danger'});
      }
    },

    // ----------------- GitHub Stars 导入 -----------------

    async openStars() {
      const saved = localStorage.getItem('starsUsername') || '';
      this.starsModal = {open: true, username: saved, token: '', saveToken: true, hasToken: false, editToken: false, mode: 'latest', limit: 30};
      // 查询后端是否已配 GITHUB_TOKEN（已配则隐藏输入框）
      try {
        const r = await fetch('/api/stars/token-status');
        if (r.ok) this.starsModal.hasToken = (await r.json()).has_token;
      } catch (e) { /* 忽略 */ }
    },

    async runStarsImport() {
      const m = this.starsModal;
      if (!m.username.trim() || this.isJobRunning('stars')) return;
      localStorage.setItem('starsUsername', m.username.trim());
      if (m.hasToken === false && (m.token || '').trim()) m.hasToken = true;
      await this.startJob('stars', {
        username: m.username.trim(), mode: m.mode, limit: Number(m.limit) || 30,
        token: (m.token || '').trim(), save_token: m.saveToken,
      });
    },

    // 把 stars job.progress 转成展示行
    starsRows() {
      const job = this.activeJobs['stars'];
      if (!job) return [];
      const rows = [];
      for (const p of job.progress) {
        const d = p.data || {};
        if (p.event === 'fetched') rows.push({kind: 'info', text: `共 ${d.total_stars} 个 star，待处理 ${d.to_process} 个（已完成 ${d.already_done}）`});
        else if (p.event === 'repo_start') rows.push({kind: 'start', text: `→ [${d.i}/${d.total}] ${d.repo}（★${d.stars ?? '?'} ${d.lang || ''}）`});
        else if (p.event === 'placed') rows.push({kind: 'placed', repo: d.repo, path: d.path});
        else if (p.event === 'skipped') rows.push({kind: 'info', text: `　⏭ ${d.repo}：${d.message}`});
        else if (p.event === 'error') rows.push({kind: 'error', text: `　✗ ${d.repo || ''}：${d.message}`});
      }
      return rows;
    },

    starsCreatedCount() {
      const job = this.activeJobs['stars'];
      if (!job) return 0;
      return job.progress.filter(p => p.event === 'placed').length;
    },

    // ----------------- 知识图谱 -----------------

    async _fetchGraph(scope) {
      const r = await fetch('/api/graph?scope=' + scope);
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
      return r.json();
    },

    // 左下小图：连通主图，点节点跳笔记，高亮当前笔记
    async loadMiniGraph() {
      const el = document.getElementById('mini-graph');
      if (!el || !window.VaultGraph) return;
      try {
        const data = await this._fetchGraph('connected');
        if (this._miniGraphCtl) this._miniGraphCtl.destroy();
        this._miniGraphCtl = window.VaultGraph.render(el, data, {
          showLabels: false,
          currentPath: this.currentNote ? this.currentNote.path : null,
          onSelect: (path) => this.openNote(path),
        });
      } catch (e) {
        console.error('图谱加载失败', e);
      }
    },

    // 放大：全屏铺开全部笔记（含孤立散点）
    async openGraphBig() {
      this.graphBigOpen = true;
      await this.$nextTick();
      const el = document.getElementById('big-graph');
      if (!el || !window.VaultGraph) return;
      try {
        const data = await this._fetchGraph('all');
        this.graphStatsBig = {nodes: data.stats.nodes, edges: data.stats.edges};
        if (this._bigGraphCtl) this._bigGraphCtl.destroy();
        this._bigGraphCtl = window.VaultGraph.render(el, data, {
          showLabels: true,
          currentPath: this.currentNote ? this.currentNote.path : null,
          onSelect: (path) => { this.openNote(path); },
        });
      } catch (e) {
        console.error('大图加载失败', e);
        this.showNotice({title: '图谱加载失败', body: this.escapeHtml(e.message), kind: 'danger'});
      }
    },

    closeGraphBig() {
      this.graphBigOpen = false;
      if (this._bigGraphCtl) { this._bigGraphCtl.destroy(); this._bigGraphCtl = null; }
    },

    // 当前笔记变化时高亮（openNote 调用）
    _highlightGraphs() {
      const p = this.currentNote ? this.currentNote.path : null;
      if (this._miniGraphCtl) this._miniGraphCtl.highlight(p);
      if (this._bigGraphCtl) this._bigGraphCtl.highlight(p);
    },

    // 主题切换后重渲染（颜色读自 CSS 变量）
    _rerenderGraphs() {
      if (this._miniGraphCtl) this.loadMiniGraph();
      if (this._bigGraphCtl && this.graphBigOpen) this.openGraphBig();
    },

    // ----------------- 三栏拖拽 -----------------

    startResize(which, ev) {
      ev.preventDefault();
      this._resizing = which;
      const startX = ev.clientX;
      const startTree = this.treeW;
      const startNote = this.noteW;
      const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
      const target = ev.currentTarget;
      if (target) target.classList.add('dragging');
      document.body.style.userSelect = 'none';
      document.body.style.cursor = 'col-resize';

      const move = (e) => {
        const dx = e.clientX - startX;
        if (which === 'tree') this.treeW = clamp(startTree + dx, 180, 560);
        else this.noteW = clamp(startNote - dx, 300, 760);  // 右栏向左拖变宽
      };
      const up = () => {
        document.removeEventListener('mousemove', move);
        document.removeEventListener('mouseup', up);
        document.body.style.userSelect = '';
        document.body.style.cursor = '';
        if (target) target.classList.remove('dragging');
        localStorage.setItem('treeW', String(this.treeW));
        localStorage.setItem('noteW', String(this.noteW));
        this._resizing = null;
      };
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
    },
  };
}

// 暴露给 onclick 调用（Alpine 在 x-html 里失活，只能用 window）
document.addEventListener('alpine:init', () => {
  Alpine.store('boot', { ready: true });
});

document.addEventListener('alpine:initialized', () => {
  // 找到根 Alpine 组件实例
  const root = document.querySelector('[x-data]');
  if (root && root._x_dataStack) {
    window._vault = root._x_dataStack[0];
  }
});
