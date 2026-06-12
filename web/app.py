"""FastAPI 主入口"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request

from web import chat, files, insights
from web.auth import TokenAuthMiddleware
from web.config import HOST, PORT, WEB_TOKEN, VAULT_ROOT
from web.rag import index as rag_index


app = FastAPI(title="KnowledgeX Web 应用", docs_url=None, redoc_url=None)

# 鉴权中间件
app.add_middleware(TokenAuthMiddleware)

# 静态资源 + 模板
WEB_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

# 启动恢复：把上次残留的 running 任务标记为 interrupted（线程已随重启消失）
from web import jobs as _jobs  # noqa: E402
_jobs.init_jobs()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
def login(request: Request):
    return templates.TemplateResponse(request, "login.html")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request, "index.html", {"vault_root": str(VAULT_ROOT)}
    )


@app.get("/api/files")
def api_files() -> JSONResponse:
    return JSONResponse({"tree": files.list_tree()})


@app.get("/api/note")
def api_note(path: str = Query(..., description="vault 内相对路径")) -> JSONResponse:
    try:
        return JSONResponse(files.read_note(path))
    except FileNotFoundError:
        raise HTTPException(404, f"笔记不存在: {path}")
    except ValueError as e:
        raise HTTPException(400, str(e))


class ChatRequest(BaseModel):
    question: str
    note_path: str
    mode: str = "auto"  # auto / note / deepwiki


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    """SSE 流式问答。前端用 EventSource 或 fetch + ReadableStream 消费。"""
    if req.mode not in {"auto", "note", "deepwiki"}:
        raise HTTPException(400, f"无效 mode: {req.mode}")
    if not req.question.strip():
        raise HTTPException(400, "问题不能为空")

    def gen():
        for event in chat.stream_chat(req.question, req.note_path, req.mode):
            yield event

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class InsightAppendRequest(BaseModel):
    note_path: str
    question: str
    answer: str


@app.post("/api/insight/append")
def api_insight_append(req: InsightAppendRequest) -> JSONResponse:
    try:
        return JSONResponse(insights.append_to_note(req.note_path, req.question, req.answer))
    except FileNotFoundError:
        raise HTTPException(404, f"笔记不存在: {req.note_path}")


class FlashRequest(BaseModel):
    question: str
    answer: str
    mode: str = "note"               # note（当前笔记问答）| vault（全库问答）
    note_path: str | None = None     # note 模式：当前笔记
    sources: list[str] = []          # vault 模式：本次引用的来源笔记路径


@app.post("/api/insight/flash")
def api_insight_flash(req: FlashRequest) -> JSONResponse:
    try:
        if req.mode == "note":
            result = insights.create_flash_note(req.question, req.answer, note_path=req.note_path)
        else:
            result = insights.create_flash_note(req.question, req.answer, sources=req.sources)
        return JSONResponse({"created": result})
    except FileNotFoundError:
        raise HTTPException(404, "关联笔记不存在")


# ---------------- 全库 RAG 问答 ----------------

class RagChatRequest(BaseModel):
    question: str
    top_k: int = 8
    history: list[dict] = []


@app.post("/api/rag/chat")
def api_rag_chat(req: RagChatRequest):
    if not req.question.strip():
        raise HTTPException(400, "问题不能为空")

    def gen():
        for event in chat.stream_rag_chat(req.question, top_k=req.top_k, history=req.history):
            yield event

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/rag/status")
def api_rag_status() -> JSONResponse:
    from web.rag import graph
    vecs, chunks = rag_index.load_index()
    return JSONResponse(
        {
            "indexed": rag_index.index_exists(),
            "chunks": len(chunks),
            "notes": len({c["note_path"] for c in chunks}),
            "graph": graph.stats(),
        }
    )


@app.post("/api/rag/reindex")
def api_rag_reindex(force: bool = False) -> JSONResponse:
    """重建/增量更新索引。force=true 全量重建。"""
    logs: list[str] = []
    stats = rag_index.build_index(force=force, progress=lambda m: logs.append(m))
    return JSONResponse({"stats": stats, "logs": logs})


class BuildLinksRequest(BaseModel):
    top_k: int = 5
    threshold: float = 0.45
    with_reasons: bool = False  # 默认不调 LLM（秒级）；勾选则带关联理由（慢）


@app.post("/api/links/build")
def api_build_links(req: BuildLinksRequest) -> JSONResponse:
    """给所有已索引笔记自动建双链。with_reasons=true 会调 LLM 生成理由（1-2分钟）。"""
    from web.rag import linker
    logs: list[str] = []
    stats = linker.link_all(
        top_k=req.top_k,
        threshold=req.threshold,
        with_reasons=req.with_reasons,
        progress=lambda m: logs.append(m),
    )
    return JSONResponse({"stats": stats, "logs": logs})


class UnlinkRequest(BaseModel):
    path: str


@app.post("/api/links/unlink")
def api_unlink(req: UnlinkRequest) -> JSONResponse:
    """以某篇笔记为中心，双向彻底清除其所有双链。"""
    from web.rag import graph
    if not req.path.strip():
        raise HTTPException(400, "path 不能为空")
    return JSONResponse(graph.unlink_note(req.path))


@app.get("/api/graph")
def api_graph(scope: str = Query("connected", description="connected | all")) -> JSONResponse:
    """导出双链图供前端 D3 可视化。"""
    from web.rag import graph
    if scope not in ("connected", "all"):
        raise HTTPException(400, "scope 必须是 connected 或 all")
    return JSONResponse(graph.export_graph(scope))


# ---------------- 用户画像（persona）配置 ----------------

class ProfileSaveRequest(BaseModel):
    persona: dict


class ProfileDraftRequest(BaseModel):
    answers: dict   # 引导各维度的原始回答（可口语/零散）


@app.get("/api/profile")
def api_profile_get() -> JSONResponse:
    """读取当前用户画像。"""
    from scripts import persona
    return JSONResponse(persona.load_profile())


@app.get("/api/profile/status")
def api_profile_status() -> JSONResponse:
    """是否已构建专属画像。前端首启据此决定是否弹出引导。"""
    from scripts import persona
    return JSONResponse({"configured": persona.is_configured()})


@app.post("/api/profile")
def api_profile_save(req: ProfileSaveRequest) -> JSONResponse:
    """保存用户画像（写回 profile.yaml，问答/消化下次调用即生效）。"""
    from scripts import persona
    if not isinstance(req.persona, dict) or not req.persona:
        raise HTTPException(400, "persona 不能为空")
    return JSONResponse(persona.save_profile(req.persona))


@app.post("/api/profile/draft")
def api_profile_draft(req: ProfileDraftRequest):
    """AI 引导式提炼：把各维度零散回答提炼成规范 persona JSON（SSE 流式）。"""
    def gen():
        for event in chat.stream_profile_draft(req.answers):
            yield event

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------- 目录体系（taxonomy）配置 ----------------

class TaxonomySaveRequest(BaseModel):
    taxonomy: dict


@app.get("/api/taxonomy")
def api_taxonomy_get() -> JSONResponse:
    """读取当前目录体系（归位的单一来源）。"""
    from scripts import persona
    return JSONResponse(persona.load_taxonomy())


@app.post("/api/taxonomy")
def api_taxonomy_save(req: TaxonomySaveRequest) -> JSONResponse:
    """保存目录体系（写回 profile.yaml，归位/消化下次调用即生效）。返回规范化结果。"""
    from scripts import persona
    if not isinstance(req.taxonomy, dict) or not req.taxonomy.get("dirs"):
        raise HTTPException(400, "taxonomy.dirs 不能为空")
    return JSONResponse(persona.save_taxonomy(req.taxonomy))


class TaxonomySuggestRequest(BaseModel):
    persona: dict | None = None   # 可选：未保存的画像草稿覆盖；为空则读 profile.yaml


@app.post("/api/taxonomy/suggest")
def api_taxonomy_suggest(req: TaxonomySuggestRequest):
    """AI 据画像+现有目录+笔记分布推荐优化后的目录体系（SSE 流式）。"""
    def gen():
        for event in chat.stream_taxonomy_suggest(req.persona):
            yield event

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------- 单篇笔记重新归类 ----------------

class ReclassifyRequest(BaseModel):
    note_path: str


@app.post("/api/note/reclassify")
def api_note_reclassify(req: ReclassifyRequest) -> JSONResponse:
    """LLM 据 taxonomy 给单篇笔记建议目标目录 + 理由（非流式）。"""
    if not req.note_path.strip():
        raise HTTPException(400, "note_path 不能为空")
    try:
        note = files.read_note(req.note_path)
    except FileNotFoundError:
        raise HTTPException(404, f"笔记不存在: {req.note_path}")
    except ValueError as e:
        raise HTTPException(400, str(e))

    from pathlib import PurePosixPath
    suggestion = chat.suggest_placement(note)
    current_dir = str(PurePosixPath(note["path"]).parent)
    return JSONResponse({
        "note_path": note["path"],
        "current_dir": current_dir,
        "target_dir": suggestion["target_dir"],
        "reason": suggestion["reason"],
    })


class MoveNoteRequest(BaseModel):
    note_path: str
    target_dir: str


@app.post("/api/note/move")
def api_note_move(req: MoveNoteRequest) -> JSONResponse:
    """移动笔记到目标目录：move_note → 同步边图路径键 → 增量重索引。返回新路径。"""
    from web.rag import graph
    if not req.note_path.strip() or not req.target_dir.strip():
        raise HTTPException(400, "note_path / target_dir 不能为空")
    try:
        new_path = files.move_note(req.note_path, req.target_dir)
    except FileNotFoundError:
        raise HTTPException(404, f"笔记不存在: {req.note_path}")
    except ValueError as e:
        raise HTTPException(400, str(e))

    edges_updated = 0
    if new_path != req.note_path:
        edges_updated = graph.rename_note_path(req.note_path, new_path)
        # 增量重索引：旧路径当删除、新路径当新增，自动同步 RAG 索引
        try:
            rag_index.build_index(force=False)
        except Exception:
            pass  # 索引同步失败不阻塞移动结果

    return JSONResponse({
        "old_path": req.note_path,
        "new_path": new_path,
        "moved": new_path != req.note_path,
        "edges_updated": edges_updated,
    })


# ---------------- 知识缺口补全 ----------------

class GapSuggestRequest(BaseModel):
    question: str
    recalled_titles: list[str] = []


@app.post("/api/gap/suggest")
def api_gap_suggest(req: GapSuggestRequest) -> JSONResponse:
    """据问答缺口 AI 推荐 GitHub 仓库并 GitHub API 校验，返回 verified 候选（非流式）。"""
    from web import gapfill
    if not req.question.strip():
        raise HTTPException(400, "question 不能为空")
    return JSONResponse({"candidates": gapfill.suggest_gaps(req.question, req.recalled_titles)})


class GapFillRequest(BaseModel):
    repos: list[dict]
    question: str = ""   # 触发缺口补全的问答，用于命名收件箱文件


@app.post("/api/gap/fill")
def api_gap_fill(req: GapFillRequest) -> JSONResponse:
    """把选中仓库的链接写进收件箱，之后「处理收件箱」异步消化（避免同步阻塞）。"""
    from web import gapfill
    return JSONResponse(gapfill.collect_to_inbox(req.repos, req.question))


# ---------------- 方案规划（HTML 结构化输出） ----------------

class PlanRequest(BaseModel):
    requirements: str
    history: list[dict] = []
    knowledge_context: str | None = None   # 从问答触发时带入的上下文


@app.post("/api/plan/generate")
def api_plan_generate(req: PlanRequest):
    """流式生成 HTML 方案文档（SSE）。"""
    from web import plan
    if not req.requirements.strip():
        raise HTTPException(400, "需求描述不能为空")

    def gen():
        for event in plan.stream_plan(req.requirements, req.history, req.knowledge_context):
            yield event

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class PlanSaveRequest(BaseModel):
    html: str
    title: str
    summary: str = ""


@app.post("/api/plan/save")
def api_plan_save(req: PlanSaveRequest) -> JSONResponse:
    """保存 HTML 方案到 04-项目/ 目录。"""
    from web import plan
    if not req.html.strip() or not req.title.strip():
        raise HTTPException(400, "html / title 不能为空")
    return JSONResponse(plan.save_plan(req.html, req.title, req.summary))


# ---------------- GitHub Stars 导入 ----------------

class StarsImportRequest(BaseModel):
    username: str
    mode: str = "latest"   # latest | full
    limit: int = 30
    token: str = ""        # 可选：前端填的 GITHUB_TOKEN
    save_token: bool = False


@app.get("/api/stars/token-status")
def api_stars_token_status() -> JSONResponse:
    """前端用来判断是否已配 GITHUB_TOKEN（已配则隐藏输入框）。不返回 token 明文。"""
    import os
    return JSONResponse({"has_token": bool(os.environ.get("GITHUB_TOKEN"))})


@app.post("/api/stars/import")
def api_stars_import(req: StarsImportRequest):
    """流式导入 GitHub starred 仓库（fetcher→digest→place），SSE 进度。"""
    from web import stars
    if not req.username.strip():
        raise HTTPException(400, "username 不能为空")
    if req.mode not in ("latest", "full"):
        raise HTTPException(400, "mode 必须是 latest 或 full")

    def gen():
        for event in stars.stream_import_stars(
            req.username, req.mode, req.limit, token=req.token, save_token=req.save_token
        ):
            yield event

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------- 异步任务系统（后台线程 + 持久化 + 轮询） ----------------

class JobStartRequest(BaseModel):
    type: str               # inbox | stars
    params: dict = {}


@app.post("/api/jobs/start")
def api_jobs_start(req: JobStartRequest) -> JSONResponse:
    """启动后台任务。同类已有运行中的则复用其 id（单例）。"""
    from web import jobs, inbox, stars
    import os

    if req.type == "inbox":
        factory = lambda: inbox.stream_process()  # noqa: E731
        title = "处理收件箱"
    elif req.type == "stars":
        p = req.params or {}
        username = str(p.get("username") or "").strip()
        if not username:
            raise HTTPException(400, "stars 任务缺少 username")
        # token 在主线程设进环境变量，后台线程继承
        token = str(p.get("token") or "").strip()
        if token:
            os.environ["GITHUB_TOKEN"] = token
            if p.get("save_token"):
                stars._persist_token(token)
        mode = p.get("mode", "latest")
        limit = int(p.get("limit", 30))
        factory = lambda: stars.stream_import_stars(username, mode, limit)  # noqa: E731
        title = f"导入 Stars · {username}"
    else:
        raise HTTPException(400, f"未知任务类型: {req.type}")

    return JSONResponse(jobs.start_job(req.type, title, factory))


@app.get("/api/jobs/active")
def api_jobs_active() -> JSONResponse:
    """列出所有运行中的任务（前端轮询恢复用）。"""
    from web import jobs
    return JSONResponse({"jobs": jobs.list_active()})


@app.get("/api/jobs/{job_id}")
def api_jobs_get(job_id: str) -> JSONResponse:
    """查询任务进度/状态。"""
    from web import jobs
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return JSONResponse(job)


# ---------------- 收件箱处理 ----------------

@app.post("/api/inbox/process")
def api_inbox_process():
    """在 Web 端触发收件箱处理管道，SSE 流式返回日志。"""
    from web import inbox

    def gen():
        for event in inbox.stream_process():
            yield event

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class InboxAddRequest(BaseModel):
    text: str


@app.post("/api/inbox/add")
def api_inbox_add(req: InboxAddRequest) -> JSONResponse:
    """App 内录入：把链接/文字写进当日收件箱文件（取代在 Obsidian 里手动编辑）。"""
    from web import inbox
    try:
        return JSONResponse(inbox.add_to_inbox(req.text))
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------- 笔记轻量编辑 ----------------

class NoteSaveRequest(BaseModel):
    path: str
    body: str


@app.post("/api/note/save")
def api_note_save(req: NoteSaveRequest) -> JSONResponse:
    """覆盖写笔记正文（保留 frontmatter）。app 内轻量编辑用。"""
    if not req.path.strip():
        raise HTTPException(400, "path 不能为空")
    try:
        return JSONResponse(files.save_note(req.path, req.body))
    except FileNotFoundError:
        raise HTTPException(404, f"笔记不存在: {req.path}")
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------- 对话记录持久化 ----------------

class ConvSaveRequest(BaseModel):
    kind: str                        # note | vault
    note_path: str | None = None
    messages: list[dict] = []


class ConvKeyRequest(BaseModel):
    kind: str
    note_path: str | None = None


@app.get("/api/conversation")
def api_conversation_get(
    kind: str = Query(..., description="note | vault"),
    note_path: str | None = Query(None),
) -> JSONResponse:
    """读取某上下文的滚动会话（缺失返回空）。"""
    from web import conversations
    try:
        return JSONResponse(conversations.load_conversation(kind, note_path))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/conversation/save")
def api_conversation_save(req: ConvSaveRequest) -> JSONResponse:
    """整条会话覆盖写。"""
    from web import conversations
    try:
        return JSONResponse(conversations.save_conversation(req.kind, req.note_path, req.messages))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/conversation/clear")
def api_conversation_clear(req: ConvKeyRequest) -> JSONResponse:
    """清空对话：删除磁盘文件。"""
    from web import conversations
    try:
        return JSONResponse(conversations.delete_conversation(req.kind, req.note_path))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/conversation/export")
def api_conversation_export(req: ConvSaveRequest) -> JSONResponse:
    """把对话导出成 01-笔记/对话/ 下的 markdown 笔记。"""
    try:
        created = insights.export_to_note(req.kind, req.note_path, req.messages)
        return JSONResponse({"created": created})
    except ValueError as e:
        raise HTTPException(400, str(e))


def main():
    import uvicorn
    # 启动时增量更新 RAG 索引（只处理新增/变化的笔记，通常很快）
    try:
        print("  检查 RAG 索引（增量）...")
        stats = rag_index.build_index(force=False, progress=lambda m: print("   ", m))
        print(f"  索引就绪：{stats['notes_total']} 篇 / {stats['chunks_total']} 块")
    except Exception as e:
        print(f"  ⚠️ 索引更新失败（不影响浏览/单笔记问答）：{e}")

    print()
    print("=" * 70)
    print(f"  KnowledgeX Web 启动中...")
    print(f"  访问 URL（含 token，首次进入用这个）：")
    print(f"    本机:    http://localhost:{PORT}/?token={WEB_TOKEN}")
    # 试图打印局域网 IP
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
        print(f"    局域网:  http://{lan_ip}:{PORT}/?token={WEB_TOKEN}")
    except Exception:
        pass
    print("=" * 70)
    print()
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
