# -*- mode: python ; coding: utf-8 -*-
"""TalentHub PyInstaller 打包规格 — Windows / Linux 通用。

用法（在项目根目录）:
  Linux:   .venv/bin/pyinstaller talenthub.spec
  Windows: .venv\Scripts\pyinstaller talenthub.spec   （或直接双击 build_windows.bat）
产物:
  Linux:   dist/talenthub        （单文件可执行）
  Windows: dist\TalentHub.exe    （单文件 exe）
"""
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# OCR 引擎与依赖：模型(.onnx)、配置 yaml、原生库全部收进来
for pkg in ("rapidocr_onnxruntime", "onnxruntime", "pypdfium2", "pdfplumber", "pdfminer", "fpdf"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# AI 增强解析（可选依赖）：装了 llama-cpp-python 就打进二进制；没装自动跳过，
# 运行时 llm_parser.status() 检测不到即回退纯规则解析（不影响主功能）。
# 注意：GGUF 模型文件不打包（太大），由用户在「系统设置 → 模型下载」页自取。
try:
    d, b, h = collect_all("llama_cpp")
    datas += d
    binaries += b
    hiddenimports += h
except Exception:
    print("[spec] llama_cpp 未安装，二进制不含 AI 增强解析（规则解析不受影响）")

# 前端静态资源（index.html / style.css / app.js / vendor/echarts.min.js）
datas += [(str(Path("app") / "static"), str(Path("app") / "static"))]

# 业务包全量收集（uvicorn 字符串导入等动态引用兜底）
hiddenimports += [
    "app", "app.main", "app.config", "app.db",
    "app.extract", "app.extract.loader", "app.extract.ocr_engine", "app.extract.parser",
    "app.extract.llm_parser",
    "app.services", "app.services.talent", "app.services.excel", "app.services.model_hub",
    "app.services.lock_auth", "app.services.applications", "app.services.doc2pdf", "app.services.updater",
    "app.apply", "app.apply.apply_app", "app.apply.captcha",
    "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan", "uvicorn.lifespan.on",
]

a = Analysis(
    ["server.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)

import sys as _sys
APP_NAME = "TalentHub" if _sys.platform.startswith("win") else "talenthub"

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    strip=False,
    upx=False,
    console=True,          # 保留控制台：显示访问地址横幅与日志
    disable_windowed_traceback=False,
)
