"""注册式 Hook 的请求、响应、顺序、开关与旧配置迁移。"""

import asyncio
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from mitmproxy import http
from mitmproxy.test import tflow
from pydantic import ValidationError

import capture.backend.api.system as system_module
import capture.backend.context as context_module
from capture.engine.hooks import HookRunner
from capture.engine.manager import EngineManager
from capture.plugins.base import BaseHook, discover_hooks, hook_catalog
from config import (
    DEFAULT_SETTINGS,
    ROOT,
    ExtensionsStartup,
    RequestHook,
    Settings,
    load_settings,
)


def flow_with_response():
    """创建可分别检验请求和响应修改的本地报文。"""
    flow = tflow.tflow()
    flow.request = http.Request.make("GET", "http://example.test/")
    flow.response = None
    return flow


def test_registered_hooks_run_in_order_and_cache_instances():
    """禁用项不实例化，启用项按顺序处理两个阶段。"""
    created = []

    class First(BaseHook):
        name = "test_chain_first"

        def __init__(self):
            created.append(self.name)

        def on_request(self, flow, context):
            flow.request.headers["X-Chain"] = "first"

        def on_response(self, flow, context):
            flow.response.headers["X-Chain"] = "first"

    class Second(BaseHook):
        name = "test_chain_second"

        def on_request(self, flow, context):
            assert context.hook_name == self.name
            flow.request.headers["X-Chain"] += ",second"

        def on_response(self, flow, context):
            flow.response.headers["X-Chain"] += ",second"

    class Disabled(BaseHook):
        name = "test_chain_disabled"

        def __init__(self):
            raise AssertionError("禁用的 Hook 不应实例化")

    entries = [
        {"name": First.name, "enabled": True},
        {"name": Disabled.name, "enabled": False},
        {"name": Second.name, "enabled": True},
    ]
    runner = HookRunner()
    flow = flow_with_response()
    assert runner.run_request(entries, flow, SimpleNamespace(), lambda: False)
    assert flow.request.headers["X-Chain"] == "first,second"
    flow.response = http.Response.make(200, b"original")
    runner.run_response(entries, flow, SimpleNamespace())
    assert flow.response.headers["X-Chain"] == "first,second"
    assert flow.metadata["executed_hooks"] == [First.name, Second.name]
    assert created == [First.name]
    assert runner.load(First.name) is runner.load(First.name)
    assert {item["name"] for item in hook_catalog()} >= {First.name, Second.name}


def test_request_error_blocked_redirect_and_response_error():
    """错误与改写后的拒绝域名会中断链；响应错误标明阶段。"""

    class Broken(BaseHook):
        name = "test_broken_request"

        def on_request(self, flow, context):
            raise ValueError("signature failed")

    class Redirect(BaseHook):
        name = "test_redirect_request"

        def on_request(self, flow, context):
            flow.request.host = "blocked.test"

    class Later(BaseHook):
        name = "test_later_request"

        def on_request(self, flow, context):
            raise AssertionError("不应执行后续 Hook")

    runner = HookRunner()
    with pytest.raises(
        RuntimeError, match=r"test_broken_request \(request\): signature failed"
    ):
        runner.run_request(
            [{"name": Broken.name}],
            flow_with_response(),
            SimpleNamespace(),
            lambda: False,
        )
    flow = flow_with_response()
    assert not runner.run_request(
        [{"name": Redirect.name}, {"name": Later.name}],
        flow,
        SimpleNamespace(),
        lambda: flow.request.host == "blocked.test",
    )

    class BrokenResponse(BaseHook):
        name = "test_broken_response"

        def on_response(self, flow, context):
            raise ValueError("bad body")

    flow.response = http.Response.make(200, b"original")
    with pytest.raises(
        RuntimeError, match=r"test_broken_response \(response\): bad body"
    ):
        runner.run_response([{"name": BrokenResponse.name}], flow, SimpleNamespace())


