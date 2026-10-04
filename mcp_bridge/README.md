# Blender 快捷键助手：可选 MCP 桥接

快捷键助手 0.6.0 在 Blender 编辑区域底部持续显示提示：空闲时显示随当前编辑器和模式变化的常用完整键位，包含 `G / R / S` 等单键；按住前缀时切换为当前层下一步按键，增加修饰键后展开对应分支。实际移动等模态操作中的提示持续到操作结束，随后回到常用快捷键。选项按用途排序，相同效果的按键共享操作说明，每个按键仍完整显示；分支以淡强调色、`›` 和描边标出。所有按键自动换行，无分页。使用提示只需启用配套插件。

这个可选的独立 Python 进程让 Codex 等 MCP 客户端读取当前编辑器、模式、选择、常用快捷键、按住的键、下一步选项与模态状态。它只请求本机 `127.0.0.1` 的插件端点，不执行 Python、修改场景或模拟按键。

## 安装

1. 在 Blender 中启用配套插件，在 N 侧栏「快捷键」中点击「启动本机 MCP 数据桥」。从插件面板复制当前连接令牌。
2. 安装 Python 3.10 或以上版本；在此目录运行：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
   ```

   脚本只在此目录创建 `.venv` 并安装依赖。若系统中命令为 `py`，使用 `-PythonCommand py`。

3. 按 `config.example.toml` 的格式添加 MCP 服务器，将两个绝对路径换成实际解压目录，令牌换成插件复制出的值。Codex 配置可放在用户目录的 `.codex/config.toml` 或项目的 `.codex/config.toml`；此安装脚本不会修改配置。
4. 重启或重新加载 MCP 连接。先调用 `get_blender_connection_status`，再询问“我现在能用哪些 Blender 快捷键？”

`config.example.toml` 默认指向此交付包的 `outputs/mcp_bridge`；移动目录后须修改路径。示例通过服务器 `env` 向进程传入令牌。也可用 `env_vars` 转发 Codex 启动时已有的 `BLENDER_SHORTCUT_TOKEN`，但仅在终端设置变量不会改变已运行的桌面应用环境。

## 工具

| 工具 | 功能 |
| --- | --- |
| `get_blender_context()` | 获取完整上下文、常用快捷键、当前按键前缀、下一步选项及模态状态。 |
| `suggest_shortcuts(query="", limit=12)` | 空闲时返回常用完整绑定，前缀和模态状态下返回完整匹配绑定，最多 50 条；支持中英文动作名、操作器、键位筛选。多个词需全部匹配；不是自然语言意图分析。 |
| `get_blender_connection_status()` | 检查连接、Blender 版本、采集序号、时间及当前编辑器。 |

所有工具均声明 `readOnlyHint=true`。返回值为 JSON 对象，同时提供 MCP 结构化输出。候选排序与键位由插件决定，采用实际用户配置。

完整快照中，`display_mode` 为 `common`、`prefix` 或 `modal`。`common` 状态下，`common_shortcuts` 为常用完整键位，`common_count` 为其长度；`next_steps` 为空、`candidate_count` 为 0。前缀和模态状态下，`common_shortcuts` 为空，`next_steps` 为当前层每个按键的独立记录，附带用途分组和共享说明的展示元数据；`candidate_count` 等于该列表长度。HUD 展示组数单独统计，每个键都保留。`recommendations` 在空闲时为常用完整绑定，在前缀或模态状态下为完整匹配绑定；`binding_count` 等于其长度。`modal` 记录实际模态操作，无模态操作时为 `null`。

常用列表从已知常用操作中结合编辑器、模式、选择状态与操作可用性筛选，只显示用户键位中已验证的实际主要绑定；未绑定的操作不显示，不用搜索文字替代键位，也不会任意截取前几项。空闲时不会展开全部插件键位；前缀匹配仍涵盖当前上下文中全部适用的实际绑定。

模态提示支持具有可查询关联模态键位的实际活动操作，如移动、旋转、缩放（通常为 `G / R / S`）。Blender 未公开原生模态键位的逐项可用状态和宏当前子操作，部分宏及操作内部特殊状态可能无法完整反映。底部提示条展开当前层全部选项；编辑区域过小、无法容纳时提示扩大编辑器。

鼠标移出 Blender 后，读取的是最后悬停的编辑器上下文（`tracking="last_hovered_editor"`）。按键状态来自 Blender 事件；`input` 字段记录当前前缀。`updated_at` 是 Unix 秒，`sequence` 是采集序号。

## 端点约定

环境变量 `BLENDER_SHORTCUT_TOKEN` 必填；`BLENDER_SHORTCUT_PORT` 默认 `18764`。仅允许向 `http://127.0.0.1:<port>/v1/context` 发起带 `Authorization: Bearer <token>` 的 GET 请求。禁用代理和重定向，超时为 2 秒，最大响应 2 MiB。

空闲状态的插件响应示意（省略每项的其他绑定与展示字段）：

```json
{
  "schema_version": 1,
  "status": "connected",
  "updated_at": 1791000000.0,
  "sequence": 42,
  "blender_version": "5.2.0 LTS",
  "tracking": "last_hovered_editor",
  "context": {
    "editor": "VIEW_3D",
    "editor_label_zh": "3D 视图",
    "mode": "EDIT_MESH",
    "mode_label_zh": "网格编辑",
    "object_type": "MESH",
    "object_name": "Cube",
    "selected_objects": 1,
    "selected_vertices": 4,
    "selected_edges": 4,
    "selected_faces": 1,
    "tool_label": "选择框"
  },
  "input": {
    "held_modifiers": [],
    "held_keys": [],
    "prefix_label": "",
    "active": false
  },
  "modal": null,
  "display_mode": "common",
  "candidate_count": 0,
  "binding_count": 1,
  "common_count": 1,
  "next_steps": [],
  "common_shortcuts": [
    {"action": "move", "label_zh": "移动", "label_en": "Move", "shortcut": "G"}
  ],
  "recommendations": [
    {"action": "move", "label_zh": "移动", "label_en": "Move", "shortcut": "G"}
  ]
}
```

以上用一项常用键演示字段关系，实际常用列表随上下文与用户键位变化。按住前缀后，`display_mode` 为 `prefix`，`next_steps` 包含当前可直接完成的操作和「更多组合(N)」分支；`recommendations` 保留完整组合及其 `suffix_shortcut`。实际模态操作中，`display_mode` 为 `modal`，各项来自该操作的实际模态键位。插件可增加其他字段，桥接保留完整快照的其他字段。

## 排障

- `missing_token`：MCP 进程没有接收到令牌。添加配置后重新加载连接。
- `authentication_failed`：令牌与插件不一致。复制当前令牌；更新配置并重新加载。
- `offline` / `timeout`：Blender 未运行、插件未启用或端口不同。核对插件端口与 `BLENDER_SHORTCUT_PORT`。
- `not_ready`：插件还没有可用快照。将鼠标移入 Blender 编辑器。
- `invalid_response` / `unsupported_schema`：端点响应不符合上述约定。确认该端口运行的是配套插件。

服务启动后等待客户端的 stdio 消息。原生悬浮提示可通过 Blender 顶部「窗口」菜单 →「快捷键助手：显示悬浮提示」开启；显示提示与启动 MCP 数据桥可以独立使用。标准输出专供 MCP 协议使用。

## 依赖与参考

使用官方 [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)，固定为 [mcp 1.30.0](https://pypi.org/project/mcp/1.30.0/) 的 FastMCP API。2.x 将 FastMCP 改名为 MCPServer，不能直接升级此依赖。Codex TOML 示例按官方 [MCP 配置文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) 编写。
