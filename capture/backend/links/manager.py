"""管理独立分析任务：单并发、取消、超时和独立结果归档。"""

import asyncio
import hashlib
import json
import multiprocessing
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty
from uuid import uuid4

from capture.backend.links.models import LinkOptions, LinkView
from capture.backend.links.worker import run_worker


class AnalysisManager:
    """父进程只编排任务；报文解码与值匹配全部交给 spawn 子进程。"""

    def __init__(self, captures: Path, directory: Path, timeout=90):
        self.captures = captures.resolve()
        self.directory = directory.resolve()
        for folder in (
            self.directory,
            self.directory / "jobs",
            self.directory / "views",
        ):
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.context = multiprocessing.get_context("spawn")
        self.timeout = timeout
        self.jobs = {}
        self.active = None
        self.lock = asyncio.Lock()

    @staticmethod
    def identifier(value):
        if not re.fullmatch(r"[0-9a-f]{32}", value):
            raise ValueError("分析 ID 无效")
        return value

    def path(self, kind, id):
        return self.directory / kind / (self.identifier(id) + ".json")

    @staticmethod
    def write(path, value):
        """原子保存分析文件，防止退出或读取时遇到半份 JSON。"""
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False))
        temp.chmod(0o600)
        temp.replace(path)

    def load(self, id):
        path = self.path("jobs", id)
        if not path.is_file():
            raise FileNotFoundError("分析结果不存在")
        return json.loads(path.read_text())

    @staticmethod
    def key(options):
        payload = options.model_dump(exclude={"incremental_from"})
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    async def start(self, options: LinkOptions):
        """拒绝无界排队；同一任务最多执行 90 秒，结果持久化到独立目录。"""
        if options.operation == "search" and options.query_from and not options.query:
            previous = await asyncio.to_thread(self.load, options.query_from)
            if previous["options"]["session_id"] != options.session_id:
                raise ValueError("搜索引用不属于当前会话")
            options = options.model_copy(
                update={
                    "query": previous["options"]["query"],
                    "field": previous["options"]["field"],
                    "value_range": previous["options"]["value_range"],
                    "query_from": None,
                }
            )
        async with self.lock:
            if self.active:
                raise RuntimeError("分析进程正在忙，请等待或取消当前任务")
            folder = (self.captures / options.session_id).resolve()
            if (
                folder.parent != self.captures
                or not (folder / "capture.sqlite").is_file()
            ):
                raise FileNotFoundError("会话不存在")
            previous = None
            if options.incremental_from:
                prior = await asyncio.to_thread(self.load, options.incremental_from)
                if (
                    prior["options_key"] != self.key(options)
                    or prior["status"] != "complete"
                ):
                    raise ValueError("增量任务必须使用同一起点、字段和筛选条件")
                previous = prior.get("result")
            id = uuid4().hex
            queue = self.context.Queue(maxsize=8)
            cancel = self.context.Event()
            process = self.context.Process(
                target=run_worker,
                args=(
                    str(self.captures),
                    str(self.directory),
                    options.model_dump(),
                    cancel,
                    queue,
                    previous,
                ),
                daemon=True,
            )
            job = {
                "id": id,
                "status": "running",
                "created": datetime.now(timezone.utc).isoformat(),
                "options": options.model_dump(),
                "options_key": self.key(options),
                "progress": {"scanned": 0, "total": 0, "matched": 0},
            }
            await asyncio.to_thread(self.write, self.path("jobs", id), job)
            try:
                await asyncio.to_thread(process.start)
            except Exception as exc:
                queue.close()
                process.close()
                job.update(
                    status="error", error="分析进程启动失败：" + type(exc).__name__
                )
                await asyncio.to_thread(self.write, self.path("jobs", id), job)
                raise RuntimeError(job["error"]) from exc
            runtime = {
                "process": process,
                "queue": queue,
                "cancel": cancel,
                "job": job,
                "started": time.monotonic(),
            }
            self.jobs[id] = runtime
            self.active = id
            runtime["task"] = asyncio.create_task(self.monitor(id))
            return self.status(id)

    @staticmethod
    def messages(queue):
        """大结果的反序列化在后台线程完成，IPC 读取不会阻塞 Web 事件循环。"""
        events = []
        for _ in range(16):
            try:
                events.append(queue.get_nowait())
            except Empty:
                break
        return events

    async def monitor(self, id):
        """轮询小型 IPC 消息；结果写入由线程处理，避免阻塞 Web 事件循环。"""
        runtime = self.jobs[id]
        process = runtime["process"]
        job = runtime["job"]
        dead_ticks = 0
        try:
            while job["status"] == "running":
                if time.monotonic() - runtime["started"] > self.timeout:
                    job.update(status="timeout", error="分析超过时间上限，请缩小范围")
                    break
                if runtime["cancel"].is_set():
                    job["status"] = "cancelled"
                    break
                events = await asyncio.to_thread(self.messages, runtime["queue"])
                for event in events:
                    if event["type"] == "progress":
                        job["progress"] = {
                            k: v for k, v in event.items() if k != "type"
                        }
                    elif event["type"] == "result":
                        job.update(status="complete", result=event["result"])
                    elif event["type"] == "cancelled":
                        job["status"] = "cancelled"
                    else:
                        job.update(status="error", error=event["error"])
                if job["status"] != "running":
                    break
                if time.monotonic() - runtime["started"] > self.timeout:
                    job.update(status="timeout", error="分析超过时间上限，请缩小范围")
                    break
                if runtime["cancel"].is_set():
                    job["status"] = "cancelled"
                    break
                if not process.is_alive():
                    dead_ticks += 1
                    if dead_ticks >= 3:
                        job.update(
                            status="error",
                            error=f"分析进程退出（{process.exitcode}），抓包不受影响",
                        )
                        break
                await asyncio.sleep(0.15)
        finally:
            if process.is_alive() and job["status"] != "complete":
                process.terminate()
            await asyncio.to_thread(process.join, 2)
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join, 2)
            runtime["queue"].close()
            runtime["queue"].join_thread()
            process.close()
            job["duration_ms"] = round(
                (time.monotonic() - runtime["started"]) * 1000, 2
            )
            job["finished"] = datetime.now(timezone.utc).isoformat()
            await asyncio.to_thread(self.write, self.path("jobs", id), job)
            self.jobs.pop(id, None)
            if self.active == id:
                self.active = None

    def status(self, id):
        self.identifier(id)
        job = self.jobs[id]["job"] if id in self.jobs else self.load(id)
        if id not in self.jobs and job["status"] == "running":
            job = {
                **job,
                "status": "interrupted",
                "error": "任务所在后端已退出，请重新分析",
            }
        # 状态轮询不传大图和查询中的敏感搜索词。
        return {
            key: job[key]
            for key in (
                "id",
                "status",
                "created",
                "progress",
                "duration_ms",
                "finished",
                "error",
            )
            if key in job
        }

    def result(self, id, offset=0, limit=100):
        job = self.load(id) if id not in self.jobs else self.jobs[id]["job"]
        if job["status"] != "complete":
            return {**self.status(id), "items": []}
        result = {
            key: value
            for key, value in job["result"].items()
            if not key.startswith("_")
        }
        result["job_id"] = id
        result["configuration"] = {
            key: value
            for key, value in job["options"].items()
            if key not in ("query", "reveal", "incremental_from")
        }
        if result["operation"] == "search":
            # 保存视图和实时查询使用任务引用，不向结果复制敏感搜索明文。
            result["configuration"]["query_from"] = id
        if result["operation"] == "fields":
            items = result["items"]
            result.update(
                items=items[offset : offset + limit],
                total=len(items),
                has_more=offset + limit < len(items),
            )
        else:
            nodes = result["nodes"]
            page = nodes[offset : offset + limit]
            ids = {node["id"] for node in page}
            result.update(
                nodes=page,
                edges=[
                    edge
                    for edge in result["edges"]
                    if edge.get("related_request_id", edge["to"]) in ids
                ],
                total=len(nodes),
                has_more=offset + limit < len(nodes),
            )
        return result

    async def cancel(self, id):
        self.identifier(id)
        runtime = self.jobs.get(id)
        if runtime:
            runtime["cancel"].set()
            await runtime["task"]
        return self.status(id)

    async def close(self):
        if self.active:
            await self.cancel(self.active)

    def save_view(self, view: LinkView, id=None):
        """持久化布局和人工判断，任务结果继续独立保存。"""
        for job_id in view.job_ids:
            self.load(job_id)
        id = self.identifier(id) if id else uuid4().hex
        value = {
            "id": id,
            "updated": datetime.now(timezone.utc).isoformat(),
            **view.model_dump(),
        }
        self.write(self.path("views", id), value)
        return value

    def views(self):
        result = []
        for path in sorted(
            (self.directory / "views").glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:100]:
            item = json.loads(path.read_text())
            result.append({key: item[key] for key in ("id", "name", "updated")})
        return result

    def view(self, id):
        """打开保存视图时复核请求是否仍存在，缺失节点明确标记。"""
        path = self.path("views", id)
        if not path.is_file():
            raise FileNotFoundError("视图不存在")
        view = json.loads(path.read_text())
        missing = []
        for job_id in view["job_ids"]:
            job = self.load(job_id)
            if job["status"] != "complete":
                continue
            result = job["result"]
            ids = {node["id"] for node in result.get("nodes", [])} | {
                job["options"]["flow_id"]
            }
            folder = (self.captures / job["options"]["session_id"]).resolve()
            if folder.parent != self.captures:
                raise ValueError("会话路径无效")
            try:
                with sqlite3.connect(
                    (folder / "capture.sqlite").as_uri() + "?mode=ro",
                    uri=True,
                    timeout=0.2,
                ) as db:
                    found = {
                        row[0]
                        for row in db.execute(
                            "SELECT id FROM flows WHERE id IN ("
                            + ",".join("?" for _ in ids)
                            + ")",
                            list(ids),
                        )
                    }
                missing.extend(sorted(ids - found))
            except sqlite3.OperationalError:
                missing.extend(sorted(ids))
        view["missing_requests"] = list(dict.fromkeys(missing))
        return view