def test_registration_validation_template_and_legacy_settings():
    """拒绝重复/异步定义；旧文件路径只迁移为注册名。"""
    names = {item["name"] for item in hook_catalog()}
    assert {"request_hook", "example_headers", "example_query", "template"} <= names
    assert (
        RequestHook.model_validate({"path": "plugins/request_hook.py"}).name
        == "request_hook"
    )
    assert Settings(hook_enabled=True).request_hooks[0].name == "request_hook"
    for name in ("../outside", "BadName", "plugins/test.js"):
        with pytest.raises(ValidationError):
            RequestHook(name=name)
    with pytest.raises(ValidationError, match="重复"):
        Settings(
            request_hooks=[RequestHook(name="template"), RequestHook(name="template")]
        )
    with pytest.raises(ValueError, match="重复"):

        class Duplicate(BaseHook):
            name = "template"

    with pytest.raises(ValueError, match="同步"):

        class AsyncHook(BaseHook):
            name = "test_async_hook"

            async def on_response(self, flow, context):
                pass

    with pytest.raises(RuntimeError, match="未注册"):
        HookRunner().run_request(
            [{"name": "missing_hook"}],
            flow_with_response(),
            SimpleNamespace(),
            lambda: False,
        )
    flow = flow_with_response()
    flow.response = http.Response.make(200, b"unchanged")
    runner = HookRunner()
    entries = [{"name": "template", "enabled": True}]
    runner.run_request(entries, flow, SimpleNamespace(), lambda: False)
    runner.run_response(entries, flow, SimpleNamespace())
    assert flow.response.content == b"unchanged"


