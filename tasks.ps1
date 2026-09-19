<#
.SYNOPSIS
  Development and setup tasks for Lectern.

.DESCRIPTION
  Deliberately ASCII-only. Windows PowerShell 5.1 reads a .ps1 file as ANSI unless it
  carries a UTF-8 byte-order mark, so a stray em-dash or arrow in a string turns into
  seven parse errors and the script will not run at all. Keep it to plain ASCII.

.EXAMPLE
  .\tasks.ps1 setup      # create venv, install everything (GPU build where possible)
  .\tasks.ps1 dev        # run the API and the web UI with hot reload
  .\tasks.ps1 gpu        # show the GPU the app can see, and how it will be used
  .\tasks.ps1 test       # run the test suite
#>
param(
  [Parameter(Position = 0)]
  [ValidateSet("setup", "dev", "serve", "share", "test", "lint", "build", "clean", "gpu", "ollama", "help")]
  [string]$Task = "help"
)

$ErrorActionPreference = "Stop"

# The console defaults to the legacy codepage, which renders anything outside ASCII as a
# replacement character - including the em-dashes the app prints. Child processes need
# telling separately, hence PYTHONIOENCODING.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONIOENCODING = "utf-8"

$Root = $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"

function Write-Step($message) { Write-Host "`n>> $message" -ForegroundColor Cyan }
function Write-Ok($message) { Write-Host "   $message" -ForegroundColor Green }
function Write-Warn($message) { Write-Host "   $message" -ForegroundColor Yellow }

function Assert-Venv {
  if (-not (Test-Path $Python)) {
    Write-Warn "No virtual environment found. Run: .\tasks.ps1 setup"
    exit 1
  }
}

# Extras are quoted because PowerShell parses bare square brackets as an index expression.
$IngestExtra = "$Root\backend[ingest,media]"
$DevExtra = "$Root\backend[dev]"

