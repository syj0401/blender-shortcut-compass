param(
    [string]$PythonCommand = "python"
)

$ErrorActionPreference = "Stop"
$bridgeDirectory = $PSScriptRoot
$environmentDirectory = Join-Path $bridgeDirectory ".venv"
$environmentPython = Join-Path $environmentDirectory "Scripts\python.exe"
$requirementsPath = Join-Path $bridgeDirectory "requirements.txt"

if (-not (Test-Path -LiteralPath $environmentPython)) {
    & $PythonCommand -m venv $environmentDirectory
    if ($LASTEXITCODE -ne 0) { throw "创建 Python 虚拟环境失败。请安装 Python 3.10 或以上版本。" }
}

& $environmentPython -m pip install --timeout 120 --retries 3 -r $requirementsPath
if ($LASTEXITCODE -ne 0) { throw "安装 MCP 依赖失败。请检查网络和 Python 版本。" }

Write-Host "MCP 桥接依赖已安装。"
Write-Host "Python: $environmentPython"
Write-Host "Server: $(Join-Path $bridgeDirectory 'server.py')"
Write-Host "请按 config.example.toml 填写路径和 Blender 连接令牌。"
