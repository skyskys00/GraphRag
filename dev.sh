#!/usr/bin/env bash
# ============================================================
# GraphRAG 一键启停脚本（开发环境）
#
# 管理四件套，启动顺序 PG → Xinference → 后端 → 前端；停止反向。
#   pg  = docker 容器 graphrag-pg（postgres/pgvector，端口 5432）
#   xin = Xinference 模型网关（127.0.0.1:9997，bge-m3 等）
#   api = M7 后端（127.0.0.1:8787）
#   web = M8 前端 vite（localhost:5173，vite 监听 IPv6 ::1）
#
# 用法：
#   ./dev.sh start|stop|restart|status|logs [pg|xin|api|web]   （省略组件 = 全部）
#   例：./dev.sh start web      # 只重拉前端
#       ./dev.sh logs api       # 跟踪后端日志
#
# 日志落盘 logs/{xin,api,web}.log（根 logs/ 已被 .gitignore 忽略）。
# 各步幂等：已在运行/已停止的组件直接跳过并提示。
#
# 注意：
#   - Xinference 用绝对路径 python 启动（勿用 conda run）。
#   - 重启后 Xinference 不会自带恢复模型：start 会逐一核对 bge-m3 /
#     bge-reranker-v2-m3，缺失则用 xinference launch 载入（CPU）。
#     若首次全新环境，launch 会自动下载模型（走 modelscope/预留缓存，较慢）。
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGDIR="$ROOT/logs"
PY="/opt/anaconda3/envs/graphrag/bin/python"

XIN_PORT=9997
API_PORT=8787
WEB_PORT=5173
PG_CONTAINER="graphrag-pg"

mkdir -p "$LOGDIR"

say()  { printf '\033[32m[%s]\033[0m %s\n' "$(date '+%H:%M:%S')" "$*"; }
warn() { printf '\033[33m[%s]\033[0m %s\n' "$(date '+%H:%M:%S')" "$*"; }
fail() { printf '\033[31m[%s]\033[0m %s\n' "$(date '+%H:%M:%S')" "$*" >&2; }

