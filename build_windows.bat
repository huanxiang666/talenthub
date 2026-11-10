@echo off
chcp 65001 >nul
rem ============================================================
rem  TalentHub Windows 一键打包：产出 dist\TalentHub.exe 单文件
rem  要求：Windows 10/11 + Python 3.10-3.12（python.org，勾选 Add to PATH）
rem  说明：PyInstaller 不支持跨平台交叉编译，exe 必须在 Windows 上打包；
rem        本脚本全自动（建环境→装依赖→打包），约 5-10 分钟。
rem ============================================================
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [错误] 未找到 Python，请先安装 Python 3.10-3.12 并勾选 Add to PATH
  pause
  exit /b 1
)

if not exist ".venv-build\Scripts\python.exe" (
  echo [1/3] 创建独立构建环境 .venv-build …
  %PY% -m venv .venv-build
  if errorlevel 1 (
    echo [错误] venv 创建失败
    pause
    exit /b 1
  )
)

echo [2/3] 安装依赖与 PyInstaller（含 OCR 运行库，约 2-5 分钟）…
.venv-build\Scripts\python.exe -m pip install -q --upgrade pip
.venv-build\Scripts\python.exe -m pip install -q -r requirements.txt pyinstaller
if errorlevel 1 (
  echo [错误] 依赖安装失败，请检查网络
  pause
  exit /b 1
)

echo [3/3] 打包单文件 exe …
.venv-build\Scripts\pyinstaller.exe talenthub.spec --noconfirm
if errorlevel 1 (
  echo [错误] 打包失败，请把上面的报错发出来排查
  pause
  exit /b 1
)

echo.
echo ============================================================
echo  ✓ 产物: %cd%\dist\TalentHub.exe
echo    双击运行（自动弹浏览器 http://127.0.0.1:8300）
echo    开放端口: TalentHub.exe --host 0.0.0.0
echo    换端口:   TalentHub.exe --port 9000
echo    数据目录: exe 旁边的 data\（可整体拷贝迁移）
echo ============================================================
pause
