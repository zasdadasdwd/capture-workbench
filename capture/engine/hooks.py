"""请求与响应扩展链：按设置中的注册名顺序执行 Hook 实例。"""

import inspect
from collections.abc import Mapping
from pathlib import PurePosixPath

from capture.plugins.base import BaseHook, registered_hooks


class HookRunner:
    """每个代理进程缓存 Hook 实例，分别处理请求和响应。"""

    def __init__(self, hooks: Mapping[str, type[BaseHook]] | None = None):
        """运行时接收进程快照；省略参数供临时 Hook 测试使用。"""
        self.hooks = hooks if hooks is not None else registered_hooks()
        self.instances = {}

    def load(self, name):
        """按注册名创建一次实例；未知名称给出明确错误。"""
        if name not in self.instances:
            hook_class = self.hooks.get(name)
            if hook_class is None:
                raise ValueError(f"未注册的 Hook：{name}")
            self.instances[name] = hook_class()
        return self.instances[name]

    def run_request(self, entries, flow, context, is_blocked):
        """发送前顺序执行；改写到拒绝域名时停止后续 Hook。"""
        return self._run("request", entries, flow, context, is_blocked)

    def run_response(self, entries, flow, context):
        """响应完成后顺序执行；可修改状态、头部和正文。"""
        self._run("response", entries, flow, context)

    def _run(self, stage, entries, flow, context, is_blocked=None):
        for entry in entries:
            if not entry.get("enabled", True):
                continue
            # 已保存的旧配置可能仍是 plugins/名称.py。
            name = entry.get("name") or PurePosixPath(entry["path"]).stem
            context.hook_name = name
            try:
                callback = getattr(self.load(name), f"on_{stage}")
                result = callback(flow, context)
                if inspect.isawaitable(result):
                    close = getattr(result, "close", None)
                    if callable(close):
                        close()
                    raise ValueError("Hook 不能返回协程，请使用同步方法")
            except Exception as exc:
                raise RuntimeError(f"{name} ({stage}): {exc}") from exc
            executed = flow.metadata.setdefault("executed_hooks", [])
            if name not in executed:
                executed.append(name)
            if is_blocked and is_blocked():
                return False
            if stage == "request" and flow.response is not None:
                break
        return True
