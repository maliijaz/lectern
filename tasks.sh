#!/usr/bin/env bash
# Development and setup tasks for Lectern on macOS and Linux.
#
# This is not how you install Lectern - install.sh is, and it calls this underneath.
# These commands assume a checkout you are working on.
#
# The POSIX half of tasks.ps1. The two are kept deliberately in step: same task names,
# same output, same behaviour, so the README can give one instruction to everybody and
# only the file extension differs.
#
#   ./tasks.sh setup     install everything
#   ./tasks.sh serve     run it on http://127.0.0.1:8000
#   ./tasks.sh share     put this machine on a public URL
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"

step() { printf '\n\033[36m>> %s\033[0m\n' "$1"; }
ok() { printf '\033[32m   %s\033[0m\n' "$1"; }
warn() { printf '\033[33m   %s\033[0m\n' "$1"; }

need_venv() {
  if [ ! -x "$PY" ]; then
    warn "No virtual environment found. Run: ./tasks.sh setup"
    exit 1
  fi
}

# The python to build the venv with. Prefer a named 3.1x so an old default python3 on
# macOS does not silently produce an environment the app cannot run in.
pick_python() {
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

build_ui_if_needed() {
  if [ ! -f "$ROOT/frontend/dist/index.html" ]; then
    step "Building the web UI first"
    (cd "$ROOT/frontend" && npm run build)
  fi
}

case "${1:-help}" in

setup)
  step "Creating the Python environment"
  if [ ! -x "$PY" ]; then
    PYBIN="$(pick_python)" || { warn "Python 3.11 or newer is required."; exit 1; }
    "$PYBIN" -m venv "$VENV"
  fi
  "$PY" -m pip install --quiet --upgrade pip
  ok "virtual environment ready"

  step "Installing the backend"
  "$PY" -m pip install -e "$ROOT/backend[dev]"
  ok "backend installed"

  # PyTorch first, from the CUDA index where there is an NVIDIA card. Installing it
  # afterwards is not enough: Docling requires torch>=2.2.2 with no upper bound, so pip
  # will happily replace a working CUDA build with a CPU wheel and leave the GPU idle.
  # The constraints file is what prevents that. Apple silicon uses MPS from the normal
  # wheel, so it needs none of this.
  CUDA=0
  CONSTRAINTS="$(mktemp)"
  if command -v nvidia-smi >/dev/null 2>&1; then
    GPU="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n1 || true)"
    if [ -n "$GPU" ]; then
      step "Found $GPU - installing the CUDA build of PyTorch (large download)"
      if "$PY" -m pip install torch --index-url https://download.pytorch.org/whl/cu121; then
        echo "torch==$("$PY" -c 'import torch; print(torch.__version__)')" > "$CONSTRAINTS"
        CUDA=1
        ok "PyTorch pinned to the CUDA build"
      else
        warn "The CUDA build failed; falling back to the CPU build."
      fi
    fi
  fi

  step "Installing document parsing and search"
  if [ "$CUDA" = "1" ]; then
    "$PY" -m pip install -e "$ROOT/backend[ingest,media]" -c "$CONSTRAINTS" \
      --extra-index-url https://download.pytorch.org/whl/cu121 || warn "Optional extras failed; PDF upload will be unavailable."
  else
    "$PY" -m pip install -e "$ROOT/backend[ingest,media]" || warn "Optional extras failed; PDF upload will be unavailable."
  fi
  ok "document parsing ready"

  if [ "$CUDA" = "1" ]; then
    step "Confirming the GPU survived the install"
    if "$PY" -c 'import torch; print(torch.cuda.is_available())' | grep -qi true; then
      ok "PyTorch is still using the GPU"
    else
      warn "A dependency replaced PyTorch with the CPU build. Restore it with:"
      warn "  .venv/bin/pip install --force-reinstall torch --index-url https://download.pytorch.org/whl/cu121"
    fi
  fi

  step "Installing the web UI"
  (cd "$ROOT/frontend" && npm install --no-audit --no-fund)
  ok "web UI ready"

  [ -f "$ROOT/.env" ] || { cp "$ROOT/.env.example" "$ROOT/.env"; ok "created .env from the example"; }

  step "Checking for Ollama"
  if command -v ollama >/dev/null 2>&1; then
    ok "Ollama is installed"
    ollama list 2>/dev/null | grep -q . || warn "No models yet. Pull one: ollama pull qwen3:8b"
  else
    warn "Ollama is not installed. Get it from https://ollama.com, then:"
    warn "  ollama pull qwen3:8b"
  fi

  printf '\n\033[32mReady. Start it with: ./tasks.sh serve\033[0m\n\n'
  ;;

