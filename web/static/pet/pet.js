/* KnowledgeX 桌宠 · 投喂逻辑（MVP / Phase 1）
 *
 * 设计：宠物=品牌星座图谱的化身。喂它 → 走现有收件箱管道 → 真的长出一个新节点。
 * 它只是个「薄客户端」：分类交给后端 scan.py，自己只按形状把内容 POST 到
 *   /api/inbox/add（链接/文本） 或 /api/inbox/upload（文件），
 * 再 POST /api/jobs/start 起单例消化任务，轮询 /api/jobs/{id} 解析日志驱动动画。
 * 桌面端 DESKTOP_LOCAL=1，故同源请求免 token。
 */
(() => {
  "use strict";
  // withGlobalTauri 下 window.__TAURI__ 在顶层同步执行时可能尚未注入（见 tauri#12990），
  // 故惰性取用 + 轮询等待，确保快捷键监听一定能挂上。
  const getT = () => window.__TAURI__;
  const whenTauri = (cb, n = 0) => {
    const t = getT();
    if (t) cb(t);
    else if (n < 100) setTimeout(() => whenTauri(cb, n + 1), 30);
  };
  const AUTO_HIDE_ON_IDLE = true;

  // ---------- DOM ----------
  const $ = (id) => document.getElementById(id);
  const sky = $("sky");
  const panels = { hint: $("hint"), card: $("card"), status: $("status"), result: $("result") };
  const cardBadge = $("card-badge"), cardSrc = $("card-src"), cardPreview = $("card-preview");
  const ring = $("ring"), stageEl = $("stage"), detailEl = $("detail"), resultEl = $("result");
  const dropEl = $("drop");

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const hostOf = (u) => { try { return new URL(u).host; } catch { return ""; } };

  function setPanel(name) {
    for (const k in panels) panels[k].classList.toggle("hidden", k !== name);
  }

  function hidePetWindow() {
    const t = getT();
    if (!t) return;
    if (t.core) {
      t.core.invoke("hide_pet").catch(() => {
        try { t.window.getCurrentWindow().hide(); } catch {}
      });
      return;
    }
    try { t.window.getCurrentWindow().hide(); } catch {}
  }

  function holdPetPrompt() {
    const t = getT();
    if (t && t.core) t.core.invoke("hold_pet_prompt").catch(() => {});
  }

  function releasePetPrompt() {
    const t = getT();
    if (t && t.core) t.core.invoke("release_pet_prompt").catch(() => {});
  }

  // ---------- 状态 → 动画目标 ----------
  let state = "idle";
  let coreTarget = 0.55, spinMul = 1;
  function setState(s) {
    state = s;
    ({
      idle:      () => { coreTarget = 0.55; spinMul = 1.0; },
      card:      () => { coreTarget = 0.90; spinMul = 1.3; },
      feeding:   () => { coreTarget = 1.00; spinMul = 2.4; },
      digesting: () => { coreTarget = 0.95; spinMul = 3.4; },
      done:      () => { coreTarget = 1.00; spinMul = 1.5; },
      fail:      () => { coreTarget = 0.50; spinMul = 0.8; },
    }[s] || (() => {}))();
  }

  // ---------- 星座画布 ----------
  const DPR = Math.max(1, window.devicePixelRatio || 1);
  const W = 104, H = 104, CX = W / 2, CY = H / 2;
  const ctx = sky.getContext("2d");
  sky.width = W * DPR; sky.height = H * DPR; ctx.scale(DPR, DPR);

  const ALT = ["#7c3aed", "#a0c3ec"];
  let sats = [];
  function initSats() {
    sats = [];
    const specs = [[22, 0.0, 1.1, 2.2], [30, 1.1, -0.7, 1.8], [38, 2.3, 0.9, 2.0],
                   [26, 3.5, -1.2, 1.6], [42, 4.4, 0.6, 2.3], [33, 5.4, -0.9, 1.9]];
    specs.forEach((s, i) => sats.push({
      baseR: s[0], ang: s[1], spd: s[2], size: s[3],
      color: ALT[i % 2], phase: i * 1.3, born: 0,
    }));
  }
  function spawnSat(color) {
    sats.push({ baseR: 20 + Math.abs(Math.sin(sats.length) * 24), ang: sats.length * 1.7,
      spd: (sats.length % 2 ? -1 : 1) * 0.9, size: 2.2, color, phase: sats.length, born: performance.now() });
    if (sats.length > 12) sats.splice(0, 1); // 控制数量，纯视觉
  }

  let spin = 0, coreGlow = 0.55, tprev = performance.now();
  function frame(now) {
    const dt = Math.min(0.05, (now - tprev) / 1000); tprev = now;
    spin += dt * spinMul * 0.25;
    coreGlow += (coreTarget - coreGlow) * Math.min(1, dt * 4);
    const breathe = 1 + 0.06 * Math.sin(now * 0.0012);

    ctx.clearRect(0, 0, W, H);

    // edges
    ctx.lineWidth = 1;
    for (const s of sats) {
      const r = s.baseR * (1 + 0.05 * Math.sin(now * 0.001 + s.phase));
      const a = s.ang + spin * s.spd;
      const x = CX + r * Math.cos(a), y = CY + r * Math.sin(a);
      ctx.strokeStyle = `rgba(160,195,236,${0.10 + 0.12 * coreGlow})`;
      ctx.beginPath(); ctx.moveTo(CX, CY); ctx.lineTo(x, y); ctx.stroke();
    }
    // satellites
    for (const s of sats) {
      const r = s.baseR * (1 + 0.05 * Math.sin(now * 0.001 + s.phase));
      const a = s.ang + spin * s.spd;
      const x = CX + r * Math.cos(a), y = CY + r * Math.sin(a);
      let pop = 1;
      if (s.born) pop = Math.min(1.25, (now - s.born) / 320) * (now - s.born < 600 ? 1.2 : 1);
      ctx.fillStyle = s.color;
      ctx.globalAlpha = 0.55 + 0.4 * coreGlow;
      ctx.beginPath(); ctx.arc(x, y, s.size * Math.min(1.25, pop), 0, Math.PI * 2); ctx.fill();
      ctx.globalAlpha = 1;
    }
    // core
    const cr = 9 * breathe * (0.85 + 0.25 * coreGlow);
    const g = ctx.createRadialGradient(CX, CY, 0, CX, CY, cr * 2.4);
    g.addColorStop(0, `rgba(255,194,133,${0.9 * coreGlow + 0.1})`);
    g.addColorStop(0.4, `rgba(255,122,23,${0.85 * coreGlow})`);
    g.addColorStop(1, "rgba(255,122,23,0)");
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(CX, CY, cr * 2.4, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = `rgba(255,170,90,${0.7 + 0.3 * coreGlow})`;
    ctx.beginPath(); ctx.arc(CX, CY, cr * 0.62, 0, Math.PI * 2); ctx.fill();

    requestAnimationFrame(frame);
  }
  initSats(); requestAnimationFrame(frame);

  // ---------- idle / 提示 ----------
  const HINT_DEFAULT = '拖<b>链接</b>进来 · <a class="pick" id="pick">＋ 文件</a><br>或按 <kbd>⌘⇧C</kbd> 投喂剪贴板';
  function backToIdle() { setState("idle"); panels.hint.innerHTML = HINT_DEFAULT; setPanel("hint"); }
  let hintTimer = 0;
  function flashHint(msg) {
    clearTimeout(hintTimer);
    panels.hint.textContent = msg; setPanel("hint"); setState("idle");
    hintTimer = setTimeout(() => {
      backToIdle();
      if (AUTO_HIDE_ON_IDLE) hidePetWindow();
    }, 2600);
  }

  // ---------- 投喂卡（快捷键捕获） ----------
  let pending = null, countdown = 0;
  function clearCountdown() { if (countdown) { clearInterval(countdown); countdown = 0; } }
  function showCard(p) {
    clearCountdown();
    pending = p;  // 整个 {kind, text, preview}
    cardBadge.textContent = p.kind === "url" ? "链接" : (p.kind === "image" ? "图片" : "文本");
    cardSrc.textContent = p.kind === "url" ? hostOf(p.text) : "";
    cardPreview.textContent = p.preview || p.text || "";
    setState("card"); setPanel("card");
    let n = 10; ring.textContent = n;
    countdown = setInterval(() => {
      n -= 1; ring.textContent = n > 0 ? n : "";
      if (n <= 0) { releasePetPrompt(); dismissCard(); hidePetWindow(); }
    }, 1000);
  }
  function dismissCard() { clearCountdown(); pending = null; backToIdle(); }

  $("feed").onclick = () => {
    clearCountdown();
    const p = pending; pending = null;
    if (!p) return;
    holdPetPrompt();
    if (p.kind === "image") feedImage();
    else if (p.text) feedText(p.text);
  };
  $("dismiss").onclick = () => { releasePetPrompt(); dismissCard(); hidePetWindow(); };
  $("close").onclick = () => { releasePetPrompt(); hidePetWindow(); };

  // ---------- 复制自动监听开关（默认开，localStorage 记忆；托盘也可切换） ----------
  const watchBtn = $("watch");
  let watchOn = true;
  function applyWatch(on, persist) {
    watchOn = on;
    if (watchBtn) {
      watchBtn.classList.toggle("on", on);
      watchBtn.title = on ? "复制即投喂：开（点击关）" : "复制即投喂：关（点击开）";
    }
    if (persist) { try { localStorage.setItem("kx_clip_watch", on ? "1" : "0"); } catch {} }
    const t = getT();
    if (t && t.core) t.core.invoke("set_clip_monitor", { enabled: on }).catch(() => {});
  }
  if (watchBtn) watchBtn.onclick = () => applyWatch(!watchOn, true);

  // ---------- 投喂 → 后端 ----------
  async function feedText(text) {
    text = (text || "").trim();
    if (!text) return;
    setState("feeding"); stageEl.textContent = "投喂中…"; detailEl.textContent = ""; setPanel("status");
    try {
      const r = await fetch("/api/inbox/add", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!r.ok) throw new Error("add " + r.status);
      await startAndPoll();
    } catch (e) { showFail("投喂失败：" + e.message); }
  }
  async function feedFiles(files) {
    if (!files.length) return;
    setState("feeding"); stageEl.textContent = "投喂中…"; detailEl.textContent = files.length + " 个文件"; setPanel("status");
    try {
      for (const f of files) {
        const fd = new FormData(); fd.append("file", f);
        const r = await fetch("/api/inbox/upload", { method: "POST", body: fd });
        if (!r.ok) { let t = ""; try { t = (await r.json()).detail || ""; } catch {} throw new Error("upload " + r.status + " " + t); }
      }
      await startAndPoll();
    } catch (e) { showFail("上传失败：" + e.message); }
  }

  async function feedImage() {
    setState("feeding"); stageEl.textContent = "投喂中…"; detailEl.textContent = "剪贴板图片"; setPanel("status");
    try {
      const t = getT();
      if (!t || !t.core) throw new Error("仅桌面端支持");
      const buf = await t.core.invoke("take_clip_image");   // ArrayBuffer
      const blob = new Blob([buf], { type: "image/png" });
      const fd = new FormData();
      fd.append("file", blob, "剪贴板图片-" + Date.now() + ".png");
      const r = await fetch("/api/inbox/upload", { method: "POST", body: fd });
      if (!r.ok) { let d = ""; try { d = (await r.json()).detail || ""; } catch {} throw new Error("upload " + r.status + " " + d); }
      await startAndPoll();
    } catch (e) { showFail("图片投喂失败：" + e.message); }
  }

  async function startAndPoll() {
    const r = await fetch("/api/jobs/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: "inbox", params: {} }),
    });
    const j = await r.json();
    setState("digesting"); stageEl.textContent = "消化中…"; detailEl.textContent = ""; setPanel("status");
    poll(j.job_id);
  }

  async function poll(jobId) {
    let res;
    try { res = await fetch("/api/jobs/" + jobId); }
    catch { return void setTimeout(() => poll(jobId), 2000); }
    if (!res.ok) {
      if (res.status === 404) return showFail("任务已丢失，可在主界面重试");
      return void setTimeout(() => poll(jobId), 2000);
    }
    const job = await res.json();
    const prog = job.progress || [];
    // 单例任务的 progress 可能跨多次运行；只取「最后一次 start 之后」的日志行
    let startIdx = 0;
    for (let i = 0; i < prog.length; i++) if (prog[i].event === "start") startIdx = i;
    const lines = prog.slice(startIdx).filter((e) => e.event === "log").map((e) => (e.data && e.data.line) || "");
    updateFromLines(lines);
    if (job.status === "running") setTimeout(() => poll(jobId), 1500);
    else finalize(job, lines);
  }

  function updateFromLines(lines) {
    let stg = null, det = null;
    for (const ln of lines) {
      if (ln.includes("→ [")) { stg = "抓取中…"; det = ln.replace(/^.*?→\s*\[[^\]]*\]\s*/, "").trim(); }
      else if (ln.includes("✓ 已抓取")) { stg = "消化中…"; }
      else if (ln.includes("✓ 已归位")) { stg = "归位…"; det = ln.split("→").pop().trim(); }
      else if (ln.includes("条关联边")) { stg = "生长…"; }
    }
    if (stg) stageEl.textContent = stg;
    if (det != null) detailEl.textContent = det;
  }

  const prettyTitle = (p) => p.split("/").pop().replace(/\.md$/, "").replace(/^\d{4}-\d{2}-\d{2}\s+/, "");
  function dirHue(dir) {
    if (dir.includes("领域")) return "#7c3aed";
    if (dir.includes("项目")) return "#ff7a17";
    if (dir.includes("笔记")) return "#ffc285";
    return "#a0c3ec";
  }

  function finalize(job, lines) {
    let processed = 0, failed = 0, skipped = 0, placed = null, edges = 0, errMsg = null, m;
    for (const ln of lines) {
      if ((m = ln.match(/processed=(\d+),\s*failed=(\d+),\s*skipped=(\d+)/))) { processed = +m[1]; failed = +m[2]; skipped = +m[3]; }
      if (ln.includes("✓ 已归位")) placed = ln.split("→").pop().trim();
      if ((m = ln.match(/(\d+)\s*条关联边/))) edges = +m[1];
      if (ln.includes("❌")) errMsg = ln.replace(/^.*?❌\s*/, "").trim();
    }
    if (job.status !== "done" && !lines.length) return showFail("任务中断，可在主界面重试");

    if (processed >= 1) {
      const dir = placed ? placed.split("/")[0] : "";
      const title = placed ? prettyTitle(placed) : "";
      spawnSat(dirHue(dir)); setState("done");
      resultEl.innerHTML =
        `<div class="grow">消化完成 · +1 笔记</div>` +
        `<div class="where">→ ${esc(dir || "已归位")}${title ? " · " + esc(title) : ""}</div>` +
        (edges ? `<div class="ok">图谱 +${edges} 条关联边</div>` : "");
      setPanel("result");
      // 通知主界面：刷新文件树 + 轻提示（path 为 vault 相对路径，主界面可直接打开）
      const t = getT();
      if (t && t.event && t.event.emit) t.event.emit("knowledgex-digested", { path: placed || "", title, dir, edges });
      setTimeout(() => {
        if (state === "done") {
          releasePetPrompt();
          backToIdle();
          if (AUTO_HIDE_ON_IDLE) hidePetWindow();
        }
      }, 6000);
    } else if (failed >= 1 || errMsg) {
      showFail(errMsg || "抓取失败");
    } else if (skipped >= 1) {
      flashHint("已处理过，跳过");
    } else {
      flashHint("没有新内容");
    }
  }

  function showFail(msg) {
    setState("fail");
    resultEl.innerHTML =
      `<div class="bad">消化不良 🤢</div>` +
      `<div class="where">${esc(msg)}</div>` +
      `<div class="where">已留在收件箱，可重试</div>`;
    setPanel("result");
    setTimeout(() => {
      if (state === "fail") {
        releasePetPrompt();
        backToIdle();
        if (AUTO_HIDE_ON_IDLE) hidePetWindow();
      }
    }, 7000);
  }

  // ---------- 拖放投喂（链接稳；原生文件在 macOS WKWebView 下可能拿不到字节，用「＋ 文件」兜底） ----------
  let dragTimer = 0;
  window.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropEl.classList.remove("hidden");
    clearTimeout(dragTimer); dragTimer = setTimeout(() => dropEl.classList.add("hidden"), 160);
  });
  window.addEventListener("drop", (e) => {
    e.preventDefault(); clearTimeout(dragTimer); dropEl.classList.add("hidden");
    const dt = e.dataTransfer; if (!dt) return;
    if (dt.files && dt.files.length) return void feedFiles([...dt.files]);
    const uri = (dt.getData("text/uri-list") || dt.getData("text/plain") || "").trim();
    if (uri) return void feedText(uri);
    if (dt.items && [...dt.items].some((it) => it.kind === "file")) flashHint("拖文件不稳，请点「＋ 文件」选择");
  });

  // 文件选择兜底：hint 里的「＋ 文件」（事件委托，扛得住 hint innerHTML 重置）
  const fileInput = $("file-input");
  panels.hint.addEventListener("click", (e) => { if (e.target && e.target.id === "pick") fileInput.click(); });
  fileInput.addEventListener("change", () => {
    if (fileInput.files && fileInput.files.length) { feedFiles([...fileInput.files]); fileInput.value = ""; }
  });

  // ---------- 快捷键捕获事件（来自 Rust，惰性等 __TAURI__ 就绪） ----------
  whenTauri((t) => {
    if (t.event && t.event.listen) {
      t.event.listen("capture-propose", (ev) => {
        const p = ev.payload || {};
        if (p.kind === "empty") return flashHint("剪贴板是空的");
        showCard(p);
      });
    }
    // 恢复「复制自动监听」开关并同步给 Rust；未设置时默认开启。
    let saved = true;
    try {
      const raw = localStorage.getItem("kx_clip_watch");
      saved = raw == null ? true : raw === "1";
    } catch {}
    applyWatch(saved, false);
  });

  backToIdle();
})();
