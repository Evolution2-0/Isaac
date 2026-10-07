# Isaac – Windows 11 Setup
[CmdletBinding()]
param([switch]$SkipPlaywright,[switch]$SkipOptionalMemory,[switch]$NoStart)
$ErrorActionPreference="Stop"; Set-StrictMode -Version Latest
$RepoRoot = if ($PSScriptRoot) { $PSScriptRoot } elseif ($MyInvocation.MyCommand.Path) { Split-Path -Parent $MyInvocation.MyCommand.Path } else { Get-Location }
Set-Location $RepoRoot

function Step([string]$m){Write-Host "";Write-Host "==> $m" -ForegroundColor Cyan}
function Fail([string]$m){Write-Host "ERROR: $m" -ForegroundColor Red; return}
function Has([string]$n){$null -ne (Get-Command $n -ErrorAction SilentlyContinue)}

function Refresh-EnvironmentPath {
 try {
  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
  if ($userPath -or $machinePath) {
   $env:Path = "$userPath;$machinePath"
  }
 } catch {}
}

function Find-PythonExecutable {
 Refresh-EnvironmentPath
 $candidates = [System.Collections.Generic.List[string]]::new()
 foreach($c in @("py","python","python3")){ $candidates.Add($c) }
 $knownPaths = @(
  "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
  "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
  "$env:ProgramFiles\Python313\python.exe",
  "$env:ProgramFiles\Python312\python.exe",
  "C:\Python313\python.exe",
  "C:\Python312\python.exe"
 )
 foreach($exe in $knownPaths){
  if(Test-Path $exe){ $candidates.Add($exe) }
 }
 foreach($candidate in $candidates){
  if((Has $candidate) -or (Test-Path $candidate)){
   try {
    $v = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
    $p = $v.Trim().Split(".")
    if([int]$p[0] -gt 3 -or ([int]$p[0] -eq 3 -and [int]$p[1] -ge 12)){
     return $candidate
    }
   } catch {}
  }
 }
 return $null
}

if([Environment]::OSVersion.Platform -ne "Win32NT"){Write-Host "ERROR: Dieses Setup ist für Windows vorgesehen." -ForegroundColor Red; exit}
if(-not [Environment]::Is64BitOperatingSystem){Write-Host "ERROR: Isaac benötigt 64-Bit-Windows." -ForegroundColor Red; exit}

Step "Prüfe Python 3.12+"
$python = Find-PythonExecutable

if($null -eq $python -and (Has "winget")){
 Step "Python fehlt – Installation über winget"
 & winget install --id Python.Python.3.12 --exact --accept-package-agreements --accept-source-agreements --silent
 $python = Find-PythonExecutable
}

if($null -eq $python){Write-Host "ERROR: Python 3.12+ wurde nicht gefunden." -ForegroundColor Red; exit}
Write-Host "Python: $((& $python --version).Trim()) ($python)"

$VenvPath = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
# Never reuse a packaged/foreign venv (for example a GitHub Actions hostedtoolcache path).
$RecreateVenv = $false
if(Test-Path $VenvPython){
  try { & $VenvPython -c "import sys; print(sys.executable)" *> $null; if($LASTEXITCODE -ne 0){$RecreateVenv=$true} } catch {$RecreateVenv=$true}
}
if($RecreateVenv){
  Step "Entferne inkompatible virtuelle Umgebung"
  Remove-Item -Recurse -Force $VenvPath
}
if(-not(Test-Path $VenvPath)){& $python -m venv $VenvPath}
if(-not(Test-Path $VenvPython)){Write-Host "ERROR: Virtuelle Umgebung konnte nicht erstellt werden." -ForegroundColor Red; exit}
try { & $VenvPython -c "import sys; print(sys.executable)" *> $null; if($LASTEXITCODE -ne 0){Write-Host "ERROR: Virtuelle Umgebung ist nicht lauffähig." -ForegroundColor Red; exit} } catch {Write-Host "ERROR: Virtuelle Umgebung ist nicht lauffähig." -ForegroundColor Red; exit}

Step "Installiere Isaac-Abhängigkeiten"
& $VenvPython -m pip install --upgrade pip setuptools wheel
& $VenvPython -m pip install -r (Join-Path $RepoRoot "requirements.txt")
if(-not $SkipOptionalMemory){Write-Host "Optionale Memory-Adapter bleiben deaktiviert; Isaac Core benötigt sie nicht."}
if(-not $SkipPlaywright){Step "Installiere Playwright Chromium";& $VenvPython -m playwright install chromium}

Step "Erzeuge Runtime-Verzeichnisse"
foreach($d in @("data","logs","runtime","workspace","traces")){New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot $d)|Out-Null}

$EnvPath = Join-Path $RepoRoot ".env"
if(-not(Test-Path $EnvPath)){
  $EnvContent = @'
# Isaac – lokale Windows-Konfiguration
ISAAC_BIND_HOST=127.0.0.1
PORT=5000
MONITOR_PORT=8765
MONITOR_HTTP_PORT=8766
ISAAC_DISABLE_VECTOR_MEMORY=1
ISAAC_COMPUTER_USE_ENABLED=1
ISAAC_COMPUTER_USE_LIVE=1
ISAAC_BROWSER_AUTOMATION=1
ISAAC_BROWSER_EXTERNAL_SITES=1
ISAAC_PRIVILEGE_MODE=user
# ACTIVE_PROVIDER=ollama
# OLLAMA_HOST=http://127.0.0.1:11434
# OPENROUTER_API_KEY=
# GROQ_API_KEY=
# GOOGLE_API_KEY=
'@
  Set-Content -Path $EnvPath -Value $EnvContent -Encoding UTF8
}

Step "Validiere Isaac"
& $VenvPython -c "import flask, aiohttp, websockets, dotenv; import app; print('Isaac Python-Import: OK')"
& $VenvPython -m py_compile isaac_core.py executor.py low_complexity.py memory.py relay.py logic.py watchdog.py task_checkpoint.py
$env:ISAAC_DISABLE_VECTOR_MEMORY="1"
& $VenvPython -m unittest tests_provider_configuration -q

Write-Host "Windows-spezifische Basisvalidierung erfolgreich." -ForegroundColor Green
Write-Host "Hinweis: plattformgebundene Unix-Permission-Tests laufen weiterhin in der Linux-CI."
Write-Host "Start: .\start_isaac_windows.cmd"
Write-Host "API:   http://127.0.0.1:5000"
Write-Host "UI:    http://127.0.0.1:8766"

if(-not $NoStart){& (Join-Path $RepoRoot "start_isaac_windows.cmd")}
