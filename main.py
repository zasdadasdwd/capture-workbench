"""启动本地管理界面：python main.py。"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

import uvicorn

from config import ROOT, load_startup


def main():
    """解析启动配置并运行 Web/API；MCP 由独立适配器调用这些 API。"""
    parser = argparse.ArgumentParser(description="Capture 流量工作台")
    parser.add_argument(
        "--config", type=Path, default=ROOT / "startup.toml", help="启动配置 TOML 路径"
    )
    args = parser.parse_args()
    path = args.config.resolve()
    if not path.exists():
        parser.error(f"启动配置不存在：{path}")
    os.environ["CAPTURE_STARTUP_FILE"] = str(path)
    startup = load_startup(path)
    mcp_process = None
    if startup.mcp.enabled:
        if startup.mcp.transport == "stdio":
            parser.error(
                "stdio MCP 由 Agent 客户端启动；随工作台启动请使用 streamable-http"
            )
        if startup.mcp.port == startup.web.port:
            parser.error("MCP 与管理界面不能使用相同端口")
        if startup.mcp.entrypoint not in (
            "",
            "agent_mcp.server:main",  # 兼容已有启动配置。
            "capture.agent_mcp.server:main",
        ):
            parser.error("本版本 MCP 入口为 capture.agent_mcp.server:main")
        mcp_process = subprocess.Popen(
            [sys.executable, "-m", "capture.agent_mcp.server", "--config", str(path)], cwd=ROOT
        )
    try:
        # 给 SSE 等长连接留出收尾时间，避免它们让程序退出无限等待。
        uvicorn.run(
            "capture.backend.app:app",
            host=startup.web.host,
            port=startup.web.port,
            timeout_graceful_shutdown=5,
        )
    finally:
        if mcp_process:
            mcp_process.terminate()
            try:
                mcp_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                mcp_process.kill()
                mcp_process.wait()


if __name__ == "__main__":
    main()
