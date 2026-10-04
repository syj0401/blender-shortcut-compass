# 可选 MCP 接入

使用底部快捷键提示不需要 MCP。下面的步骤仅用于让 Codex 等客户端只读查询 Blender 当前上下文。

1. 在 Blender 的 N 侧栏 →「快捷键」→ 启动数据桥，默认地址为 `127.0.0.1:18764`。
2. 使用插件按钮复制连接令牌。不要把真实令牌上传到仓库。保存 Blender 偏好设置可跨重启复用令牌。
3. 在仓库的 `mcp_bridge` 目录执行 `./setup.ps1`，或按桥接 README 手动创建虚拟环境和安装依赖。
4. 将 `mcp_bridge/config.example.toml` 的配置合并到客户端配置中，替换为本机绝对路径和连接令牌。
5. 重新加载 MCP 连接，再调用 `get_blender_connection_status` 检查。

可用工具：

- `get_blender_connection_status`：检查连接。
- `get_blender_context`：读取编辑器、模式、选择、常用快捷键、当前层选项及模态状态。
- `suggest_shortcuts`：查询快捷键，支持按操作名称或键位筛选。

移动仓库或修改端口、令牌后，需要更新客户端配置。完整字段和排障说明见 [桥接 README](../mcp_bridge/README.md)。
