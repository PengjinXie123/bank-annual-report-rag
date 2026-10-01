param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$coursePython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $coursePython)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { throw '请先按 README 安装 Python 和依赖。' }
    $coursePython = $pythonCommand.Source
}
$env:PYTHONIOENCODING = 'utf-8'
Set-Location -LiteralPath $PSScriptRoot
& $coursePython (Join-Path $PSScriptRoot 'web_server.py') --port $Port
