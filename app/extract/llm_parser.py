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

PROMPT_TEMPLATE = """你是一个简历信息抽取引擎。从下面的简历文本中抽取字段，只输出一个 JSON 对象，不要输出任何其他文字或代码块标记。

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


def _get_llm():
    """懒加载 Llama 实例；失败记录原因并抛 RuntimeError。"""
    global _llm, _load_error, _loaded_path
    if _llm is not None:
        return _llm
    with _lock:
        if _llm is not None:
            return _llm
        try:
            from llama_cpp import Llama
        except ImportError as e:
            _load_error = "llama-cpp-python 未安装（pip install llama-cpp-python，详见 README「AI 智能解析」）"
            raise RuntimeError(_load_error) from e
        model = find_model()
        if model is None:
            _load_error = f"未找到 GGUF 模型文件，请将模型放入 {config.DATA_DIR / 'models'}/（推荐 Qwen2.5-1.5B-Instruct q4_k_m）"
            raise RuntimeError(_load_error)
        try:
            import os
            _llm = Llama(
                model_path=str(model),
                n_ctx=4096,
                n_threads=max(2, (os.cpu_count() or 4) - 1),
                verbose=False,
            )
            _loaded_path = str(model)
            _load_error = None
        except Exception as e:
            _load_error = f"{type(e).__name__}: {e}"
            _llm = None
            raise RuntimeError(f"模型加载失败: {_load_error}") from e
    return _llm


def status() -> dict:
    """LLM 可用性状态（不触发加载）。"""
    import importlib.util
    installed = importlib.util.find_spec("llama_cpp") is not None
    model = find_model()
    return {
        "installed": installed,
        "model_found": bool(model),
        "model_path": str(model) if model else "",
        "model_name": model.name if model else "",
        "loaded": _llm is not None,
        "engine": "Qwen2.5-1.5B-Instruct GGUF (llama.cpp, 纯CPU离线)",
        "load_error": _load_error,
        "enabled": bool(config.get().get("llm_enabled", True)) and installed and bool(model),
    }


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
    """LLM 抽取主入口：返回规范化字段 dict；任何失败返回 None（主流程回退规则结果）。"""
    if not text or not text.strip():
        return None
    llm = _get_llm()
    prompt = PROMPT_TEMPLATE.format(text=text[:max_input_chars])
    with _lock:
        resp = llm(prompt, max_tokens=900, temperature=0.1, stop=["</s>", "\n\n\n"])
    raw = resp["choices"][0]["text"] if resp.get("choices") else ""
    obj = _extract_json(raw)
    if not obj:
        return None
    return _normalize(obj)


def enhance(text: str, rule_result: dict) -> tuple[dict, bool]:
    """规则结果 + LLM 分层融合。返回 (融合后dict, llm_used)。LLM 失败时原样返回规则结果。

    信任分层（实测驱动：规则正则在非标简历上会抓到垃圾值，如姓名抓到"党员"、
    学校抓到"年在安徽财经大学"；而 LLM 对数字/邮箱可能幻觉改写）：
    - LLM_OVERLAY：语义类字段，LLM 值非空即覆盖规则值（修正"非空但错误"）
    - RULE_KEEP：  数字/邮箱类字段，规则正则极稳，LLM 仅在规则为空时补
    - MERGE_LIST： 技能/证书/语言，两侧归一化合并去重
    """
    LLM_OVERLAY = {"name", "gender", "city", "hometown", "political_status", "education",
                   "school", "major", "expected_title",
                   "expected_salary", "summary"}
    RULE_KEEP = {"phone", "email", "age", "graduate_year"}
    MERGE_LIST = {"skills", "certificates", "languages"}
    # 时间语义字段：1.5B 小模型对"哪段是最近的工作"判断不稳（实测会把两年前旧公司当现任），
    # 而规则的「至今」锚点命中时非常可靠 —— 锚点命中则规则优先，否则 LLM 优先。
    TIME_FIELDS = {"current_company", "current_title", "work_history", "work_years"}

    try:
        llm_fields = extract(text)
    except Exception:
        llm_fields = None
    if not llm_fields:
        return rule_result, False
    merged = dict(rule_result)
    changed = 0
    anchor_now = bool(rule_result.get("_anchor_now"))

    def _empty(v):
        return v is None or (isinstance(v, str) and not v.strip())

    def _norm_key(s: str) -> str:
        # 归一去重键："CET-6"≈"CET6"、"注册会计师专业阶段"≈"注册会计师(专业阶段)"取包含关系
        return re.sub(r"[\s\-—_（）()]", "", str(s)).lower()

    for k, v in llm_fields.items():
        cur = merged.get(k)
        if k in TIME_FIELDS:
            if k == "work_years":
                # 年限：规则显式表述或 LLM 语义值都可能偏小，取两者较大（范围已由 _normalize 校验）
                rv, lv = cur, v
                if isinstance(rv, (int, float)) and isinstance(lv, (int, float)) and lv > rv:
                    merged[k] = lv
                    changed += 1
                elif _empty(rv) and not _empty(lv):
                    merged[k] = lv
                    changed += 1
            elif anchor_now:
                if _empty(cur) and not _empty(v):   # 锚点命中：规则优先，LLM 只补空
                    merged[k] = v
                    changed += 1
            else:
                if not _empty(v) and cur != v:      # 无锚点：LLM 优先
                    merged[k] = v
                    changed += 1
        elif k in LLM_OVERLAY:
            if not _empty(v) and cur != v:
                merged[k] = v
                changed += 1
        elif k in RULE_KEEP:
            if _empty(cur) and not _empty(v):
                merged[k] = v
                changed += 1
        elif k in MERGE_LIST and isinstance(v, str):
            if _empty(cur):
                merged[k] = v
                changed += 1
            else:
                parts = [x.strip() for x in re.split(r"[、,，]", cur) if x.strip()]
                have = {_norm_key(x) for x in parts}
                extra = []
                for x in (y.strip() for y in re.split(r"[、,，]", v)):
                    nk = _norm_key(x)
                    if not x or nk in have:
                        continue
                    # 包含关系也算重复（"注册会计师专业阶段" vs "注册会计师"）
                    if any(nk in h or h in nk for h in have):
                        continue
                    extra.append(x)
                    have.add(nk)
                if extra:
                    merged[k] = cur + "、" + "、".join(extra[:8])
                    changed += 1
    if changed:
        weights = [
            (bool(merged.get("name")), .20), (bool(merged.get("phone")), .14),
            (bool(merged.get("email")), .08), (bool(merged.get("education")), .09),
            (merged.get("work_years") is not None, .07), (bool(merged.get("skills")), .09),
            (bool(merged.get("current_company")), .08), (bool(merged.get("current_title")), .05),
            (bool(merged.get("city")), .04), (bool(merged.get("expected_title") or merged.get("summary")), .04),
            (bool(merged.get("work_history")), .06), (bool(merged.get("school")), .06),
        ]
        merged["parse_confidence"] = round(sum(w for ok, w in weights if ok), 3)
    return merged, changed > 0
