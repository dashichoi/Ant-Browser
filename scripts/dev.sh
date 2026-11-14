#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-stable}"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/dev.sh [stable|live|help]

Modes:
  stable   Default. Build frontend static assets and start Wails without Vite dev server.
  live     Start the frontend dev server and connect Wails to it.
  help     Show this help.
EOF
}

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[ERROR] Missing required command: $cmd" >&2
    exit 1
  fi
}

# 端口占用检测：Linux 用 ss，macOS 用 lsof，都没有时退回 bash /dev/tcp
is_tcp_port_busy() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "( sport = :$port )" 2>/dev/null | tail -n +2 | grep -q .
  elif command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
  else
    (exec 3<>"/dev/tcp/127.0.0.1/$port") >/dev/null 2>&1 || return 1
    exec 3<&- 3>&-
    return 0
  fi
}

resolve_wails_devserver() {
  local start_port="${WAILS_DEVSERVER_PORT:-34115}"
  local host="${WAILS_DEVSERVER_HOST:-127.0.0.1}"
  local port="$start_port"

  while is_tcp_port_busy "$port"; do
    port=$((port + 1))
  done

  WAILS_DEVSERVER_ADDRESS="$host:$port"
  export WAILS_DEVSERVER_ADDRESS
}

# 仅 Linux 需要 webkit2gtk 相关 tag；macOS 使用系统 WKWebView，没有 pkg-config
detect_wails_build_tags() {
  WAILS_BUILD_TAGS=()
  command -v pkg-config >/dev/null 2>&1 || return 0
  if ! pkg-config --exists webkit2gtk-4.0 && pkg-config --exists webkit2gtk-4.1; then
    echo "Detected webkit2gtk-4.1 only, enabling Wails tag: webkit2_41"
    WAILS_BUILD_TAGS=(-tags webkit2_41)
  fi
}

prepare_env() {
  require_cmd node
  require_cmd npm
  require_cmd go
  require_cmd wails
  detect_wails_build_tags

  if [[ -n "${DEV_PROXY_URL:-}" ]]; then
    export HTTP_PROXY="$DEV_PROXY_URL"
    export HTTPS_PROXY="$DEV_PROXY_URL"
    export http_proxy="$DEV_PROXY_URL"
    export https_proxy="$DEV_PROXY_URL"
  fi

  if [[ -n "${DEV_NO_PROXY:-}" ]]; then
    export NO_PROXY="$DEV_NO_PROXY"
    export no_proxy="$DEV_NO_PROXY"
  fi

  if [[ -n "${DEV_GOPROXY:-}" ]]; then
    export GOPROXY="$DEV_GOPROXY"
  elif [[ -z "${GOPROXY:-}" ]]; then
    export GOPROXY="https://goproxy.cn,direct"
  fi
}

install_frontend_deps() {
  echo "Installing frontend dependencies..."
  npm install
}

build_frontend() {
  echo "Building frontend assets..."
  npm run build:clean
}

run_stable() {
  echo "========================================"
  echo "  Ant Chrome - Dev Launcher"
  echo "========================================"
  echo
  echo "Current workdir: $ROOT_DIR"
  echo "Mode: stable"
  echo "Frontend mode: stable static assets"
  echo "Wails frontend dev server: disabled"
  echo

  prepare_env
  resolve_wails_devserver
  cd "$ROOT_DIR/frontend"
  install_frontend_deps
  build_frontend

  cd "$ROOT_DIR"
  echo "Starting Wails dev..."
  echo "Wails dev server: http://$WAILS_DEVSERVER_ADDRESS"
  exec wails dev ${WAILS_BUILD_TAGS[@]+"${WAILS_BUILD_TAGS[@]}"} -m -nogorebuild -noreload -s -skipbindings -assetdir frontend/dist -devserver "$WAILS_DEVSERVER_ADDRESS"
}

run_live() {
  local frontend_port="${FRONTEND_PORT:-5218}"
  local frontend_pid=""

  trap 'if [[ -n "$frontend_pid" ]] && kill -0 "$frontend_pid" >/dev/null 2>&1; then kill "$frontend_pid" >/dev/null 2>&1 || true; fi' EXIT

  echo "========================================"
  echo "  Ant Chrome - Dev Launcher"
  echo "========================================"
  echo
  echo "Current workdir: $ROOT_DIR"
  echo "Mode: live"
  echo "Frontend URL: http://127.0.0.1:$frontend_port"
  echo

  prepare_env
  resolve_wails_devserver

  cd "$ROOT_DIR/frontend"
  install_frontend_deps
  npm run dev:raw -- --host 127.0.0.1 --port "$frontend_port" &
  frontend_pid="$!"

  cd "$ROOT_DIR"
  echo "Starting Wails dev..."
  echo "Wails dev server: http://$WAILS_DEVSERVER_ADDRESS"
  exec wails dev ${WAILS_BUILD_TAGS[@]+"${WAILS_BUILD_TAGS[@]}"} -m -s -skipbindings -frontenddevserverurl "http://127.0.0.1:$frontend_port" -viteservertimeout 60 -devserver "$WAILS_DEVSERVER_ADDRESS"
}

case "$MODE" in
  stable)
    run_stable
    ;;
  live)
    run_live
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "[ERROR] Unsupported mode: $MODE" >&2
    echo >&2
    usage >&2
    exit 1
    ;;
esac
