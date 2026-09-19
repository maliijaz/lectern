#!/usr/bin/env bash
# One-command install for Lectern on macOS and Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/maliijaz/lectern/main/install.sh | bash
#
# Run this and you get a working Lectern: the app, its dependencies, a local AI model,
# and a browser open on it. Everything it installs is free and open source, it installs
# only what is missing, and it says what it is about to do before it does it.
set -euo pipefail

REPO="https://github.com/maliijaz/lectern"
BRANCH="main"
TARGET="${LECTERN_HOME:-$HOME/lectern}"
MODEL="${LECTERN_MODEL:-qwen3:8b}"
SKIP_MODEL="${LECTERN_SKIP_MODEL:-0}"
ASSUME_YES="${LECTERN_YES:-0}"

while [ $# -gt 0 ]; do
  case "$1" in
    --path) TARGET="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --skip-model) SKIP_MODEL=1; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    *) shift ;;
  esac
done

head() { printf '\n\033[36m%s\033[0m\n' "$1"; }
ok() { printf '\033[32m  [ok]\033[0m %s\n' "$1"; }
info() { printf '\033[90m  %s\033[0m\n' "$1"; }
warn() { printf '\033[33m  [!]\033[0m %s\n' "$1"; }
bad() { printf '\033[31m  [x]\033[0m %s\n' "$1"; }
has() { command -v "$1" >/dev/null 2>&1; }

OS="$(uname -s)"
case "$OS" in
  Darwin) PLATFORM=mac ;;
  Linux) PLATFORM=linux ;;
  *) bad "This installer supports macOS and Linux. On Windows use install.ps1."; exit 1 ;;
esac

# The package manager anything missing gets installed with. Detected rather than assumed,
# because "install these three things first" is exactly the friction this script exists
# to remove -- but only where we can do it without guessing.
PM=""
if [ "$PLATFORM" = mac ] && has brew; then PM="brew"
elif has apt-get; then PM="apt"
elif has dnf; then PM="dnf"
elif has pacman; then PM="pacman"
fi

pm_install() {
  local pkg="$1" label="$2"
  if [ -z "$PM" ]; then
    bad "$label is missing and no supported package manager was found."
    info "Install $label yourself, then run this again."
    return 1
  fi
  info "Installing $label..."
  case "$PM" in
    brew) brew install "$pkg" ;;
    apt) sudo apt-get update -qq && sudo apt-get install -y "$pkg" ;;
    dnf) sudo dnf install -y "$pkg" ;;
    pacman) sudo pacman -S --noconfirm "$pkg" ;;
  esac
}

python_ok() {
  for c in python3.13 python3.12 python3.11 python3; do
    if has "$c" && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  return 1
}

cat <<'EOF'

  Lectern
  Slide decks, lecture notes and question papers, made on your own machine.

EOF

# ---------------------------------------------------------------- what is already here
head "Checking what you already have"

PYBIN="$(python_ok || true)"
if [ -n "$PYBIN" ]; then ok "Python ($("$PYBIN" --version 2>&1))"; else warn "Python 3.11+ is not installed"; fi
if has node; then ok "Node $(node --version)"; else warn "Node is not installed"; fi
if has ollama; then ok "Ollama"; else warn "Ollama is not installed"; fi

# ---------------------------------------------------------------- the plan
head "The plan"
TODO=""
[ -z "$PYBIN" ] && TODO="$TODO Python"
has node || TODO="$TODO Node.js"
{ has ollama || [ "$SKIP_MODEL" = 1 ]; } || TODO="$TODO Ollama"
[ -n "$TODO" ] && info "Install:$TODO"
info "Download Lectern to: $TARGET"
info "Set up its Python environment and build the web interface"
[ "$SKIP_MODEL" = 1 ] || info "Download the $MODEL model (about 5 GB, one time)"
info "Start it and open your browser"

if [ "$ASSUME_YES" != 1 ] && [ -t 0 ]; then
  printf '\n  Press Enter to continue, or Ctrl-C to stop: '
  read -r _ </dev/tty || true
