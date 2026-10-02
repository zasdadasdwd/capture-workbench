"""管理服务生命周期的异常收尾测试，不启动真实代理或写入用户数据。"""

import asyncio
from builtins import BaseExceptionGroup
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from capture.backend import context


def make_workbench(autostart=False):
    """构造只记录调用的进程依赖，避免触碰真实配置和存储。"""
    return SimpleNamespace(
        settings=object(),
        startup=SimpleNamespace(proxy=SimpleNamespace(autostart=autostart)),
        engine=SimpleNamespace(
            ensure_proxy=AsyncMock(), start=AsyncMock(), shutdown=AsyncMock()
        ),
        analysis=SimpleNamespace(close=AsyncMock()),
        store=SimpleNamespace(finish=Mock(), close=Mock()),
        jobs={},
    )


def test_autostart_failure_closes_every_resource(monkeypatch):
    """自动启动失败时，已经创建的依赖也必须关闭。"""
    state = make_workbench(autostart=True)
    state.engine.start.side_effect = RuntimeError("启动失败")
    monkeypatch.setattr(context, "Workbench", lambda: state)

    async def run():
        with pytest.raises(RuntimeError, match="启动失败"):
            async with context.lifespan(SimpleNamespace(state=SimpleNamespace())):
                pytest.fail("启动失败后不应进入服务体")

    asyncio.run(run())
    state.analysis.close.assert_awaited_once()
    state.engine.shutdown.assert_awaited_once()
    state.store.close.assert_called_once()


def test_service_failure_cancels_snapshot_even_when_callback_removes_job(monkeypatch):
    """服务体抛错时，任务回调清空 jobs 也不应漏掉会话收尾。"""
    state = make_workbench()
    monkeypatch.setattr(context, "Workbench", lambda: state)

    async def run():
        with pytest.raises(ValueError, match="服务失败"):
            async with context.lifespan(SimpleNamespace(state=SimpleNamespace())):
                task = asyncio.create_task(asyncio.sleep(3600))
                state.jobs["replay-1"] = task
                task.add_done_callback(lambda _: state.jobs.pop("replay-1", None))
                raise ValueError("服务失败")
        assert not state.jobs
        assert task.cancelled()

    asyncio.run(run())
    state.store.finish.assert_called_once_with("replay-1", "cancelled")
    state.analysis.close.assert_awaited_once()
    state.engine.shutdown.assert_awaited_once()
    state.store.close.assert_called_once()


def test_cleanup_errors_do_not_skip_later_resources(monkeypatch):
    """多个关闭步骤失败时仍尝试其余步骤，并向调用方报告所有错误。"""
    state = make_workbench()
    state.store.finish.side_effect = OSError("会话关闭失败")
    state.analysis.close.side_effect = RuntimeError("分析关闭失败")
    state.engine.shutdown.side_effect = RuntimeError("代理关闭失败")
    monkeypatch.setattr(context, "Workbench", lambda: state)

    async def run():
        with pytest.raises(BaseExceptionGroup) as caught:
            async with context.lifespan(SimpleNamespace(state=SimpleNamespace())):
                state.jobs["replay-2"] = asyncio.create_task(asyncio.sleep(3600))
        assert [str(exc) for exc in caught.value.exceptions] == [
            "会话关闭失败",
            "分析关闭失败",
            "代理关闭失败",
        ]

    asyncio.run(run())
    state.store.finish.assert_called_once_with("replay-2", "cancelled")
    state.analysis.close.assert_awaited_once()
    state.engine.shutdown.assert_awaited_once()
    state.store.close.assert_called_once()


def test_startup_and_cleanup_errors_are_both_reported(monkeypatch):
    """自动启动与关闭同时失败时，保留两个阶段的错误。"""
    state = make_workbench(autostart=True)
    state.engine.start.side_effect = RuntimeError("启动失败")
    state.analysis.close.side_effect = OSError("分析关闭失败")
    monkeypatch.setattr(context, "Workbench", lambda: state)

    async def run():
        with pytest.raises(BaseExceptionGroup) as caught:
            async with context.lifespan(SimpleNamespace(state=SimpleNamespace())):
                pytest.fail("启动失败后不应进入服务体")
        assert [str(exc) for exc in caught.value.exceptions] == [
            "启动失败",
            "分析关闭失败",
        ]

    asyncio.run(run())
    state.engine.shutdown.assert_awaited_once()
    state.store.close.assert_called_once()
