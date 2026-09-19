<#
.SYNOPSIS
  One-command install for Lectern on Windows.

.DESCRIPTION
  Run this and you get a working Lectern: the app, its dependencies, a local AI model,
  and a browser open on it. Nothing to configure.

    irm https://raw.githubusercontent.com/maliijaz/lectern/main/install.ps1 | iex

  Everything it installs is free and open source. It installs only what is missing, and
  it tells you what it is about to do before it does it.

  Deliberately ASCII-only. Windows PowerShell 5.1 reads a .ps1 as ANSI unless it carries
  a UTF-8 byte-order mark, so one stray em-dash turns into a pile of parse errors.

.PARAMETER Path
  Where to install. Defaults to a "lectern" folder in your home directory.

.PARAMETER Model
  Which local model to pull. The default is tuned for this workload.

.PARAMETER SkipModel
  Do not download a model. Use this if you plan to point Lectern at a hosted endpoint.

.PARAMETER Yes
  Do not ask anything. For scripted or unattended installs.
#>
param(
  [string]$Path = (Join-Path $HOME "lectern"),
  [string]$Model = "qwen3:8b",
  [switch]$SkipModel,
  [switch]$Yes
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Repo = "https://github.com/maliijaz/lectern"
$Branch = "main"

function Write-Head($m) { Write-Host "`n$m" -ForegroundColor Cyan }
function Write-Ok($m) { Write-Host "  [ok] $m" -ForegroundColor Green }
function Write-Info($m) { Write-Host "  $m" -ForegroundColor Gray }
function Write-Warn2($m) { Write-Host "  [!] $m" -ForegroundColor Yellow }
function Write-Bad($m) { Write-Host "  [x] $m" -ForegroundColor Red }

function Test-Cmd($name) {
  return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

# winget is how anything missing gets installed. Without it we can still proceed, but the
# user has to install the prerequisites themselves, so say so clearly rather than failing
# five steps later with a confusing error.
function Install-With-Winget($id, $label) {
  if (-not (Test-Cmd "winget")) {
    Write-Bad "$label is missing and winget is not available to install it."
    Write-Info "Install $label by hand, then run this again."
    return $false
  }
  Write-Info "Installing $label (this can take a few minutes)..."
  winget install --id $id --accept-package-agreements --accept-source-agreements `
    --silent --disable-interactivity | Out-Null

  # A fresh install is not on PATH in this process yet. Re-read it from the registry so
  # the rest of the script can find the new executable without a restart.
  $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
  $user = [Environment]::GetEnvironmentVariable("Path", "User")
  $env:Path = "$machine;$user"
  return $true
}

function Get-PythonVersion {
  # py -3 is the reliable launcher on Windows; `python` can be the Store stub that only
  # opens the Microsoft Store and exits, which would look like a working Python here.
  foreach ($exe in @("py", "python")) {
    if (-not (Test-Cmd $exe)) { continue }
    $args = if ($exe -eq "py") { @("-3", "-c", "import sys; print(sys.version_info[:2])") }
            else { @("-c", "import sys; print(sys.version_info[:2])") }
    try {
      $out = & $exe @args 2>$null
      if ($out -match "\((\d+),\s*(\d+)\)") {
        return @{ Exe = $exe; Major = [int]$Matches[1]; Minor = [int]$Matches[2] }
      }
    }
    catch { }
  }
  return $null
}

Write-Host @"

  Lectern
  Slide decks, lecture notes and question papers, made on your own machine.

"@ -ForegroundColor Cyan

# ---------------------------------------------------------------- what is already here
Write-Head "Checking what you already have"

$py = Get-PythonVersion
$havePython = $py -and ($py.Major -gt 3 -or ($py.Major -eq 3 -and $py.Minor -ge 11))
if ($havePython) { Write-Ok "Python $($py.Major).$($py.Minor)" }
elseif ($py) { Write-Warn2 "Python $($py.Major).$($py.Minor) is too old; 3.11 or newer is needed" }
else { Write-Warn2 "Python is not installed" }

$haveNode = Test-Cmd "node"
if ($haveNode) { Write-Ok "Node $(node --version)" } else { Write-Warn2 "Node is not installed" }

$haveOllama = Test-Cmd "ollama"
if ($haveOllama) { Write-Ok "Ollama" } else { Write-Warn2 "Ollama is not installed" }

$haveGit = Test-Cmd "git"

# ---------------------------------------------------------------- the plan
$todo = @()
if (-not $havePython) { $todo += "Python 3.12" }
if (-not $haveNode) { $todo += "Node.js LTS" }
if (-not $haveOllama -and -not $SkipModel) { $todo += "Ollama" }

Write-Head "The plan"
if ($todo.Count) { Write-Info ("Install: " + ($todo -join ", ")) }
Write-Info "Download Lectern to: $Path"
Write-Info "Set up its Python environment and build the web interface"
if (-not $SkipModel) {
  Write-Info "Download the $Model model (about 5 GB, one time)"
}
Write-Info "Start it and open your browser"

if (-not $Yes) {
  Write-Host ""
  # Read-Host throws outright when there is no console to read from, which is the normal
  # case for `irm ... | iex` under some hosts and for any scripted run. Someone who typed
  # the install command has already said yes, so a prompt we cannot show is not a reason
  # to fail - carry on.
  try {
    $answer = Read-Host "  Press Enter to continue, or type n to stop"
    if ($answer -match "^\s*n") { Write-Info "Stopped. Nothing was changed."; exit 0 }
  }
  catch {
    Write-Info "(no console to ask at - continuing)"
  }
}

# ---------------------------------------------------------------- prerequisites
if (-not $havePython) {
  Write-Head "Installing Python"
  if (-not (Install-With-Winget "Python.Python.3.12" "Python 3.12")) { exit 1 }
  $py = Get-PythonVersion
  if (-not $py) { Write-Bad "Python still not found. Open a new terminal and run this again."; exit 1 }
  Write-Ok "Python $($py.Major).$($py.Minor)"
}

if (-not $haveNode) {
  Write-Head "Installing Node.js"
  if (-not (Install-With-Winget "OpenJS.NodeJS.LTS" "Node.js LTS")) { exit 1 }
  if (-not (Test-Cmd "node")) { Write-Bad "Node still not found. Open a new terminal and run this again."; exit 1 }
  Write-Ok "Node $(node --version)"
}

if (-not $haveOllama -and -not $SkipModel) {
  Write-Head "Installing Ollama"
  if (Install-With-Winget "Ollama.Ollama" "Ollama") {
    if (Test-Cmd "ollama") { Write-Ok "Ollama installed" }
    else { Write-Warn2 "Ollama installed but not on PATH yet; skipping the model for now."; $SkipModel = $true }
  }
  else { $SkipModel = $true }
}

# ---------------------------------------------------------------- the code
Write-Head "Getting Lectern"
if (Test-Path (Join-Path $Path "backend")) {
  Write-Info "Already there - updating it"
  if ($haveGit -and (Test-Path (Join-Path $Path ".git"))) {
    Push-Location $Path
    try { git pull --ff-only 2>&1 | Out-Null; Write-Ok "updated" } catch { Write-Warn2 "could not update; using what is there" } finally { Pop-Location }
  }
  else { Write-Ok "using the existing copy" }
}
elseif ($haveGit) {
  git clone --depth 1 --branch $Branch $Repo $Path 2>&1 | Out-Null
  Write-Ok "cloned to $Path"
}
else {
  # No git, so take the zip. This is what keeps the whole thing to one command for
  # someone who has never installed a developer tool in their life.
  Write-Info "git is not installed, downloading the zip instead"
  $zip = Join-Path $env:TEMP "lectern.zip"
  $tmp = Join-Path $env:TEMP "lectern-unzip"
  Invoke-WebRequest -Uri "$Repo/archive/refs/heads/$Branch.zip" -OutFile $zip -UseBasicParsing
  if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
  Expand-Archive -Path $zip -DestinationPath $tmp -Force
  $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
  New-Item -ItemType Directory -Force -Path (Split-Path $Path -Parent) | Out-Null
  Move-Item $inner.FullName $Path
  Remove-Item -Force $zip
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
  Write-Ok "downloaded to $Path"
}

# ---------------------------------------------------------------- the model
if (-not $SkipModel -and (Test-Cmd "ollama")) {
  Write-Head "Getting the AI model"
  $have = (ollama list 2>$null) -join "`n"
  if ($have -match [regex]::Escape($Model)) {
    Write-Ok "$Model is already downloaded"
  }
  else {
    Write-Info "Pulling $Model - about 5 GB, so this is the slow part"
    ollama pull $Model
    Write-Ok "$Model ready"
  }
}

# ---------------------------------------------------------------- build
Write-Head "Setting up (a few minutes)"
Push-Location $Path
try {
  & (Join-Path $Path "tasks.ps1") setup
}
finally { Pop-Location }

# ---------------------------------------------------------------- go
Write-Host @"

  Done.

  Lectern is at http://127.0.0.1:8000
  Your copy lives in $Path

  To start it again later:
      cd $Path
      .\tasks.ps1 serve

  To put it on a public link for a colleague:
      .\tasks.ps1 share

"@ -ForegroundColor Green

Start-Job -ScriptBlock {
  Start-Sleep -Seconds 6
  Start-Process "http://127.0.0.1:8000"
} | Out-Null

Push-Location $Path
try { & (Join-Path $Path "tasks.ps1") serve } finally { Pop-Location }
