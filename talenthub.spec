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
for pkg in ("rapidocr_onnxruntime", "onnxruntime", "pypdfium2", "pdfplumber", "pdfminer"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# 前端静态资源（index.html / style.css / app.js / vendor/echarts.min.js）
datas += [(str(Path("app") / "static"), str(Path("app") / "static"))]

# 业务包全量收集（uvicorn 字符串导入等动态引用兜底）
hiddenimports += [
    "app", "app.main", "app.config", "app.db",
    "app.extract", "app.extract.loader", "app.extract.ocr_engine", "app.extract.parser",
    "app.services", "app.services.talent", "app.services.excel",
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