def test_configured_third_party_hook_module(tmp_path, monkeypatch):
    """启动配置中的模块可被导入、注册并用于请求修改。"""
    module = tmp_path / "third_party_capture_hook.py"
    module.write_text(
        "from capture.plugins import BaseHook\n"
        "class ThirdPartyHook(BaseHook):\n"
        "    name = 'test_third_party_hook'\n"
        "    description = '测试第三方注册'\n"
        "    def on_request(self, flow, context):\n"
        "        flow.request.headers['X-Third-Party'] = 'ready'\n"
    )
    startup = tmp_path / "startup.toml"
    startup.write_text('[extensions]\nhook_modules = ["third_party_capture_hook"]\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("CAPTURE_STARTUP_FILE", str(startup))
    snapshot = discover_hooks(["third_party_capture_hook"])
    assert "test_third_party_hook" in snapshot
    flow = flow_with_response()
    HookRunner(snapshot).run_request(
        [{"name": "test_third_party_hook"}], flow, SimpleNamespace(), lambda: False
    )
    assert flow.request.headers["X-Third-Party"] == "ready"
    # mitmdump 是独立进程；确认同一启动文件和导入路径在子进程可用。
    environment = dict(
        os.environ, PYTHONPATH=os.pathsep.join([str(tmp_path), str(ROOT)])
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from capture.plugins.base import discover_hooks; print(json.dumps(sorted(discover_hooks(['third_party_capture_hook']))))",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "test_third_party_hook" in json.loads(result.stdout)


def test_discovery_snapshot_is_fixed_and_excludes_test_classes(tmp_path, monkeypatch):
    """配置和注册表后续变化不影响已固定的运行快照。"""
    module = tmp_path / "snapshot_hook.py"
    module.write_text(
        "from capture.plugins import BaseHook\n"
        "class SnapshotHook(BaseHook):\n"
        "    name = 'test_snapshot_hook'\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    snapshot = discover_hooks(["snapshot_hook"])
    assert "test_snapshot_hook" in snapshot

    class LocalHook(BaseHook):
        name = "test_local_after_snapshot"

    assert LocalHook.name not in snapshot
    assert LocalHook.name not in discover_hooks()
    assert "test_snapshot_hook" not in discover_hooks()
    assert LocalHook.name in HookRunner().hooks
    assert LocalHook.name not in HookRunner(snapshot).hooks


def test_failed_module_import_rolls_back_submodule_and_can_retry(tmp_path, monkeypatch):
    """包内类注册成功后顶层失败，也能修复并重试且不重复注册。"""
    package = tmp_path / "broken_capture_hook"
    package.mkdir()
    (package / "part.py").write_text(
        "from capture.plugins import BaseHook\n"
        "class PartialHook(BaseHook):\n"
        "    name = 'test_partial_retry_hook'\n"
    )
    root = package / "__init__.py"
    root.write_text("from . import part\nraise ValueError('broken')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(RuntimeError, match="broken"):
        discover_hooks(["broken_capture_hook"])
    assert "broken_capture_hook.part" not in sys.modules
    root.write_text("from . import part\n")
    snapshot = discover_hooks(["broken_capture_hook"])
    assert "test_partial_retry_hook" in snapshot


def test_cached_entry_and_failed_batch_retry(tmp_path, monkeypatch):
    """入口只导入同包实现时，重复发现及批次失败重试都保留类。"""
    package = tmp_path / "indirect_capture_hook"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "impl.py").write_text(
        "from capture.plugins import BaseHook\n"
        "class IndirectHook(BaseHook):\n"
        "    name = 'test_indirect_retry_hook'\n"
    )
    (package / "bridge.py").write_text("from .impl import IndirectHook\n")
    (package / "entry.py").write_text("from .bridge import IndirectHook\n")
    bad = tmp_path / "bad_capture_hook.py"
    bad.write_text("raise ValueError('batch failed')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(RuntimeError, match="batch failed"):
        discover_hooks(["indirect_capture_hook.entry", "bad_capture_hook"])
    assert "indirect_capture_hook.entry" not in sys.modules
    assert "indirect_capture_hook.bridge" not in sys.modules
    assert "indirect_capture_hook.impl" not in sys.modules
    bad.write_text("")
    snapshot = discover_hooks(["indirect_capture_hook.entry", "bad_capture_hook"])
    assert "test_indirect_retry_hook" in snapshot
    assert "test_indirect_retry_hook" in discover_hooks(["indirect_capture_hook.entry"])


def test_engine_passes_frozen_module_names_to_proxy(monkeypatch):
    """Web 管理器重启代理时传递构造时固定的模块列表。"""
    captured = {}
    manager = EngineManager(SimpleNamespace(), lambda event: None, ["first_hook"])

    async def fake_spawn(*args, **kwargs):
        captured.update(kwargs["env"])
        raise RuntimeError("stop before spawn")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    with pytest.raises(RuntimeError, match="stop before spawn"):
        asyncio.run(
            manager.start_proxy(
                SimpleNamespace(
                    listen_host="127.0.0.1",
                    listen_port=9999,
                    connection_mode="direct",
                    body_limit=1024,
                )
            )
        )
    assert json.loads(captured["CAPTURE_HOOK_MODULES"]) == ["first_hook"]


def test_workbench_freezes_startup_for_catalog_and_proxy(tmp_path, monkeypatch):
    """Workbench 读取启动文件后即使文件变更，目录和新代理仍用原模块。"""
    module = tmp_path / "initial_capture_hook.py"
    module.write_text(
        "from capture.plugins import BaseHook\n"
        "class InitialHook(BaseHook):\n"
        "    name = 'test_initial_capture_hook'\n"
    )
    startup = tmp_path / "startup.toml"
    startup.write_text('[extensions]\nhook_modules = ["initial_capture_hook"]\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("CAPTURE_STARTUP_FILE", str(startup))
    monkeypatch.setattr(context_module, "load_settings", Settings)
    monkeypatch.setattr(context_module, "save_settings", lambda settings: None)
    monkeypatch.setattr(context_module, "Store", lambda path: SimpleNamespace())
    monkeypatch.setattr(context_module, "QueryLogs", lambda path: SimpleNamespace())
    monkeypatch.setattr(
        context_module, "AnalysisManager", lambda *paths: SimpleNamespace()
    )
    state = context_module.Workbench()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(workbench=state))
    )
    startup.write_text('[extensions]\nhook_modules = ["later_capture_hook"]\n')
    catalog = system_module.hook_files(request)["hooks"]
    assert "test_initial_capture_hook" in {item["name"] for item in catalog}
    assert state.engine.hook_modules == ("initial_capture_hook",)
    with pytest.raises(HTTPException, match="未注册的 Hook：test_later_capture_hook"):
        asyncio.run(
            system_module.update_settings(
                Settings(request_hooks=[RequestHook(name="test_later_capture_hook")]),
                request,
            )
        )

    captured = {}

    async def fake_spawn(*args, **kwargs):
        captured.update(kwargs["env"])
        raise RuntimeError("stop before spawn")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    with pytest.raises(RuntimeError, match="stop before spawn"):
        asyncio.run(
            state.engine.start_proxy(
                SimpleNamespace(
                    listen_host="127.0.0.1",
                    listen_port=9999,
                    connection_mode="direct",
                    body_limit=1024,
                )
            )
        )
    assert json.loads(captured["CAPTURE_HOOK_MODULES"]) == ["initial_capture_hook"]


def test_default_settings_and_extension_name_validation(tmp_path, monkeypatch):
    """首次运行读取 Python 默认字典；扩展配置拒绝路径和重复模块。"""
    import config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "absent.json")
    assert load_settings().listen_port == DEFAULT_SETTINGS["listen_port"]
    with pytest.raises(ValidationError):
        ExtensionsStartup(hook_modules=["../unsafe.py"])
    with pytest.raises(ValidationError):
        ExtensionsStartup(hook_modules=["same", "same"])
