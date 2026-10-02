"""请求 Hook 的基类和注册表。继承 BaseHook 即完成注册。"""

import importlib
import inspect
import re
import sys
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import ClassVar

_registry: dict[str, type["BaseHook"]] = {}
_module_hooks: dict[str, dict[str, type["BaseHook"]]] = {}


class BaseHook:
    """扩展点：子类设置唯一 name，可实现同步请求或响应处理。"""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs):
        """在模块导入时注册子类；尽早发现重复名称和错误接口。"""
        super().__init_subclass__(**kwargs)
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", cls.name):
            raise ValueError(f"Hook 名称无效：{cls.name!r}")
        if any(
            inspect.iscoroutinefunction(method)
            for method in (cls.on_request, cls.on_response)
        ):
            raise ValueError(f"Hook {cls.name} 必须使用同步方法")
        if cls.name in _registry:
            raise ValueError(f"Hook 名称重复：{cls.name}")
        _registry[cls.name] = cls

    def on_request(self, flow, context) -> None:
        """请求发送前调用；默认不修改请求。"""

    def on_response(self, flow, context) -> None:
        """响应返回后调用；默认不修改响应。"""


def registered_hooks() -> Mapping[str, type[BaseHook]]:
    """返回只读实时注册表，供直接定义临时 Hook 的测试使用。"""
    from . import hooks  # noqa: F401 -- 导入时触发子类注册。

    importlib.import_module("hook_template")
    return MappingProxyType(_registry)


def discover_hooks(modules: Sequence[str] = ()) -> Mapping[str, type[BaseHook]]:
    """按启动时固定的模块名发现 Hook；失败时回滚注册和导入缓存。"""
    before = dict(_registry)
    previous_modules = dict(_module_hooks)
    loaded = set(sys.modules)
    found = {}
    imports = ("capture.plugins.hooks", "hook_template", *modules)
    try:
        for module in imports:
            previous = set(_registry)
            importlib.import_module(module)
            owned = {
                name: cls
                for name, cls in _registry.items()
                if name not in previous
                or cls.__module__ == module
                or cls.__module__.startswith(module + ".")
            }
            owned.update(_module_hooks.get(module, {}))
            _module_hooks[module] = owned
            found.update(owned)
    except Exception as exc:
        # 子模块可能已成功导入并注册；清理缓存才能在修复后真正重试。
        added = {name: cls for name, cls in _registry.items() if name not in before}
        _registry.clear()
        _registry.update(before)
        _module_hooks.clear()
        _module_hooks.update(previous_modules)
        registered_modules = {cls.__module__ for cls in added.values()}
        # 入口可能经多个同包中间模块才导入定义类的文件；整个插件命名空间
        # 都需回退，但不清理同一时刻导入的无关外部依赖。
        namespaces = {item.rpartition(".")[0] or item for item in imports}
        for name in set(sys.modules) - loaded:
            if name in registered_modules or any(
                name == namespace or name.startswith(namespace + ".")
                for namespace in namespaces
            ):
                imported = sys.modules.pop(name, None)
                parent, _, child = name.rpartition(".")
                if parent and getattr(sys.modules.get(parent), child, None) is imported:
                    delattr(sys.modules[parent], child)
        raise RuntimeError(f"加载 Hook 模块 {module!r} 失败：{exc}") from exc

    return MappingProxyType(found)


def hook_catalog(
    hooks: Mapping[str, type[BaseHook]] | None = None,
) -> list[dict[str, str]]:
    """供设置界面展示固定快照；省略参数时供临时 Hook 测试使用。"""
    if hooks is None:
        hooks = registered_hooks()
    return [
        {"name": name, "description": cls.description or cls.__doc__ or ""}
        for name, cls in hooks.items()
    ]
