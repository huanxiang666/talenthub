"""更新检查：从 GitHub Releases 查最新版本，提供直连与加速源双下载链接。

- api.github.com 通常国内可直连；失败时回退 gh-proxy 加速代理
- 版本比较用语义化数字组（1.10.0 > 1.9.0），非字符串比较
- 全程 fail-soft：任何网络/解析错误返回 {"error": ...}，绝不影响主程序
"""
from __future__ import annotations

import json
import re
import urllib.request

REPO = "huanxiang666/talenthub"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
# 加速代理（GitHub 文件下载直连慢/超时时用）；前缀式代理，拼在原始 URL 前
MIRRORS = {
    "gh-proxy（国内加速）": "https://gh-proxy.com/",
    "ghproxy 备用": "https://mirror.ghproxy.com/",
}
TIMEOUT = 10


def _ver_tuple(v: str) -> tuple:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def check_update(current_version: str) -> dict:
    """检查更新。返回 {latest, current, has_update, published_at, notes, assets:[{name,size_mb,url,mirrors:{}}]}"""
    try:
        req = urllib.request.Request(API_URL, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "TalentHub-UpdateCheck",
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "current": current_version}

    tag = str(data.get("tag_name") or "").lstrip("vV")
    cur = str(current_version or "").lstrip("vV")
    assets = []
    for a in data.get("assets") or []:
        url = a.get("browser_download_url") or ""
        if not url:
            continue
        assets.append({
            "name": a.get("name"),
            "size_mb": round((a.get("size") or 0) / 1048576, 1),
            "url": url,
            "mirrors": {label: prefix + url for label, prefix in MIRRORS.items()},
        })
    body = str(data.get("body") or "")
    return {
        "latest": tag,
        "current": cur,
        "has_update": _ver_tuple(tag) > _ver_tuple(cur),
        "published_at": data.get("published_at") or "",
        "notes": body[:2000],
        "html_url": data.get("html_url") or f"https://github.com/{REPO}/releases/latest",
        "assets": assets,
    }
