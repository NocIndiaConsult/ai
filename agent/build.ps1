$ErrorActionPreference = "Stop"

$tempHome = Join-Path $PSScriptRoot ".build-home"
New-Item -ItemType Directory -Force $tempHome | Out-Null
$env:HOME = $tempHome
$env:USERPROFILE = $tempHome
$env:LOCALAPPDATA = Join-Path $tempHome "LocalAppData"
$env:APPDATA = Join-Path $tempHome "RoamingAppData"
$env:TEMP = Join-Path $tempHome "Temp"
$env:TMP = $env:TEMP
$env:PYINSTALLER_CONFIG_DIR = Join-Path $tempHome ".pyinstaller"
New-Item -ItemType Directory -Force $env:LOCALAPPDATA | Out-Null
New-Item -ItemType Directory -Force $env:APPDATA | Out-Null
New-Item -ItemType Directory -Force $env:TEMP | Out-Null

python -m pip install -r "$PSScriptRoot\requirements.txt"
python -m pip install pyinstaller
python -m PyInstaller --clean --noconfirm --noconsole --onefile --noupx --name idea-agent-v23 `
  --paths "$PSScriptRoot" `
  --hidden-import webview `
  --hidden-import cache `
  --hidden-import client `
  --hidden-import local_polling `
  --hidden-import webui `
  --hidden-import webui2 `
  --hidden-import agent.cache `
  --hidden-import agent.client `
  --hidden-import agent.local_polling `
  --hidden-import agent.webui `
  --hidden-import agent.webui2 `
  "$PSScriptRoot\main.py"

$built = Join-Path $PSScriptRoot "..\dist\idea-agent-v23.exe"
$legacy = Join-Path $PSScriptRoot "..\dist\idea-agent.exe"
if (Test-Path $built) {
    Copy-Item $built $legacy -Force
}