port_listening() { lsof -tiTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

# 后台启动一条命令：nohup 防终端退出收到 SIGHUP；日志统一落 logs/
bg() { local log=$1; shift; nohup "$@" >>"$log" 2>&1 & }

check_xin_token() { [ -n "$(xin_token)" ]; }

xin_token() {
  curl -s --max-time 3 -X POST "http://127.0.0.1:${XIN_PORT}/token" \
    -H 'Content-Type: application/json' \
    -d '{"username":"admin","password":"graphrag_local"}' \
    | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null
}

# 已加载模型 uid 列表（每行一个）
xin_loaded() {
  local token; token=$(xin_token)
  [ -n "$token" ] || return 1
  curl -s --max-time 5 "http://127.0.0.1:${XIN_PORT}/v1/models" \
    -H "Authorization: Bearer $token" \
    | python3 -c "import sys,json;[print(m['id']) for m in json.load(sys.stdin).get('data',[])]" 2>/dev/null
}

xin_launch() { # uid, model-type, engine, [--键 值 ...]
  local uid=$1 type=$2 engine=$3; shift 3
  local token; token=$(xin_token)
  [ -n "$token" ] || { fail "XIN 取 token 失败"; exit 1; }
  "$(dirname "$PY")/xinference" launch -ak "$token" \
    --model-name "$uid" --model-type "$type" --model-engine "$engine" "$@" \
    >>"$LOGDIR/xin.log" 2>&1
}

# 逐模型检查，缺失则载入（CPU，避免 M3 MPS segfault；bge-m3 需 return_sparse）
XIN_MODELS=$(cat <<'EOF'
bge-m3|embedding|flag|--device cpu --return_sparse true
bge-reranker-v2-m3|rerank|sentence_transformers|--device cpu
EOF
)
xin_ensure_models() {
  local loaded; loaded=$(xin_loaded) || { fail "XIN 模型列表不可达"; exit 1; }
  local line uid type engine extra
  while IFS='|' read -r uid type engine extra; do
    [ -n "$uid" ] || continue
    if printf '%s\n' "$loaded" | grep -qx "$uid"; then
      say "XIN 模型 $uid 已载"
    else
      say "XIN 载入模型 $uid（$engine）..."
      # shellcheck disable=SC2086
      xin_launch "$uid" "$type" "$engine" $extra
      say "XIN 模型 $uid 就绪"
    fi
  done <<<"$XIN_MODELS"
}

# ---------- 启动 ----------
pg_start() {
  if docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
    say "PG  已运行（$PG_CONTAINER）"
  elif docker ps -a --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
    say "PG  启动容器 $PG_CONTAINER ..."
    docker start "$PG_CONTAINER" >/dev/null
  else
    fail "PG  容器 $PG_CONTAINER 不存在，请先创建（卷 graphrag-pgdata 保留数据）"
    exit 1
  fi
  local n=0
  until docker exec "$PG_CONTAINER" pg_isready -U postgres >/dev/null 2>&1; do
    n=$((n+1)); [ $n -ge 30 ] && { fail "PG  等待就绪超时"; exit 1; }; sleep 0.5
  done
  say "PG  就绪（pg_isready ok）"
}

xin_start() {
  if check_xin_token; then
    say "XIN 已运行（127.0.0.1:${XIN_PORT}，认证 ok）"
  elif port_listening "$XIN_PORT"; then
    fail "XIN 端口被占用但 /token 认证失败（logs/xin.log）"
    exit 1
  else
    say "XIN 启动 Xinference → logs/xin.log"
    bg "$LOGDIR/xin.log" "$PY" "$(dirname "$PY")/xinference-local" \
       --host 127.0.0.1 --port "$XIN_PORT"
    local n=0
    until check_xin_token; do
      n=$((n+1)); [ $n -ge 60 ] && { fail "XIN 120s 未就绪，见 logs/xin.log"; exit 1; }; sleep 2
    done
    say "XIN 服务就绪"
  fi
  # 服务已在/刚起都逐一核对模型，缺失则载入
  xin_ensure_models
}

api_start() {
  if port_listening "$API_PORT"; then say "API 已运行（127.0.0.1:${API_PORT}）"; return 0; fi
  say "API 启动后端 → logs/api.log"
  bg "$LOGDIR/api.log" bash -c \
    'cd "$0" && exec "$1" -m app.m7_interact.runner --port "$2"' \
    "$ROOT/backend" "$PY" "$API_PORT"
  local n=0
  until [ "$(curl -s --max-time 2 http://127.0.0.1:${API_PORT}/health 2>/dev/null)" = '{"status":"ok"}' ]; do
    n=$((n+1)); [ $n -ge 40 ] && { fail "API 80s 未就绪，见 logs/api.log"; exit 1; }; sleep 2
  done
  say "API 就绪（/health ok）"
}

web_start() {
  if port_listening "$WEB_PORT"; then say "WEB 已运行（http://localhost:${WEB_PORT}）"; return 0; fi
  say "WEB 启动前端 vite → logs/web.log"
  bg "$LOGDIR/web.log" bash -c 'cd "$0" && exec npm run dev' "$ROOT/frontend"
  local n=0
  until curl -s --max-time 2 http://localhost:${WEB_PORT} >/dev/null 2>&1; do
    n=$((n+1)); [ $n -ge 60 ] && { fail "WEB 120s 未就绪，见 logs/web.log"; exit 1; }; sleep 2
  done
  say "WEB 就绪（vite 监听 IPv6 ::1:${WEB_PORT}，访问 http://localhost:${WEB_PORT}）"
}

# ---------- 停止 ----------
stop_port() { # 端口, 组件名 —— 杀掉监听该端口的进程，宽限后强制
  local port=$1 name=$2
  if ! port_listening "$port"; then warn "$name 未运行，跳过"; return 0; fi
  local pids; pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN | tr '\n' ' ')
  warn "$name 停止 PID:${pids%% } ..."
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  local t=0
  while port_listening "$port"; do t=$((t+1)); [ $t -ge 12 ] && break; sleep 0.5; done
  if port_listening "$port"; then
    warn "$name 6s 未退，强制 kill -9"
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
    sleep 1
  fi
  if port_listening "$port"; then warn "$name 仍占用 :$port"; return 1
  else say "$name 已停止"; fi
}