dev)
  need_venv
  step "Starting the API on http://127.0.0.1:8000"
  (cd "$ROOT/backend" && "$PY" -m uvicorn app.main:app --reload --port 8000) &
  API=$!
  trap 'kill $API 2>/dev/null || true' EXIT
  step "Starting the web UI on http://127.0.0.1:5173"
  (cd "$ROOT/frontend" && npm run dev)
  ;;

serve)
  need_venv
  build_ui_if_needed
  step "Lectern on http://127.0.0.1:8000"
  cd "$ROOT/backend" && exec "$PY" -m uvicorn app.main:app --port 8000
  ;;

share)
  # A public HTTPS URL for this machine through a Cloudflare quick tunnel: no account,
  # no card, no port forwarding. The whole product, including the GPU, for as long as
  # this stays running. The URL is genuinely public and the app has no login, so this
  # refuses to start without an access key.
  need_venv
  if ! command -v cloudflared >/dev/null 2>&1; then
    warn "cloudflared is not installed. Install it with:"
    warn "  brew install cloudflared        (macOS)"
    warn "  https://pkg.cloudflare.com      (Linux)"
    exit 1
  fi
  if [ -z "${LECTERN_ACCESS_KEY:-}" ]; then
    LECTERN_ACCESS_KEY="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(18).replace("-","").replace("_",""))')"
    export LECTERN_ACCESS_KEY
    warn "No LECTERN_ACCESS_KEY was set, so one was generated for this session."
  fi
  ok "access key: $LECTERN_ACCESS_KEY"
  printf '   Share the tunnel URL with ?key=%s on the end.\n' "$LECTERN_ACCESS_KEY"
  printf '   It is swapped for a cookie on first load, so it is sent only once.\n'

  build_ui_if_needed
  step "Starting Lectern on 127.0.0.1:8000"
  (cd "$ROOT/backend" && "$PY" -m uvicorn app.main:app --port 8000) &
  API=$!
  trap 'kill $API 2>/dev/null || true; printf "\033[32m   stopped - that URL is now dead\033[0m\n"' EXIT
  sleep 3
  step "Opening the tunnel - the trycloudflare.com URL below is your address"
  cloudflared tunnel --url http://127.0.0.1:8000
  ;;

test)
  need_venv
  cd "$ROOT/backend" && exec "$PY" -m pytest app/tests -q
  ;;

lint)
  need_venv
  (cd "$ROOT/backend" && "$PY" -m ruff check app && "$PY" -m ruff format --check app)
  (cd "$ROOT/frontend" && npm run lint)
  ;;

build)
  (cd "$ROOT/frontend" && npm run build)
  ok "web UI built to frontend/dist"
  ;;

gpu)
  need_venv
  step "What the graphics driver reports"
  if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name,memory.total,memory.free,utilization.gpu --format=csv
  else
    warn "nvidia-smi not found - no NVIDIA GPU, or the driver is not installed."
  fi
  step "What the app will do with it"
  (cd "$ROOT/backend" && "$PY" -c "from app.core import hardware; [print(f'  {k}: {v}') for k, v in hardware.report().items()]")
  ;;

ollama)
  step "Pulling a model suited to this workload"
  ollama pull qwen3:8b
  ok "done - select it in Settings"
  ;;

clean)
  step "Removing build output and caches"
  rm -rf "$ROOT/frontend/dist" "$ROOT/frontend/node_modules/.vite" \
    "$ROOT/backend/.pytest_cache" "$ROOT/backend/.ruff_cache"
  find "$ROOT" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
  ok "cleaned (your data/ directory was left alone)"
  ;;

*)
  cat <<'EOF'

Lectern

  ./tasks.sh setup     Install everything (uses the GPU build of PyTorch when possible)
  ./tasks.sh dev       Run the API and the web UI with hot reload
  ./tasks.sh serve     Run the built app on http://127.0.0.1:8000
  ./tasks.sh share     Put this machine on a public URL via a Cloudflare tunnel
  ./tasks.sh build     Build the web UI for production
  ./tasks.sh test      Run the test suite
  ./tasks.sh lint      Check formatting and types
  ./tasks.sh gpu       Show the GPU the app can see, and how the model is placed
  ./tasks.sh ollama    Pull a suitable local model
  ./tasks.sh clean     Remove build output (leaves your data alone)

Command line:
  .venv/bin/lectern --help

EOF
  ;;
esac
