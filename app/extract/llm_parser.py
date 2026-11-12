"""本地 mini 大模型字段抽取（Qwen2.5-1.5B-Instruct GGUF，llama.cpp 纯 CPU，完全离线）。

定位：规则解析（parser.py）负责快与稳；LLM 负责非标准简历的兜底抽取与补全。
- 懒加载：首次使用才加载模型（1.5B Q4 约 1GB 内存，加载 2-5 秒）
- 线程锁：Llama 实例非线程安全，串行化
- 优雅降级：llama_cpp 未安装 / 模型文件缺失 / 推理异常 → 返回 None，主流程回退纯规则结果
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Optional

from .. import config

_llm = None
_lock = threading.Lock()
_load_error: Optional[str] = None
_loaded_path: Optional[str] = None

# LLM 需要输出的全部字段（与 DB 列一一对应）
LLM_FIELDS = [
    "name", "gender", "age", "phone", "email", "wechat", "city", "hometown",
    "political_status", "education", "school", "major", "graduate_year",
    "work_years", "current_company", "current_title", "expected_title",
    "expected_salary", "skills", "certificates", "languages", "summary",
]

SYSTEM_PROMPT = "你是简历信息抽取引擎。只输出一个 JSON 对象，禁止输出解释、markdown 或代码块标记。找不到的字段填 null，禁止编造。"

FEW_SHOT = """示例（学习输出格式与字段口径）：
简历片段："我叫陈晓东，男，1996年生，手机13712345678，现居杭州，本科毕业于浙江工业大学自动化专业，2018年毕业。2018.07-至今 在杭州大华技术股份有限公司做嵌入式工程师，会C语言、STM32、Linux驱动。期望职位：嵌入式开发，期望薪资18-25K。"
输出：{"name":"陈晓东","gender":"男","age":29,"phone":"13712345678","email":null,"wechat":null,"city":"杭州","hometown":null,"political_status":null,"education":"本科","school":"浙江工业大学","major":"自动化","graduate_year":2018,"work_years":7,"current_company":"杭州大华技术股份有限公司","current_title":"嵌入式工程师","expected_title":"嵌入式开发","expected_salary":"18-25K","skills":["C语言","STM32","Linux驱动"],"certificates":[],"languages":[],"work_history":[{"period":"2018.07-至今","company":"杭州大华技术股份有限公司","title":"嵌入式工程师"}],"summary":"7年嵌入式开发经验"}
"""

PROMPT_TEMPLATE = """从下面的简历文本中抽取字段，只输出一个 JSON 对象，不要输出任何其他文字或代码块标记。

字段要求（找不到的填 null，不要编造；work_history 按时间倒序，最多5段）：
{{
  "name": "姓名", "gender": "男或女", "age": 数字,
  "phone": "手机号(纯数字11位)", "email": "邮箱", "wechat": "微信号",
  "city": "现居城市", "hometown": "籍贯", "political_status": "政治面貌",
  "education": "最高学历(博士/硕士/本科/大专/中专/高中)", "school": "毕业院校",
  "major": "专业", "graduate_year": 数字(毕业年份),
  "work_years": 数字(工作年限),
  "current_company": "最近一家公司全称", "current_title": "最近职位",
  "expected_title": "期望职位", "expected_salary": "期望薪资(如 15-20K)",
  "skills": ["技能1","技能2"], "certificates": ["证书1"],
  "languages": ["语言能力1"],
  "work_history": [{{"period": "2020.03-至今", "company": "公司", "title": "职位"}}],
  "summary": "一句话概括(50字内)"
}}

简历文本：
\"\"\"
{text}
\"\"\"