pg_stop() {
  if docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
    say "PG  停止容器 $PG_CONTAINER（数据卷保留，重启不丢）"
    docker stop "$PG_CONTAINER" >/dev/null 2>&1 || true
  else
    say "PG  已停止"
  fi
}
# Xinference 停止（三层清理，防孤儿 worker 残留）：
#   1) 优雅卸载各模型（DELETE /v1/models/{uid}，让 server 自己回收 worker）
#   2) 停 server（9997 监听者，6s 宽限后强杀）
#   3) 兜底清残留：worker 进程 comm 形如 "Model: *-rep0"、spawn 父 cmdline 含 spawn_main
#      （本机 multiprocessing.spawn_main 只由 Xinference worker 家族使用——后端是单进程 uvicorn）
xin_stop() {
  local token uid
  token=$(xin_token)
  for uid in $(xin_loaded 2>/dev/null); do
    say "XIN 卸载模型 $uid"
    curl -s --max-time 15 -X DELETE "http://127.0.0.1:${XIN_PORT}/v1/models/${uid}" \
      -H "Authorization: Bearer $token" >/dev/null 2>&1 || true
  done
  stop_port "$XIN_PORT" "XIN" || true
  if pgrep -f "Model: .*-rep0" >/dev/null 2>&1 || pgrep -f "spawn_main" >/dev/null 2>&1; then
    warn "XIN 清理残留模型 worker（spawn 进程组）"
    pkill -f "Model: .*-rep0" 2>/dev/null || true
    pkill -f "spawn_main" 2>/dev/null || true
    sleep 1
  fi
}
api_stop()   { stop_port "$API_PORT" "API"; }
web_stop()   { stop_port "$WEB_PORT" "WEB"; }

# ---------- 状态 ----------
status() {
  echo "── GraphRAG 开发组件状态 ──"
  if docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
    docker ps --filter "name=^${PG_CONTAINER}\$" \
      --format "PG  容器 ${PG_CONTAINER}  运行中 ({{.Status}})"
  else
    warn "PG  容器 ${PG_CONTAINER}  停止"
  fi
  port_listening $XIN_PORT && say "XIN 监听 :$XIN_PORT (附模型进程)" || warn "XIN 未监听 :$XIN_PORT"
  port_listening $API_PORT && say "API 监听 :$API_PORT"              || warn "API 未监听 :$API_PORT"
  port_listening $WEB_PORT && say "WEB 监听 :$WEB_PORT"              || warn "WEB 未监听 :$WEB_PORT"
}

# ---------- 派发 ----------
SERVICES="pg xin api web"
cmd="${1:-}"; svc="${2:-all}"
case "$svc" in all|pg|xin|api|web) ;; *) fail "未知组件：$svc（可选 pg|xin|api|web）"; exit 1;; esac

run_selected() { # $1 = start|stop
  local action=$1 has=0 s
  # 停止时反序：web → api → xin → pg（先摘应用，最后停基础设施）
  local order="$SERVICES"
  [ "$action" = stop ] && order="web api xin pg"
  for s in $order; do
    [ "$svc" = all ] || [ "$svc" = "$s" ] || continue
    has=1; "${s}_${action}"
  done
  [ $has -eq 1 ] && return 0
  fail "未选中任何组件" && exit 1
}

case "$cmd" in
  start)   run_selected start
           echo "── 全部就绪 ──────────────────────────"
           echo "  前端  http://localhost:$WEB_PORT"
           echo "  后端  http://127.0.0.1:$API_PORT/health"
           echo "  日志  ./dev.sh logs api|web|xin|pg"
           ;;
  stop)    run_selected stop ;;
  restart) run_selected stop; run_selected start ;;
  status)  status ;;
  logs)    case "$svc" in
             pg)  docker logs -f --tail 50 "$PG_CONTAINER" ;;
             xin) tail -f -n 50 "$LOGDIR/xin.log" ;;
             api) tail -f -n 50 "$LOGDIR/api.log" ;;
             web) tail -f -n 50 "$LOGDIR/web.log" ;;
             all) fail "logs 需指定组件：./dev.sh logs api|xin|web|pg"; exit 1 ;;
           esac ;;
  *)       echo "用法：./dev.sh start|stop|restart|status|logs [pg|xin|api|web]"; exit 1 ;;
esac