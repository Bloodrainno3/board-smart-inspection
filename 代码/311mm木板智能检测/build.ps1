$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$workspaceRoot = Split-Path (Split-Path $projectRoot -Parent) -Parent
$softwareRoot = Join-Path $workspaceRoot '软件'
$python = Join-Path $softwareRoot 'venv/Scripts/python.exe'
& $python -m PyInstaller --noconfirm --distpath (Join-Path $softwareRoot '待发布') --workpath (Join-Path $softwareRoot '构建缓存/311mm木板智能检测') (Join-Path $projectRoot '智能检测.spec')
if ($LASTEXITCODE -ne 0) { throw '打包失败' }
