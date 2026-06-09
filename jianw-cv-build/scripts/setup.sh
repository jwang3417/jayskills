#!/usr/bin/env bash
# Set up the LaTeX toolchain needed to build jianw_cv.tex on a fresh macOS machine.
# Idempotent: safe to re-run. Installs BasicTeX, the bundled `res` resume class,
# and the Palatino font package. Run this in a real terminal (the BasicTeX step
# needs `sudo`, which requires an interactive password prompt).
#
# Usage:
#   bash setup.sh            # install prerequisites only
#   bash setup.sh --build    # install prerequisites, then build ./jianw_cv.pdf
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASSET_DIR="$(cd "$SCRIPT_DIR/../assets" && pwd)"
TEXBIN="/Library/TeX/texbin"
# Reliable CTAN tlnet mirror (default mirror is sometimes down / DNS-blocked).
REPO="https://ctan.math.illinois.edu/systems/texlive/tlnet"

log() { printf '\n==> %s\n' "$*"; }

ensure_path() {
  eval "$(/usr/libexec/path_helper)" >/dev/null 2>&1 || true
  export PATH="$TEXBIN:$PATH"
}

# 1. Homebrew ---------------------------------------------------------------
if ! command -v brew >/dev/null 2>&1; then
  echo "ERROR: Homebrew not found. Install it from https://brew.sh then re-run." >&2
  exit 1
fi

# 2. BasicTeX (pdflatex) ----------------------------------------------------
ensure_path
if command -v pdflatex >/dev/null 2>&1; then
  log "pdflatex already installed: $(command -v pdflatex)"
else
  log "Installing BasicTeX (downloads the cask, then runs the pkg installer with sudo)..."
  brew install --cask basictex || true   # cask install may fail at the sudo step; handled below
  ensure_path
  if ! command -v pdflatex >/dev/null 2>&1; then
    # Locate the downloaded pkg and install it directly (prompts for sudo password).
    PKG="$(ls -t "$HOME"/Library/Caches/Homebrew/Cask/mactex-basictex-*.pkg--*.pkg \
                  "$HOME"/Library/Caches/Homebrew/downloads/*mactex-basictex*.pkg 2>/dev/null | head -1 || true)"
    if [ -z "${PKG:-}" ]; then
      echo "ERROR: could not find the BasicTeX installer pkg. Run 'brew fetch --cask basictex' and retry." >&2
      exit 1
    fi
    log "Running: sudo installer -pkg \"$PKG\" -target /"
    sudo installer -pkg "$PKG" -target /
    ensure_path
  fi
fi
command -v pdflatex >/dev/null 2>&1 || { echo "ERROR: pdflatex still not on PATH." >&2; exit 1; }

# 3. The `res` resume class (not in TeX Live) -------------------------------
# The bundled res.cls is the modern version that includes the `margin` and
# `line` options internally, so no separate margin.sty / line.sty are needed.
if ! kpsewhich res.cls >/dev/null 2>&1; then
  TEXMFHOME="$(kpsewhich -var-value TEXMFHOME)"
  DEST="$TEXMFHOME/tex/latex/resume"
  log "Installing res.cls into $DEST"
  mkdir -p "$DEST"
  cp "$ASSET_DIR/res.cls" "$DEST/"
  mktexlsr "$TEXMFHOME" >/dev/null 2>&1 || true
fi
kpsewhich res.cls >/dev/null 2>&1 || { echo "ERROR: res.cls not found after install." >&2; exit 1; }
log "res.cls: $(kpsewhich res.cls)"

# 4. Palatino fonts (pplr7t etc.) via tlmgr user mode (no sudo) -------------
if ! kpsewhich pplr7t.tfm >/dev/null 2>&1; then
  log "Installing Palatino fonts (tlmgr user mode)..."
  tlmgr init-usertree >/dev/null 2>&1 || true
  tlmgr --usermode --repository "$REPO" install palatino
fi
kpsewhich pplr7t.tfm >/dev/null 2>&1 || { echo "ERROR: Palatino TFM not found after install." >&2; exit 1; }
log "Palatino TFM: $(kpsewhich pplr7t.tfm)"

log "Prerequisites ready. Build with: bash makepdf  (or: pdflatex jianw_cv && pdflatex jianw_cv)"

# 5. Optional build ---------------------------------------------------------
if [ "${1:-}" = "--build" ]; then
  if [ ! -f jianw_cv.tex ]; then
    echo "ERROR: jianw_cv.tex not found in $(pwd). cd to the resume folder first." >&2
    exit 1
  fi
  log "Building jianw_cv.pdf..."
  pdflatex -interaction=nonstopmode jianw_cv.tex >/dev/null
  pdflatex -interaction=nonstopmode jianw_cv.tex >/dev/null
  log "Done: $(pwd)/jianw_cv.pdf"
fi
