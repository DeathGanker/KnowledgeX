"""通用异步任务系统：后台线程消费 SSE 生成器，进度持久化，前端轮询。

长任务（处理收件箱、导入 Stars）改为后台线程异步跑，不再依赖前端 SSE 连接存活：
- 关 modal / 切页面 / 刷新浏览器，任务继续；回来轮询接上进度
- 进度持久化到 jobs.json，服务重启后能展示历史（running 标记为 interrupted，可重跑）
- 复用现有 SSE 生成器（inbox.stream_process / stars.stream_import_stars），不改管道

线程安全：所有对 _JOBS 的读写都在 _LOCK 内；persist 用临时文件原子替换。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Callable, Generator, Optional

from web.config import PIPELINE_DIR


JOBS_FILE = PIPELINE_DIR / "rag_index" / "jobs.json"
_MAX_PROGRESS = 500          # 单任务 progress 上限，防膨胀
_MAX_RECENT = 20             # 持久化保留最近 N 个任务

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()
_LOADED = False


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _persist_locked() -> None:
    """已持有 _LOCK 时调用：把最近 N 个任务写盘（原子替换）。"""
    try:
        JOBS_FILE.parent.mkdir(exist_ok=True)
        # 按 started_at 取最近 N 个
        items = sorted(_JOBS.values(), key=lambda j: j.get("started_at", ""), reverse=True)[:_MAX_RECENT]
        tmp = JOBS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"jobs": items}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(JOBS_FILE)
    except Exception:
        pass  # 持久化失败不影响任务本身


def init_jobs() -> None:
    """app 启动时调用：load jobs.json，把残留 running 改成 interrupted（线程已死）。"""
    global _LOADED
    with _LOCK:
        if _LOADED:
            return
        _LOADED = True
        try:
            if JOBS_FILE.exists():
                data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
                for j in data.get("jobs", []):
                    if j.get("status") == "running":
                        j["status"] = "interrupted"
                        j["error"] = "服务重启导致任务中断，可重新运行"
                    _JOBS[j["id"]] = j
        except Exception:
            pass


def _parse_sse(raw: str) -> tuple[str, Optional[dict]]:
    """解析一条 SSE 文本块 → (event, data dict)。"""
    event = "message"
    data_str = ""
    for line in raw.split("\n"):
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_str += line[5:].strip()
    if not data_str:
        return event, None
    try:
        return event, json.loads(data_str)
    except json.JSONDecodeError:
        return event, None


def _run_job(job_id: str, factory: Callable[[], Generator[str, None, None]]) -> None:
    """后台线程体：消费 SSE 生成器，累积进度到 job。"""
    try:
        for chunk in factory():
            event, data = _parse_sse(chunk)
            data = data or {}
            with _LOCK:
                job = _JOBS.get(job_id)
                if job is None:
                    return
                prog = job["progress"]
                prog.append({"event": event, "data": data})
                if len(prog) > _MAX_PROGRESS:
                    # 保留头部 start + 尾部大部分
                    job["progress"] = prog[:1] + prog[-(_MAX_PROGRESS - 1):]
                # 关键事件落盘
                if event in ("end", "error", "placed", "fetched"):
                    if event == "end":
                        job["status"] = "done"
                        job["result"] = data
                        job["finished_at"] = _now()
                    _persist_locked()
        # 生成器正常耗尽但没发 end：兜底标记完成
        with _LOCK:
            job = _JOBS.get(job_id)
            if job and job["status"] == "running":
                job["status"] = "done"
                job["finished_at"] = _now()
                _persist_locked()
    except Exception as e:
        with _LOCK:
            job = _JOBS.get(job_id)
            if job:
                job["status"] = "error"
                job["error"] = str(e)
                job["finished_at"] = _now()
                _persist_locked()


def find_active(job_type: str) -> Optional[dict]:
    """返回某类型正在运行的任务（单例约束用）。"""
    with _LOCK:
        for j in _JOBS.values():
            if j["type"] == job_type and j["status"] == "running":
                return dict(j)
    return None


def start_job(
    job_type: str, title: str, factory: Callable[[], Generator[str, None, None]]
) -> dict:
    """启动后台任务。同类已有 running 则返回那个（单例）。返回 job 精简信息。"""
    existing = find_active(job_type)
    if existing:
        return {"job_id": existing["id"], "status": "running", "reused": True}

    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "type": job_type,
        "title": title,
        "status": "running",
        "progress": [],
        "result": {},
        "error": None,
        "started_at": _now(),
        "finished_at": None,
    }
    with _LOCK:
        _JOBS[job_id] = job
        _persist_locked()

    t = threading.Thread(target=_run_job, args=(job_id, factory), daemon=True)
    t.start()
    return {"job_id": job_id, "status": "running", "reused": False}


def get_job(job_id: str) -> Optional[dict]:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def list_active() -> list[dict]:
    with _LOCK:
        return [
            {"id": j["id"], "type": j["type"], "title": j["title"], "started_at": j["started_at"]}
            for j in _JOBS.values()
            if j["status"] == "running"
        ]