switch ($Task) {

  "setup" {
    Write-Step "Creating the Python environment"
    if (-not (Test-Path $Python)) { python -m venv $Venv }
    & $Python -m pip install --quiet --upgrade pip
    Write-Ok "virtual environment ready"

    Write-Step "Installing the backend"
    & $Python -m pip install -e $DevExtra
    Write-Ok "backend installed"

    # PyTorch goes in first, from the CUDA index. Installing it afterwards is not enough:
    # Docling requires torch>=2.2.2 with no upper bound, so pip will happily "upgrade" a
    # working CUDA build to the latest CPU-only wheel and leave the GPU idle with no
    # warning. The constraints file below is what stops that. Indexing is about six times
    # faster on CUDA (measured: 5.9 -> 39 passages/sec on an RTX 4060).
    $cuda = $false
    $constraints = Join-Path $env:TEMP "ta-torch-constraints.txt"
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
      $gpu = nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1
      if ($gpu) {
        Write-Step "Found $gpu - installing the CUDA build of PyTorch (large download)"
        try {
          & $Python -m pip install torch --index-url https://download.pytorch.org/whl/cu121
          $version = & $Python -c "import torch; print(torch.__version__)"
          Set-Content -Path $constraints -Value "torch==$version" -Encoding ascii
          $cuda = $true
          Write-Ok "PyTorch $version installed and pinned"
        }
        catch {
          Write-Warn "The CUDA build failed; falling back to the CPU build."
        }
      }
    }
    if (-not $cuda) { Write-Warn "No NVIDIA GPU detected - PyTorch will run on the CPU." }

    Write-Step "Installing document parsing and search"
    try {
      if ($cuda) {
        & $Python -m pip install -e $IngestExtra -c $constraints `
          --extra-index-url https://download.pytorch.org/whl/cu121
      }
      else {
        & $Python -m pip install -e $IngestExtra
      }
      Write-Ok "document parsing ready"
    }
    catch {
      Write-Warn "Optional extras failed. The app still runs; you just cannot upload PDFs"
      Write-Warn "until you run: .venv\Scripts\pip install -e 'backend[ingest]'"
    }

    if ($cuda) {
      Write-Step "Confirming the GPU survived the install"
      $stillCuda = & $Python -c "import torch; print(torch.cuda.is_available())"
      if ($stillCuda -match "True") { Write-Ok "PyTorch is still using the GPU" }
      else {
        Write-Warn "A dependency replaced PyTorch with the CPU build. Restore it with:"
        Write-Warn "  .venv\Scripts\pip install --force-reinstall torch --index-url https://download.pytorch.org/whl/cu121"
      }
    }

    Write-Step "Installing the web UI"
    Push-Location (Join-Path $Root "frontend")
    try { npm install --no-audit --no-fund } finally { Pop-Location }
    Write-Ok "web UI ready"

    if (-not (Test-Path (Join-Path $Root ".env"))) {
      Copy-Item (Join-Path $Root ".env.example") (Join-Path $Root ".env")
      Write-Ok "created .env from the example"
    }

    Write-Step "Checking for Ollama"
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
      Write-Ok "Ollama is installed"
      $models = (ollama list 2>$null) -join "`n"
      if ($models -notmatch "\S") { Write-Warn "No models yet. Pull one: ollama pull qwen3:8b" }
    }
    else {
      Write-Warn "Ollama is not installed. Get it from https://ollama.com,"
      Write-Warn "then run: ollama pull qwen3:8b"
      Write-Warn "(Or point the app at any OpenAI-compatible endpoint in Settings.)"
    }

    Write-Host "`nReady. Start it with: .\tasks.ps1 dev`n" -ForegroundColor Green
  }

  "dev" {
    Assert-Venv
    Write-Step "Starting the API on http://127.0.0.1:8000"
    $api = Start-Process -FilePath $Python `
      -ArgumentList "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000" `
      -WorkingDirectory (Join-Path $Root "backend") -PassThru -NoNewWindow

    Write-Step "Starting the web UI on http://127.0.0.1:5173"
    try {
      Push-Location (Join-Path $Root "frontend")
      npm run dev
    }
    finally {
      Pop-Location
      if ($api -and -not $api.HasExited) {
        Write-Step "Stopping the API"
        Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue
      }
    }
  }

  "serve" {
    Assert-Venv
    if (-not (Test-Path (Join-Path $Root "frontend\dist\index.html"))) {
      Write-Step "Building the web UI first"
      Push-Location (Join-Path $Root "frontend")
      try { npm run build } finally { Pop-Location }
    }
    Write-Step "Lectern on http://127.0.0.1:8000"
    Push-Location (Join-Path $Root "backend")
    try { & $Python -m uvicorn app.main:app --port 8000 } finally { Pop-Location }
  }

  "share" {
    # Puts this machine's Lectern on a public HTTPS URL through a Cloudflare quick
    # tunnel: no account, no card, no port forwarding, no signup. Unlike a free hosting
    # tier this is the whole product - your GPU, your documents, your files kept on
    # disk - for as long as you leave it running.
    #
    # The URL is genuinely public and the app has no login, so this refuses to start
    # without an access key and generates one if you did not set it.
    Assert-Venv
    if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
      Write-Warn "cloudflared is not installed. Install it with:"
      Write-Host "   winget install --id Cloudflare.cloudflared"
      exit 1
    }

    if (-not $env:LECTERN_ACCESS_KEY) {
      $bytes = New-Object byte[] 18
      [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
      $env:LECTERN_ACCESS_KEY = ([Convert]::ToBase64String($bytes)) -replace "[+/=]", ""
      Write-Warn "No LECTERN_ACCESS_KEY was set, so one was generated for this session."
    }
    Write-Ok ("access key: " + $env:LECTERN_ACCESS_KEY)
    Write-Host ("   Share the tunnel URL with ?key=" + $env:LECTERN_ACCESS_KEY + " on the end.")
    Write-Host "   It is swapped for a cookie on first load, so it is sent only once."

    if (-not (Test-Path (Join-Path $Root "frontend/dist/index.html"))) {
      Write-Step "Building the web UI first"
      Push-Location (Join-Path $Root "frontend")
      try { npm run build } finally { Pop-Location }
    }

    Write-Step "Starting Lectern on 127.0.0.1:8000"
    $api = Start-Process -FilePath $Python -PassThru -NoNewWindow `
      -WorkingDirectory (Join-Path $Root "backend") `
      -ArgumentList "-m", "uvicorn", "app.main:app", "--port", "8000"
    try {
      Start-Sleep -Seconds 3
      Write-Step "Opening the tunnel - the trycloudflare.com URL below is your address"
      cloudflared tunnel --url http://127.0.0.1:8000
    }
    finally {
      if ($api -and -not $api.HasExited) {
        Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue
      }
      Write-Ok "stopped - that URL is now dead"
    }
  }

  "test" {
    Assert-Venv
    Push-Location (Join-Path $Root "backend")
    try { & $Python -m pytest app/tests -q } finally { Pop-Location }
  }

  "lint" {
    Assert-Venv
    Push-Location (Join-Path $Root "backend")
    try {
      & $Python -m ruff check app
      & $Python -m ruff format --check app
    }
    finally { Pop-Location }
    Push-Location (Join-Path $Root "frontend")
    try { npm run lint } finally { Pop-Location }
  }

  "build" {
    Push-Location (Join-Path $Root "frontend")
    try { npm run build } finally { Pop-Location }
    Write-Ok "web UI built to frontend\dist"
  }

  "gpu" {
    Assert-Venv
    Write-Step "What the graphics driver reports"
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
      nvidia-smi --query-gpu=name,memory.total,memory.free,utilization.gpu --format=csv
    }
    else {
      Write-Warn "nvidia-smi not found - no NVIDIA GPU, or the driver is not installed."
    }

    Write-Step "What the app will do with it"
    Push-Location (Join-Path $Root "backend")
    try {
      & $Python -c "from app.core import hardware; [print(f'  {k}: {v}') for k, v in hardware.report().items()]"
    }
    finally { Pop-Location }

    Write-Step "How the loaded model is placed right now"
    try {
      $ps = Invoke-RestMethod -Uri "http://localhost:11434/api/ps" -TimeoutSec 5
      if ($ps.models.Count -eq 0) { Write-Warn "No model loaded. Generate something first." }
      foreach ($m in $ps.models) {
        $share = if ($m.size) { 100 * $m.size_vram / $m.size } else { 0 }
        $line = "  {0}: {1:N1}% on GPU ({2:N2} of {3:N2} GB)" -f $m.name, $share, ($m.size_vram / 1e9), ($m.size / 1e9)
        if ($share -gt 99.5) { Write-Host $line -ForegroundColor Green }
        else { Write-Host "$line  <- the rest runs on the CPU and will be slow" -ForegroundColor Yellow }
      }
    }
    catch { Write-Warn "Ollama is not reachable, so model placement is unknown." }
  }

  "clean" {
    Write-Step "Removing build output and caches"
    foreach ($path in @("frontend\dist", "frontend\node_modules\.vite", "backend\.pytest_cache",
        "backend\.ruff_cache")) {
      $full = Join-Path $Root $path
      if (Test-Path $full) { Remove-Item -Recurse -Force $full }
    }
    Get-ChildItem -Path $Root -Include "__pycache__" -Recurse -Directory -ErrorAction SilentlyContinue |
      Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Ok "cleaned (your data/ directory was left alone)"
  }

  "ollama" {
    Write-Step "Pulling a model suited to this workload"
    ollama pull qwen3:8b
    Write-Ok "done - select it in Settings"
  }

  default {
    Write-Host @"

Lectern

  .\tasks.ps1 setup     Install everything (uses the GPU build of PyTorch when possible)
  .\tasks.ps1 dev       Run the API and the web UI with hot reload
  .\tasks.ps1 serve     Run the built app on http://127.0.0.1:8000
  .\tasks.ps1 share     Put this machine on a public URL via a Cloudflare tunnel
  .\tasks.ps1 build     Build the web UI for production
  .\tasks.ps1 test      Run the test suite
  .\tasks.ps1 lint      Check formatting and types
  .\tasks.ps1 gpu       Show the GPU the app can see, and how the model is placed
  .\tasks.ps1 ollama    Pull a suitable local model
  .\tasks.ps1 clean     Remove build output (leaves your data alone)

Command line:
  .venv\Scripts\lectern --help

"@ -ForegroundColor Cyan
  }
}
