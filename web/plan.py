"""方案规划：HTML 结构化输出。

输入需求 → 结合知识库检索/问答上下文 → LLM 流式输出完整 HTML 方案/PRD
→ iframe 预览 → 保存到 04-项目/ → 后续 AI 工具按方案 coding 出 Demo/MVP。

参考 Thariq Shihipar 的 HTML 论证：tab 导航、折叠区、对比表格、SVG 架构图、
交互控件——单文件自包含、浏览器直接打开、信息密度远超 MD。
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Generator, Optional

from web import chat
from web.config import PIPELINE_CONFIG, VAULT_ROOT, llm_extra_body
from web.rag import retriever
from scripts.persona import render_persona


_PLAN_SYSTEM = """你是一位资深解决方案架构师。用户描述了一个项目需求，你要据知识库中的相关技术洞察，\
输出一份**完整的 HTML 格式项目方案文档**。

输出必须为**合法的完整 HTML 文件**（从 `<!DOCTYPE html>` 开始），不要 markdown 代码块包裹。

## HTML 必须包含的章节（用 tab 导航 + 折叠 details/summary）

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>方案标题</title>
<style>
  /* 所有 CSS 必须 inline */
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         line-height: 1.6; color: #1a1a2e; background: #f8f9fa; padding: 24px; max-width: 960px; margin: 0 auto; }
  h1 { font-size: 1.8em; margin-bottom: 0.3em; }
  h2 { font-size: 1.3em; margin: 1.2em 0 0.5em; padding-bottom: 0.3em; border-bottom: 2px solid #e0e0e0; }
  nav.plan-tabs { display: flex; flex-wrap: wrap; gap: 4px; margin: 20px 0; border-bottom: 2px solid #ddd; }
  nav.plan-tabs button { padding: 8px 16px; border: none; background: none; cursor: pointer;
      font-size: 13px; color: #666; border-bottom: 2px solid transparent; margin-bottom: -2px; }
  nav.plan-tabs button:hover { color: #333; }
  nav.plan-tabs button.active { color: #e65100; border-bottom-color: #e65100; font-weight: 600; }
  .tab-content { display: none; }
  .tab-content.active { display: block; }
  table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 13px; }
  th, td { border: 1px solid #e0e0e0; padding: 8px 12px; text-align: left; }
  th { background: #f5f5f5; font-weight: 600; }
  details { margin: 8px 0; border: 1px solid #e0e0e0; border-radius: 6px; padding: 10px 14px; }
  summary { cursor: pointer; font-weight: 600; font-size: 14px; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px;
           font-weight: 600; }
  .badge-p0 { background: #ffebee; color: #c62828; }
  .badge-p1 { background: #fff3e0; color: #e65100; }
  .badge-p2 { background: #e8f5e9; color: #2e7d32; }
  .metric { display: inline-block; text-align: center; margin: 8px 16px 8px 0; }
  .metric .num { font-size: 24px; font-weight: 700; color: #e65100; }
  .metric .label { font-size: 11px; color: #999; }
  @media (prefers-color-scheme: dark) {
    body { color: #e0e0e0; background: #1a1a2e; }
    h2 { border-bottom-color: #333; }
    table, th, td { border-color: #333; }
    th { background: #252540; }
    details { border-color: #333; }
    .badge-p0 { background: #3e1a1a; }
    .badge-p1 { background: #3e2a10; }
    .badge-p2 { background: #1a3e1a; }
    nav.plan-tabs { border-bottom-color: #333; }
    nav.plan-tabs button { color: #999; }
    nav.plan-tabs button:hover { color: #ccc; }
  }
</style>
</head>
<body>
<h1>方案标题</h1>
<p class="meta">生成日期 · 基于知识库 X 篇参考</p>

<nav class="plan-tabs">
  <button class="active" onclick="switchTab('overview')">项目概述</button>
  <button onclick="switchTab('background')">背景与需求</button>
  <button onclick="switchTab('compare')">方案对比</button>
  <button onclick="switchTab('recommend')">推荐方案</button>
  <button onclick="switchTab('plan')">实施计划</button>
  <button onclick="switchTab('risk')">风险与应对</button>
  <button onclick="switchTab('refs')">知识库参考</button>
</nav>

<div id="tab-overview" class="tab-content active">...</div>
<div id="tab-background" class="tab-content">...</div>
<div id="tab-compare" class="tab-content"><table>...</table></div>
<div id="tab-recommend" class="tab-content">...</div>
<div id="tab-plan" class="tab-content"><details><summary>Phase 1: MVP (2周)</summary>...</details></div>
<div id="tab-risk" class="tab-content">...</div>
<div id="tab-refs" class="tab-content"><ul><li>《笔记标题》- 关键洞察</li></ul></div>

<script>
function switchTab(name) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('nav.plan-tabs button').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  event.target.classList.add('active');
}
</script>
</body>
</html>
```

## 内容要求

- **项目概述**：一句话定位 + 核心价值主张 + 关键指标（预估周期/难度/收益）
- **背景与需求**：用户原始需求 + 痛点分析 + 知识库相关洞察
- **方案对比**：至少 2 个备选方案，用表格对比（方案/优势/劣势/成本/适用场景）
- **推荐方案**：选其一详述架构、技术选型、关键模块、与知识库已有方案的差异化
- **实施计划**：分 Phase，每个 Phase 用 `<details>` 折叠，含交付物清单
- **风险与应对**：表格列风险/概率/影响/缓解措施
- **知识库参考**：每篇引用简述"从中获得了什么洞察"

## 约束

- 全部用中文
- CSS inline，JS 轻量（仅 tab 切换，10行内）
- 信息密度高、不要套话
- 表格/列表要有实际内容，不要占位符
- 可直接在浏览器打开，不依赖任何外部资源
- 严格按上述 HTML 骨架输出"""


def _build_plan_user(
    requirements: str,
    knowledge_context: Optional[str] = None,
    history: Optional[list[dict]] = None,
) -> str:
    parts = [f"## 用户需求\n{requirements}"]

    if knowledge_context:
        parts.append(
            "## 知识库上下文（来自此前的全库问答，含已检索到的笔记正文和 AI 分析）\n\n"
            "请先通读下面的问答和笔记正文，提取与用户需求相关的技术洞察、方案参考、选型依据，"
            "然后在方案的「背景与需求」「方案对比」「知识库参考」等章节中**具体引用**这些洞察。\n\n"
            + knowledge_context
        )
    else:
        # 独立 Tab：做一次快速 RAG 检索
        try:
            hits = retriever.search(requirements, top_k=5)
            if hits:
                lines = []
                for h in hits:
                    lines.append(f"### {h.note_title}\n{h.text[:600]}")
                parts.append("## 知识库检索到的相关内容\n" + "\n\n".join(lines))
        except Exception:
            pass

    if history:
        history_lines = []
        for h in history[-6:]:
            role = h.get("role")
            content = (h.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                history_lines.append(f"**{role}**: {content[:400]}")
        if history_lines:
            parts.append("## 近期对话上下文\n" + "\n\n".join(history_lines))

    return "\n\n".join(parts)


def stream_plan(
    requirements: str,
    history: Optional[list[dict]] = None,
    knowledge_context: Optional[str] = None,
    prev_html: Optional[str] = None,
) -> Generator[str, None, None]:
    """流式生成 HTML 方案文档，SSE 事件。

    prev_html 非空时为**迭代修改**：在已有方案 HTML 基础上按用户要求改，输出完整新 HTML。
    事件：start → delta(HTML 逐段) → plan_meta({title, summary}) → end
    """
    yield chat._sse("start", {"mode": "plan"})

    # 注入画像
    persona_text = render_persona()

    if prev_html:
        # 迭代修改：基于上一版方案改全量
        system_prompt = (
            persona_text
            + "\n\n你是上面这位用户的方案架构师。用户已有一份 HTML 方案，现在要在其基础上**迭代修改**。\n"
            "保持原方案的 HTML 骨架、风格与无关部分不变，只按用户的修改要求调整相应内容；"
            "输出**修改后的完整 HTML 文件**（从 `<!DOCTYPE html>` 开始，不要 markdown 代码块包裹）。\n\n"
            + _PLAN_SYSTEM
        )
        user_content = f"## 当前方案 HTML（在此基础上修改）\n{prev_html}\n\n## 用户的修改要求\n{requirements}"
        if knowledge_context:
            user_content += "\n\n## 可参考的知识库上下文\n" + knowledge_context
    else:
        system_prompt = (
            persona_text
            + "\n\n你是上面这位用户的方案架构师。"
            "下面会给你「用户需求」和「知识库上下文」（可能来自全库问答的检索结果，含 AI 分析和笔记正文）。\n\n"
            "工作方式：\n"
            "1. 先从知识库上下文中提取与需求相关的技术洞察、参考方案、选型依据\n"
            "2. 在方案的「背景与需求」中引用知识库里的技术趋势/痛点数据\n"
            "3. 在「方案对比」中纳入知识库里已有的同类方案作为比较基准\n"
            "4. 在「知识库参考」章节中**具体**列出每篇参考笔记及从中获得的洞察（不要泛泛而谈）\n"
            "5. 如果知识库上下文里没有相关信息，诚实说明\"知识库里关于这个领域的内容不足\"，但仍基于你的理解给出方案\n\n"
            + _PLAN_SYSTEM
        )
        user_content = _build_plan_user(requirements, knowledge_context, history)

    client, llm_cfg = chat._llm_client()
    try:
        stream = client.chat.completions.create(
            model=llm_cfg["model"],
            extra_body=llm_extra_body(llm_cfg),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.4,
            max_tokens=llm_cfg.get("max_tokens", 8000),
            stream=True,
        )
    except Exception as e:
        yield chat._sse("error", {"message": f"方案生成失败: {e}"})
        yield chat._sse("end", {})
        return

    buf = ""
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            buf += delta.content
            yield chat._sse("delta", {"text": delta.content})

    # 提取 title（从 HTML <title> 或 <h1>）
    title = "项目方案"
    summary = ""
    m_title = re.search(r"<h1[^>]*>(.*?)</h1>", buf)
    if m_title:
        title = m_title.group(1).strip()
    m_desc = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', buf)
    if m_desc:
        summary = m_desc.group(1)
    if not summary:
        # 取第一个 <p> 的前 120 字
        m_p = re.search(r"<p[^>]*>(.*?)</p>", buf, re.DOTALL)
        if m_p:
            summary = re.sub(r"<[^>]+>", "", m_p.group(1)).strip()[:120]

    yield chat._sse("plan_meta", {"title": title, "summary": summary})
    yield chat._sse("end", {"finish_reason": "stop", "chars": len(buf)})


def save_plan(html: str, title: str, summary: str) -> dict:
    """保存 HTML 方案到 04-项目/，返回 {path, title}。"""
    target_dir = VAULT_ROOT / "04-项目"
    target_dir.mkdir(parents=True, exist_ok=True)

    safe_title = re.sub(r'[\\/:*?"<>|]', "", title).strip()[:60] or "未命名方案"
    today = date.today().isoformat()
    filename = f"{today} {safe_title}.html"
    target = target_dir / filename

    n = 2
    while target.exists():
        target = target_dir / f"{today} {safe_title}-{n}.html"
        n += 1

    # 确保 HTML 自包含（如果 LLM 输出有缺失，补最小骨架）
    if "<!DOCTYPE html>" not in html[:200]:
        html = f"<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head><meta charset=\"UTF-8\"><title>{title}</title></head>\n<body>\n{html}\n</body>\n</html>"

    target.write_text(html, encoding="utf-8")
    rel_path = str(target.relative_to(VAULT_ROOT))

    return {"path": rel_path, "title": title, "summary": summary}
