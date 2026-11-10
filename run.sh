#!/usr/bin/env bash
# TalentHub 一键启动（Linux / macOS）
# 首次运行自动创建虚拟环境并安装依赖；之后直接启动。
# 用法: ./run.sh [--port 9000] [--host 0.0.0.0] [--no-open]
set -e
cd "$(dirname "$0")"

# 1. 找 Python（要求 3.9 ~ 3.12；onnxruntime 对 3.13+ 的轮子可能尚未发布）
PY=""
for c in python3.12 python3.11 python3.10 python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    v=$("$c" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)
    case "$v" in
      3.9|3.10|3.11|3.12|3.13) PY="$c"; break ;;
      *) echo "[提示] $c 版本 $v 不在推荐范围(3.9-3.13)，继续找…" ;;
    esac
  fi
done

# 2. 建虚拟环境：优先 uv（可自动下载合适的 Python），退回 venv 模块
if [ ! -x .venv/bin/python ]; then
  if command -v uv >/dev/null 2>&1; then
    echo "[安装] 使用 uv 创建虚拟环境…"
    uv venv --python 3.12 .venv >/dev/null 2>&1 || uv venv .venv
  else
    [ -z "$PY" ] && { echo "[错误] 未找到 Python 3.9-3.13，请先安装（推荐 3.12）"; exit 1; }
    echo "[安装] 使用 $PY 创建虚拟环境…"
    "$PY" -m venv .venv
  fi
fi

# 3. 安装依赖（已装过则秒级跳过）
if [ ! -f .venv/.deps-ok ] || [ requirements.txt -nt .venv/.deps-ok ]; then
  echo "[安装] 安装依赖（首次约 1-3 分钟，含 OCR 模型 ~90MB）…"
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python .venv/bin/python -r requirements.txt
  else
    .venv/bin/pip install -q --upgrade pip
    .venv/bin/pip install -q -r requirements.txt
  fi
  touch .venv/.deps-ok
fi

# 4. 启动
exec .venv/bin/python server.py "$@"
