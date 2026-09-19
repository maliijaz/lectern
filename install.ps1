<#
.SYNOPSIS
  Install and run Lectern on Windows. The only command you need.

.DESCRIPTION
  Run this once and it installs everything. Run it again and it just starts Lectern.
  There is deliberately no second way to do it.

    irm https://raw.githubusercontent.com/maliijaz/lectern/main/install.ps1 | iex

  Everything it installs is free and open source. It installs only what is missing, and
  it tells you what it is about to do before it does it.

  Deliberately ASCII-only. Windows PowerShell 5.1 reads a .ps1 as ANSI unless it carries
  a UTF-8 byte-order mark, so one stray em-dash turns into a pile of parse errors.

.PARAMETER Path
  Where to install. Defaults to a "lectern" folder in your home directory.

.PARAMETER Update
  Fetch the latest version and reinstall dependencies before starting.

.PARAMETER Share
  Start it on a public HTTPS link instead of locally, so a colleague can use it.
  Needs cloudflared; it will tell you how to get it.

.PARAMETER Model
  Which local model to use. The default is tuned for this workload.

.PARAMETER SkipModel
  Do not download a model. Use this to point Lectern at a hosted endpoint instead.

.PARAMETER Yes
  Do not ask anything. For scripted or unattended installs.
#>
param(
  [string]$Path = (Join-Path $HOME "lectern"),
  [switch]$Update,
  [switch]$Share,
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

function Test-Cmd($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Get-VenvPython($root) { return (Join-Path $root ".venv\Scripts\python.exe") }

# A finished install, as opposed to a folder that exists because a previous run died
# halfway. Both files have to be there or "already installed" is a lie that produces a
# confusing error two steps later.
function Test-Installed($root) {
  return (Test-Path (Get-VenvPython $root)) -and (Test-Path (Join-Path $root "backend\app\main.py"))
}

function Start-Lectern($root) {
  # -Share hands off to the tunnel task, which generates an access key and prints the
  # public URL. No browser is opened: the point of that mode is the link, not this
  # machine's screen.
  if ($Share) {
    Push-Location $root
    try { & (Join-Path $root "tasks.ps1") share } finally { Pop-Location }
    return
  }

  Write-Host @"

  Lectern is starting at http://127.0.0.1:8000
  Press Ctrl+C to stop it.

"@ -ForegroundColor Green

  Start-Job -ScriptBlock {
    # Wait for the port to answer rather than guessing, so the browser does not open on
    # a connection error when the first start is slow.
    for ($i = 0; $i -lt 60; $i++) {
      try {
        Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
        Start-Process "http://127.0.0.1:8000"
        return
      }
      catch { Start-Sleep -Seconds 1 }
    }
  } | Out-Null

  Push-Location $root
  try { & (Join-Path $root "tasks.ps1") serve } finally { Pop-Location }
}

# ---------------------------------------------------------------- already installed?
if ((Test-Installed $Path) -and -not $Update) {
  Write-Host "`n  Lectern is already installed in $Path" -ForegroundColor Cyan
  Write-Info "Run with -Update to fetch the latest version first."
  Start-Lectern $Path
  exit 0
}

Write-Host @"

  Lectern
  Slide decks, lecture notes and question papers, made on your own machine.

"@ -ForegroundColor Cyan

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
  # the rest of the script finds the new executable without needing a restart.
  $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
  $user = [Environment]::GetEnvironmentVariable("Path", "User")
  $env:Path = "$machine;$user"
  return $true
}

function Get-PythonVersion {
  # py -3 is the reliable launcher on Windows; bare `python` can be the Store stub that
  # only opens the Microsoft Store and exits, which would look like a working Python.
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
Write-Info $(if ($Update) { "Update Lectern in: $Path" } else { "Download Lectern to: $Path" })
Write-Info "Set up its Python environment and build the web interface"
if (-not $SkipModel) { Write-Info "Download the $Model model (about 5 GB, one time)" }
Write-Info "Start it and open your browser"

if (-not $Yes) {
  Write-Host ""
  # Read-Host throws outright when there is no console to read from, which is the normal
  # case for `irm ... | iex` under some hosts. Someone who typed the install command has
  # already said yes, so a prompt we cannot show is not a reason to fail.
  try {
    $answer = Read-Host "  Press Enter to continue, or type n to stop"
    if ($answer -match "^\s*n") { Write-Info "Stopped. Nothing was changed."; exit 0 }
  }
  catch { Write-Info "(no console to ask at - continuing)" }
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
Write-Head $(if ($Update) { "Updating Lectern" } else { "Getting Lectern" })
if (Test-Path (Join-Path $Path "backend")) {
  if ($haveGit -and (Test-Path (Join-Path $Path ".git"))) {
    Push-Location $Path
    try { git pull --ff-only 2>&1 | Out-Null; Write-Ok "updated to the latest version" }
    catch { Write-Warn2 "could not update; using what is there" }
    finally { Pop-Location }
  }
  else { Write-Ok "using the existing copy" }
}
elseif ($haveGit) {
  git clone --depth 1 --branch $Branch $Repo $Path 2>&1 | Out-Null
  Write-Ok "downloaded to $Path"
}
else {
  # No git, so take the zip. This is what keeps it to one command for someone who has
  # never installed a developer tool in their life.
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
  if ($have -match [regex]::Escape($Model)) { Write-Ok "$Model is already downloaded" }
  else {
    Write-Info "Pulling $Model - about 5 GB, so this is the slow part"
    ollama pull $Model
    Write-Ok "$Model ready"
  }
}

# ---------------------------------------------------------------- build
Write-Head "Setting up (a few minutes)"
Push-Location $Path
try { & (Join-Path $Path "tasks.ps1") setup } finally { Pop-Location }

# ---------------------------------------------------------------- a way back in
# Without this, "run it again" means remembering a path and a command. A Start Menu entry
# is what makes this feel like an installed application rather than a checkout.
Write-Head "Adding a Start Menu shortcut"
try {
  $programs = [Environment]::GetFolderPath("Programs")
  $lnk = Join-Path $programs "Lectern.lnk"
  $shell = New-Object -ComObject WScript.Shell
  $shortcut = $shell.CreateShortcut($lnk)
  $shortcut.TargetPath = "powershell.exe"
  $shortcut.Arguments = "-NoExit -ExecutionPolicy Bypass -File `"$(Join-Path $Path 'tasks.ps1')`" serve"
  $shortcut.WorkingDirectory = $Path
  $shortcut.Description = "Lectern - teaching materials, made on your own machine"
  $shortcut.Save()
  Write-Ok "search the Start Menu for Lectern"
}
catch { Write-Warn2 "could not create the shortcut; start it from the terminal instead" }

Write-Host @"

  Done. Lectern lives in $Path

  To start it again: search the Start Menu for Lectern, or run this same
  command again. Add -Update to get the latest version first.

"@ -ForegroundColor Green

Start-Lectern $Path
