"""Read-only stdio MCP bridge for the Blender Shortcut Companion addon.

The Blender addon owns context tracking and shortcut resolution. This process
only reads its authenticated loopback endpoint; it never executes Blender code.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from typing import Annotated, Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field


DEFAULT_PORT = 18764
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 2.0
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

mcp = FastMCP(
    "Blender Shortcut Companion",
    instructions=(
        "Read the current Blender editor, mode, selection and effective shortcut "
        "recommendations. The addon tracks the last hovered Blender editor; "
        "recommendations are contextual candidates, not a prediction of intent. "
        "No tool executes code, changes a scene or sends keyboard input. "
        "Return an unavailable/error result honestly when Blender is offline."
    ),
)


class _RejectRedirects(HTTPRedirectHandler):
    """Keep the credential on the one explicitly configured loopback endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"status": "unavailable", "error": {"code": code, "message": message}, **extra}


def _settings() -> tuple[str, str] | dict[str, Any]:
    token = os.environ.get("BLENDER_SHORTCUT_TOKEN", "").strip()
    if not token:
        return _error(
            "missing_token",
            "未设置 BLENDER_SHORTCUT_TOKEN。请从 Blender 插件复制连接令牌，并重启 MCP 连接。",
        )
    if any(ord(char) < 33 or ord(char) > 126 for char in token):
        return _error("invalid_token", "连接令牌必须为不含空白的 ASCII 字符串。")
    try:
        port = int(os.environ.get("BLENDER_SHORTCUT_PORT", str(DEFAULT_PORT)))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        return _error("invalid_port", "BLENDER_SHORTCUT_PORT 必须是 1 到 65535 的整数。")
    return f"http://127.0.0.1:{port}/v1/context", token


def _fetch_snapshot() -> dict[str, Any]:
    settings = _settings()
    if isinstance(settings, dict):
        return settings
    endpoint, token = settings
    request = Request(
        endpoint,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    # Do not inherit HTTP_PROXY, and never follow redirects carrying this token.
    opener = build_opener(ProxyHandler({}), _RejectRedirects())
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            return _error("response_too_large", "Blender 返回的数据超过 2 MiB 上限。")
        snapshot = json.loads(raw.decode("utf-8"))
    except HTTPError as error:
        if error.code in (401, 403):
            return _error(
                "authentication_failed",
                "Blender 拒绝连接令牌。请复制插件当前令牌后更新 MCP 环境变量。",
            )
        if 300 <= error.code < 400:
            return _error("redirect_rejected", "Blender 端点返回重定向；连接已停止。")
        return _error("http_error", f"Blender 端点返回 HTTP {error.code}。")
    except (TimeoutError, socket.timeout):
        return _error("timeout", "读取 Blender 上下文超时。请检查插件是否运行。")
    except URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            return _error("timeout", "读取 Blender 上下文超时。请检查插件是否运行。")
        return _error(
            "offline",
            "无法连接 Blender。请启动 Blender、启用插件并检查本地桥接端口。",
        )
    except OSError:
        return _error("offline", "无法读取 Blender 端点。请检查插件和本地桥接端口。")
    except (UnicodeError, json.JSONDecodeError):
        return _error("invalid_response", "Blender 端点返回了无效的 UTF-8 JSON。")

    if not isinstance(snapshot, dict):
        return _error("invalid_response", "Blender 端点应返回一个 JSON 对象。")
    if snapshot.get("schema_version") != 1:
        return _error("unsupported_schema", "Blender 端点数据版本不受支持，需要 schema_version=1。")
    if snapshot.get("status") != "connected":
        return _error("not_ready", "Blender 插件尚未提供可用的上下文，请将鼠标移到编辑器中。")
    if not isinstance(snapshot.get("context"), dict):
        return _error("invalid_response", "Blender 端点缺少有效 context 对象。")
    recommendations = snapshot.get("recommendations")
    if not isinstance(recommendations, list) or not all(
        isinstance(item, dict) for item in recommendations
    ):
        return _error("invalid_response", "Blender 端点缺少有效 recommendations 列表。")
    return snapshot


async def _snapshot() -> dict[str, Any]:
    return await asyncio.to_thread(_fetch_snapshot)


@mcp.tool(annotations=READ_ONLY)
async def get_blender_context() -> dict[str, Any]:
    """读取 Blender 最新编辑器、模式、选择、工具、按住的键及候选快照。"""
    return await _snapshot()


@mcp.tool(annotations=READ_ONLY)
async def suggest_shortcuts(
    query: Annotated[str, Field(max_length=256)] = "",
    limit: Annotated[int, Field(ge=1, le=50)] = 12,
) -> dict[str, Any]:
    """查询常用快捷键或能接续当前按住的键的组合。

    query 留空返回插件已排好优先级的建议；多个空格分隔词须全部匹配。
    limit 为 1 到 50。空闲时返回当前编辑器和模式下的常用快捷键；
    按住前缀或进入模态操作时返回对应组合，可按动作、操作器或键位筛选。
    """
    snapshot = await _snapshot()
    if snapshot.get("status") != "connected":
        return snapshot
    query = query.strip()
    terms = query.casefold().split()
    recommendations = snapshot["recommendations"]
    if terms:
        fields = (
            "action", "label_zh", "label_en", "operator", "shortcut",
            "suffix_shortcut", "prefix_shortcut", "reason_zh", "keymap", "category",
        )
        recommendations = [
            item for item in recommendations
            if all(
                term in " ".join(str(item.get(field, "")) for field in fields).casefold()
                for term in terms
            )
        ]
    return {
        "status": "connected",
        "schema_version": 1,
        "updated_at": snapshot.get("updated_at"),
        "sequence": snapshot.get("sequence"),
        "tracking": snapshot.get("tracking"),
        "context": snapshot["context"],
        "input": snapshot.get("input", {}),
        "modal": snapshot.get("modal"),
        "next_steps": snapshot.get("next_steps", []),
        "display_mode": snapshot.get("display_mode"),
        "common_shortcuts": snapshot.get("common_shortcuts", []),
        "common_count": snapshot.get("common_count", len(snapshot.get("common_shortcuts", []))),
        "binding_count": snapshot.get("binding_count", len(snapshot["recommendations"])),
        "candidate_count": snapshot.get("candidate_count", len(snapshot["recommendations"])),
        "query": query,
        "count": min(len(recommendations), limit),
        "total_matches": len(recommendations),
        "recommendations": recommendations[:limit],
    }


@mcp.tool(annotations=READ_ONLY)
async def get_blender_connection_status() -> dict[str, Any]:
    """检查本地 Blender 插件是否可读取，并返回版本与采集状态；不返回令牌。"""
    snapshot = await _snapshot()
    if snapshot.get("status") != "connected":
        return snapshot
    return {
        "status": "connected",
        "schema_version": snapshot["schema_version"],
        "blender_version": snapshot.get("blender_version"),
        "updated_at": snapshot.get("updated_at"),
        "sequence": snapshot.get("sequence"),
        "tracking": snapshot.get("tracking"),
        "editor": snapshot["context"].get("editor"),
        "mode": snapshot["context"].get("mode"),
        "read_only": True,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