JSON:"""


def find_model() -> Optional[Path]:
    """在 data/models/ 下自动发现 gguf 模型（优先 config 指定路径）。"""
    cfg = config.get()
    p = (cfg.get("llm_model_path") or "").strip()
    if p and Path(p).exists():
        return Path(p)
    models_dir = config.DATA_DIR / "models"
    if not models_dir.exists():
        return None
    ggufs = sorted(models_dir.glob("*.gguf"), key=lambda f: -f.stat().st_size)
    return ggufs[0] if ggufs else None


_llm_fingerprint: Optional[str] = None   # 已加载实例的配置指纹（模型路径+GPU层数）


def _fingerprint() -> str:
    cfg = config.get()
    m = find_model()
    return f"{m}|{cfg.get('llm_gpu_layers', 0)}"


def reset_llm() -> None:
    """卸载已加载模型（下载/导入新模型、切换路径或 GPU 设置后调用；下次使用自动按新配置加载）。"""
    global _llm, _loaded_path, _llm_fingerprint, _load_error
    with _lock:
        _llm = None
        _loaded_path = None
        _llm_fingerprint = None
        _load_error = None


def _get_llm():
    """懒加载 Llama 实例；配置指纹变化（换模型/换GPU层数）自动重载——设置切换即时生效。"""
    global _llm, _load_error, _loaded_path, _llm_fingerprint
    fp = _fingerprint()
    if _llm is not None and _llm_fingerprint == fp:
        return _llm
    with _lock:
        if _llm is not None and _llm_fingerprint == fp:
            return _llm
        if _llm is not None:      # 指纹变了 → 卸载旧实例，按新配置重载
            _llm = None
            _loaded_path = None
        try:
            from llama_cpp import Llama
        except ImportError as e:
            _load_error = "llama-cpp-python 未安装（run.bat/run.sh 会自动安装；或 pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu）"
            raise RuntimeError(_load_error) from e
        model = find_model()
        if model is None:
            _load_error = f"未找到 GGUF 模型文件，请将模型放入 {config.DATA_DIR / 'models'}/（推荐 Qwen2.5-1.5B-Instruct q4_k_m）"
            raise RuntimeError(_load_error) from e
        try:
            import os
            gpu_layers = int(config.get().get("llm_gpu_layers", 0) or 0)
            _llm = Llama(
                model_path=str(model),
                n_ctx=4096,
                n_threads=max(2, (os.cpu_count() or 4) - 1),
                n_gpu_layers=-1 if gpu_layers < 0 else gpu_layers,  # -1=全部层卸载到 GPU
                verbose=False,
            )
            _loaded_path = str(model)
            _llm_fingerprint = fp
            _load_error = None
        except Exception as e:
            _load_error = f"{type(e).__name__}: {e}"
            _llm = None
            _llm_fingerprint = None
            raise RuntimeError(f"模型加载失败: {_load_error}") from e
    return _llm


def api_ready() -> tuple[bool, str]:
    """外部 API 引擎配置完备性检查。"""
    cfg = config.get()
    base = (cfg.get("llm_api_base") or "").strip()
    key = (cfg.get("llm_api_key") or "").strip()
    model = (cfg.get("llm_api_model") or "").strip()
    if not base:
        return False, "未配置 API 地址（如 https://api.deepseek.com/v1）"
    if not key:
        return False, "未配置 API Key"
    if not model:
        return False, "未配置模型名（如 deepseek-chat）"
    return True, f"{model} @ {base}"


def _extract_api(text: str, max_input_chars: int = 6000) -> Optional[dict]:
    """外部 OpenAI 兼容 API 引擎（联网大模型：DeepSeek/OpenAI/通义/Kimi 等）。"""
    import urllib.request
    cfg = config.get()
    base = (cfg.get("llm_api_base") or "").strip().rstrip("/")
    url = f"{base}/chat/completions"
    prompt = PROMPT_TEMPLATE.format(text=text[:max_input_chars])
    payload = json.dumps({
        "model": cfg.get("llm_api_model", "").strip(),
        "messages": [
            {"role": "system", "content": "你是简历信息抽取引擎，只输出 JSON，不输出任何其他内容。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 1200,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {(cfg.get('llm_api_key') or '').strip()}",
    })
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:200]
        except Exception:
            pass
        raise RuntimeError(f"API 调用失败 HTTP {e.code}: {body}") from e
    except Exception as e:
        raise RuntimeError(f"API 调用失败: {type(e).__name__}: {e}") from e
    try:
        raw = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"API 响应结构异常: {str(data)[:200]}") from e
    global _last_api_usage
    _last_api_usage = data.get("usage") or {}   # token 用量供 extract() 统一落库
    obj = _extract_json(raw)
    return _normalize(obj) if obj else None


def status() -> dict:
    """AI 引擎状态（不触发加载）：engine=local|api，含各自就绪信息。"""
    import importlib.util
    cfg = config.get()
    mode = "api" if cfg.get("llm_engine") == "api" else "local"
    llm_on = bool(cfg.get("llm_enabled", True))
    out = {
        "mode": mode,
        "enabled_cfg": llm_on,
        "gpu_layers": int(cfg.get("llm_gpu_layers", 0) or 0),
        "load_error": _load_error,
    }
    if mode == "api":
        ready, detail = api_ready()
        out.update({
            "engine": f"外部 API（联网大模型）：{detail}",
            "api_ready": ready,
            "api_base": cfg.get("llm_api_base", ""),
            "api_model": cfg.get("llm_api_model", ""),
            "api_key_set": bool((cfg.get("llm_api_key") or "").strip()),
            "enabled": llm_on and ready,
        })
    else:
        installed = importlib.util.find_spec("llama_cpp") is not None
        model = find_model()
        out.update({
            "installed": installed,
            "model_found": bool(model),
            "model_path": str(model) if model else "",
            "model_name": model.name if model else "",
            "loaded": _llm is not None,
            "engine": "本地 GGUF 小模型（llama.cpp，离线）" + ("·GPU" if out["gpu_layers"] != 0 else "·CPU"),
            "enabled": llm_on and installed and bool(model),
        })
    return out


def _extract_json(raw: str) -> Optional[dict]:
    """从 LLM 输出里稳健提取 JSON：去代码围栏 → 找首个配平的 {...}。"""
    t = re.sub(r"```(?:json)?", "", raw).strip()
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(t)):
        c = t[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(t[start:i + 1])
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def _normalize(obj: dict) -> dict:
    """把 LLM JSON 规范化为解析器同款字段 dict。"""
    out: dict = {}
    for k in LLM_FIELDS:
        v = obj.get(k)
        if v is None:
            continue
        if isinstance(v, list):
            v = "、".join(str(x).strip() for x in v if str(x).strip())
        if isinstance(v, str):
            v = v.strip()
            if not v or v.lower() in ("null", "none", "无", "未知"):
                continue
        if k == "age":
            m = re.search(r"\d{2}", str(v))
            out[k] = int(m.group(0)) if m else None
        elif k == "graduate_year":
            m = re.search(r"(19|20)\d{2}", str(v))
            out[k] = int(m.group(0)) if m else None
        elif k == "work_years":
            try:
                f = float(re.search(r"\d+(?:\.\d+)?", str(v)).group(0))
                out[k] = f if 0.5 <= f <= 50 else None
            except (AttributeError, ValueError):
                out[k] = None
        elif k == "phone":
            m = re.search(r"1[3-9]\d{9}", re.sub(r"\D", "", str(v)))
            out[k] = m.group(0) if m else None
        elif k == "email":
            ev = str(v).strip()
            # 常见非标记法归一：# / （at） / [at] → @
            if "@" not in ev:
                ev = re.sub(r"#|（at）|\(at\)|\[at\]|\bat\b", "@", ev, count=1)
            out[k] = ev.lower() if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", ev) else ev.lower()
        else:
            out[k] = v
    wh = obj.get("work_history")
    if isinstance(wh, list) and wh:
        clean = []
        for seg in wh[:5]:
            if isinstance(seg, dict) and (seg.get("company") or seg.get("title")):
                clean.append({"period": str(seg.get("period") or ""),
                              "company": str(seg.get("company") or ""),
                              "title": str(seg.get("title") or "")})
        if clean:
            out["work_history"] = json.dumps(clean, ensure_ascii=False)
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def extract(text: str, max_input_chars: int = 3500) -> Optional[dict]:
    """AI 抽取主入口：按配置分发 本地GGUF / 外部API 引擎。
    返回规范化字段 dict；任何失败返回 None（主流程回退规则结果）。
    每次调用自动记录 token 用量（ai_usage 表）。"""
    if not text or not text.strip():
        return None
    t0 = time.time()
    cfg = config.get()
    if cfg.get("llm_engine") == "api":
        obj = _extract_api(text, max_input_chars=6000)  # 联网大模型上下文宽裕，多给文本
        _record_usage("api", cfg.get("llm_api_model", ""), "resume_parse",
                      _last_api_usage, time.time() - t0)
        return obj
    llm = _get_llm()
    user_prompt = FEW_SHOT + "\n" + PROMPT_TEMPLATE.format(text=text[:max_input_chars])
    with _lock:
        resp = llm.create_chat_completion(
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": user_prompt}],
            max_tokens=1000, temperature=0.1, stream=False)
    usage = (resp or {}).get("usage") or {}
    _record_usage("local", Path(_loaded_path or "").name, "resume_parse",
                  usage, time.time() - t0)
    try:
        raw = resp["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raw = ""
    obj = _extract_json(raw)
    if not obj:
        return None
    return _normalize(obj)


# ---------- token 用量记录 ----------
_last_api_usage: dict = {}   # 最近一次 API 调用的 usage（_extract_api 内填充）


def _record_usage(engine: str, model: str, scene: str, usage: dict, elapsed_s: float):
    """统一 token 用量落库；统计失败绝不影响主流程。"""
    try:
        from .. import db
        u = usage or {}
        pt = int(u.get("prompt_tokens") or u.get("prompt") or 0)
        ct = int(u.get("completion_tokens") or u.get("completion") or 0)
        db.record_ai_usage(engine, model or "-", scene, pt, ct, int(elapsed_s * 1000))
    except Exception:
        pass


# ---------- AI 对话（内置模型 + 外部 API 均可聊） ----------

def chat_stream(messages: list[dict]):
    """流式对话生成器：yield ("delta", 文本增量) / ("usage", dict) / ("error", msg)。
    messages 为 OpenAI 格式 [{role, content}]；按配置走本地 GGUF 或外部 API。"""
    t0 = time.time()
    if config.get().get("llm_engine") == "api":
        yield from _chat_stream_api(messages)
    else:
        yield from _chat_stream_local(messages, t0)


def _chat_stream_local(messages: list, t0: float):
    try:
        llm = _get_llm()
    except Exception as e:
        yield ("error", str(e))
        return
    full = ""
    usage: dict = {}
    try:
        stream = llm.create_chat_completion(
            messages=messages, max_tokens=1024, temperature=0.7, stream=True)
        for chunk in stream:
            try:
                delta = (chunk.get("choices") or [{}])[0].get("delta", {}).get("content") or ""
            except (AttributeError, IndexError):
                delta = ""
            if chunk.get("usage"):
                usage = chunk["usage"]
            if delta:
                full += delta
                yield ("delta", delta)
    except Exception as e:
        yield ("error", f"{type(e).__name__}: {e}")
        return
    if not usage:
        # 流式无 usage 时按字符估算（中文约 0.75 token/字），量级正确供参考
        prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
        usage = {"prompt_tokens": max(1, int(prompt_chars * 0.75)),
                 "completion_tokens": max(1, int(len(full) * 0.75)),
                 "estimated": True}
    _record_usage("local", Path(_loaded_path or "").name, "chat", usage, time.time() - t0)
    yield ("usage", usage)


def _chat_stream_api(messages: list):
    import urllib.error
    import urllib.request
    cfg = config.get()
    base = (cfg.get("llm_api_base") or "").strip().rstrip("/")
    if not base or not (cfg.get("llm_api_key") or "").strip():
        yield ("error", "外部 API 未配置完整（地址/Key），请在设置页填写或切回本地引擎")
        return
    t0 = time.time()
    payload = json.dumps({
        "model": cfg.get("llm_api_model", "").strip(),
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 2048,
        "stream": True,
        "stream_options": {"include_usage": True},
    }).encode("utf-8")
    req = urllib.request.Request(f"{base}/chat/completions", data=payload, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {(cfg.get('llm_api_key') or '').strip()}",
        "Accept": "text/event-stream",
    })
    usage: dict = {}
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if obj.get("usage"):
                    usage = obj["usage"]
                choices = obj.get("choices") or []
                if choices:
                    delta = (choices[0].get("delta") or {}).get("content") or ""
                    if delta:
                        yield ("delta", delta)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:200]
        except Exception:
            pass
        yield ("error", f"API HTTP {e.code}: {body}")
        return
    except Exception as e:
        yield ("error", f"{type(e).__name__}: {e}")
        return
    _record_usage("api", cfg.get("llm_api_model", ""), "chat", usage, time.time() - t0)
    if usage:
        yield ("usage", usage)


def fetch_api_models(base: str, key: str) -> list[str]:
    """拉取外部 OpenAI 兼容服务的模型列表（GET {base}/models），供设置页下拉选择。"""
    import urllib.error
    import urllib.request
    base = (base or "").strip().rstrip("/")
    if not re.match(r"^https?://", base):
        raise ValueError("API 地址需以 http:// 或 https:// 开头")
    req = urllib.request.Request(f"{base}/models", headers={
        "Authorization": f"Bearer {(key or '').strip()}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ValueError(f"获取模型列表失败 HTTP {e.code}（检查 API 地址与 Key）") from e
    except Exception as e:
        raise ValueError(f"获取模型列表失败: {type(e).__name__}: {e}") from e
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("响应结构异常：应为 {data: [{id: ...}]}")
    return sorted({str(i.get("id")) for i in items if isinstance(i, dict) and i.get("id")})[:300]


def enhance(text: str, rule_result: dict) -> tuple[dict, bool]:
    """规则结果 + AI 分层融合。返回 (融合后dict, llm_used)。AI 失败时原样返回规则结果。

    信任分层（实测驱动：规则正则在非标简历上会抓到垃圾值，如姓名抓到"党员"、
    学校抓到"年在安徽财经大学"；而 LLM 对数字/邮箱可能幻觉改写）：
    - LLM_OVERLAY：语义类字段，AI 值非空即覆盖规则值（修正"非空但错误"）
    - RULE_KEEP：  数字/邮箱类字段，规则正则极稳，AI 仅在规则为空时补
    - MERGE_LIST： 技能/证书/语言，两侧归一化合并去重
    - TIME_FIELDS：「至今」锚点命中时规则优先（小模型对时间先后判断不稳）

    v1.5：产出 _ai_trace 工作明细（引擎/模型/耗时/tokens/逐字段改动 before→after），
    前端「AI 工作明细」展示——让用户看懂大模型干了什么、改了什么、为什么。
    """
    LLM_OVERLAY = {"name", "gender", "city", "hometown", "political_status", "education",
                   "school", "major", "expected_title",
                   "expected_salary", "summary"}
    RULE_KEEP = {"phone", "email", "age", "graduate_year"}
    MERGE_LIST = {"skills", "certificates", "languages"}
    TIME_FIELDS = {"current_company", "current_title", "work_history", "work_years"}

    t0 = time.time()
    st = status()
    try:
        llm_fields = extract(text)
    except Exception:
        llm_fields = None
    if not llm_fields:
        return rule_result, False
    merged = dict(rule_result)
    changed = 0
    anchor_now = bool(rule_result.get("_anchor_now"))
    trace: list[dict] = []

    def _empty(v):
        return v is None or (isinstance(v, str) and not v.strip())

    def _norm_key(x: str) -> str:
        return re.sub(r"[\s\-—_（）()]", "", str(x)).lower()

    def _set(k: str, v, rule: str):
        nonlocal changed
        before = merged.get(k)
        merged[k] = v
        changed += 1
        trace.append({"field": k, "before": before if not _empty(before) else None,
                      "after": v, "rule": rule})

    for k, v in llm_fields.items():
        cur = merged.get(k)
        if k in TIME_FIELDS:
            if k == "work_years":
                rv, lv = cur, v
                if isinstance(rv, (int, float)) and isinstance(lv, (int, float)) and lv > rv:
                    _set(k, lv, "年限取较大值（规则与AI各自可能偏小）")
                elif _empty(rv) and not _empty(lv):
                    _set(k, lv, "规则未识别，AI 补充")
            elif anchor_now:
                if _empty(cur) and not _empty(v):
                    _set(k, v, "规则空，AI 补充")
                # 锚点命中：规则值优先，AI 不覆盖（小模型时间判断不稳）
            else:
                if not _empty(v) and cur != v:
                    _set(k, v, "无「至今」锚点，AI 判断优先")
        elif k in LLM_OVERLAY:
            if not _empty(v) and cur != v:
                _set(k, v, "AI 语义理解覆盖规则值" if not _empty(cur) else "规则未识别，AI 补充")
        elif k in RULE_KEEP:
            if _empty(cur) and not _empty(v):
                _set(k, v, "规则未识别，AI 补充")
        elif k in MERGE_LIST and isinstance(v, str):
            cur_items = [x for x in re.split(r"[、,，;；/|]", str(cur or "")) if x.strip()]
            ai_items = [x for x in re.split(r"[、,，;；/|]", v) if x.strip()]
            if k == "languages":
                # AI 常把编程语言混进"语言能力"，按词典过滤（保留 英语/日语/CET/N1 等真语言项）
                _prog = {"python", "java", "c++", "c", "c#", "javascript", "js", "go", "golang",
                         "rust", "php", "sql", "html", "css", "vue", "react", "typescript", "ts"}
                ai_items = [x for x in ai_items
                            if x.strip().lower() not in _prog
                            and re.search(r"[语英日俄法德西韩朝意葡]|CET|TEM|雅思|托福|IELTS|TOEFL|N[1-5]|普通话|母语", x, re.I)]
            keys = {_norm_key(x) for x in cur_items}
            added = [x for x in ai_items if _norm_key(x) not in keys
                     and not any(_norm_key(x) in kk or kk in _norm_key(x) for kk in keys)]
            if added:
                _set(k, "、".join(cur_items + added), f"AI 补充合并去重 +{'、'.join(added)[:40]}")

    elapsed = round(time.time() - t0, 1)
    merged["_ai_trace"] = {
        "engine": "本地模型" if st.get("mode") != "api" else "外部 API",
        "model": st.get("model_name") or st.get("api_model") or "",
        "elapsed_s": elapsed,
        "changed_fields": changed,
        "changes": trace[:30],
        "anchor_protected": anchor_now,
        "explain": (f"规则解析先跑（置信度 {rule_result.get('parse_confidence')}），"
                    f"AI 引擎（{st.get('model_name') or st.get('api_model') or '-'}）再通读全文抽取 22 字段，"
                    f"按信任分层融合：语义字段 AI 优先、电话/邮箱等规则优先防幻觉、"
                    f"{'当前公司命中「至今」锚点由规则保护、' if anchor_now else ''}"
                    f"本次 AI 修改/补充了 {changed} 个字段，耗时 {elapsed}s。"),
    }
    return merged, changed > 0
