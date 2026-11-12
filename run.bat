@echo off
chcp 65001 >nul
rem TalentHub 一键启动（Windows）
rem 首次运行自动创建虚拟环境并安装依赖；之后直接启动。
rem 用法: run.bat [--port 9000] [--host 0.0.0.0] [--no-open]
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [错误] 未找到 Python，请先安装 Python 3.10-3.12（python.org 下载，安装时勾选 Add to PATH）
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [安装] 创建虚拟环境…
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [错误] 虚拟环境创建失败。若 Python 来自 Microsoft Store，请改用 python.org 安装包。
    pause
    exit /b 1
  )
)

rem 每次启动都静默校验依赖（已满足时 pip 秒级跳过；requirements.txt 更新后自动补装）
echo [检查] 校验依赖（首次安装约 1-3 分钟，含 OCR 模型 ~90MB）…
.venv\Scripts\python.exe -m pip install -q --upgrade pip >nul 2>&1
.venv\Scripts\python.exe -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo [错误] 依赖安装失败，请检查网络后重试
  pause
  exit /b 1
)

rem AI 组件（llama-cpp-python）：自动补装一次，装不上不阻断（规则解析+外部API引擎仍可用）
if not exist ".venv\.llm-tried" (
  .venv\Scripts\python.exe -c "import llama_cpp" >nul 2>&1
  if errorlevel 1 (
    echo [安装] 正在安装 AI 组件 llama-cpp-python（CPU 预编译轮子，约 30-60 秒）…
    .venv\Scripts\python.exe -m pip install -q llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
    if errorlevel 1 (
      echo [提示] AI 组件安装失败（多为网络原因），程序仍可正常运行。
      echo        稍后可手动执行: .venv\Scripts\pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
    )
  )
  echo. > ".venv\.llm-tried"
)

.venv\Scripts\python.exe server.py %*
pause