fi

# ---------------------------------------------------------------- prerequisites
if [ -z "$PYBIN" ]; then
  head "Installing Python"
  case "$PM" in
    brew) pm_install python@3.12 "Python 3.12" ;;
    apt) pm_install python3 "Python" && sudo apt-get install -y python3-venv python3-pip ;;
    *) pm_install python3 "Python" ;;
  esac
  PYBIN="$(python_ok || true)"
  [ -n "$PYBIN" ] || { bad "Python still not found. Install it and run this again."; exit 1; }
  ok "Python ($("$PYBIN" --version 2>&1))"
fi

if ! has node; then
  head "Installing Node.js"
  case "$PM" in
    brew) pm_install node "Node.js" ;;
    apt) pm_install nodejs "Node.js" && sudo apt-get install -y npm ;;
    *) pm_install nodejs "Node.js" ;;
  esac
  has node || { bad "Node still not found. Install it and run this again."; exit 1; }
  ok "Node $(node --version)"
fi

if ! has ollama && [ "$SKIP_MODEL" != 1 ]; then
  head "Installing Ollama"
  if [ "$PLATFORM" = mac ] && [ "$PM" = brew ]; then
    brew install --cask ollama || warn "Ollama install failed; continuing without a local model."
  else
    # Ollama's own installer is the supported path on Linux and handles the service unit.
    curl -fsSL https://ollama.com/install.sh | sh || warn "Ollama install failed; continuing without a local model."
  fi
  has ollama && ok "Ollama installed" || SKIP_MODEL=1
fi

# ---------------------------------------------------------------- the code
head "Getting Lectern"
if [ -d "$TARGET/backend" ]; then
  info "Already there - updating it"
  if has git && [ -d "$TARGET/.git" ]; then
    (cd "$TARGET" && git pull --ff-only >/dev/null 2>&1) && ok "updated" || warn "could not update; using what is there"
  else
    ok "using the existing copy"
  fi
elif has git; then
  git clone --depth 1 --branch "$BRANCH" "$REPO" "$TARGET" >/dev/null 2>&1
  ok "cloned to $TARGET"
else
  # No git, so take the tarball. Keeps this to one command for someone who has never
  # installed a developer tool.
  info "git is not installed, downloading the archive instead"
  TMP="$(mktemp -d)"
  curl -fsSL "$REPO/archive/refs/heads/$BRANCH.tar.gz" | tar -xz -C "$TMP"
  mkdir -p "$(dirname "$TARGET")"
  mv "$TMP"/*/ "$TARGET"
  rm -rf "$TMP"
  ok "downloaded to $TARGET"
fi

chmod +x "$TARGET/tasks.sh" 2>/dev/null || true

# ---------------------------------------------------------------- the model
if [ "$SKIP_MODEL" != 1 ] && has ollama; then
  head "Getting the AI model"
  if ollama list 2>/dev/null | grep -q "${MODEL%%:*}"; then
    ok "$MODEL is already downloaded"
  else
    info "Pulling $MODEL - about 5 GB, so this is the slow part"
    ollama pull "$MODEL"
    ok "$MODEL ready"
  fi
fi

# ---------------------------------------------------------------- build
head "Setting up (a few minutes)"
"$TARGET/tasks.sh" setup

# ---------------------------------------------------------------- go
cat <<EOF

$(printf '\033[32m  Done.\033[0m')

  Lectern is at http://127.0.0.1:8000
  Your copy lives in $TARGET

  To start it again later:
      cd $TARGET
      ./tasks.sh serve

  To put it on a public link for a colleague:
      ./tasks.sh share

EOF

( sleep 6
  if [ "$PLATFORM" = mac ]; then open http://127.0.0.1:8000 >/dev/null 2>&1 || true
  else xdg-open http://127.0.0.1:8000 >/dev/null 2>&1 || true
  fi ) &

exec "$TARGET/tasks.sh" serve
