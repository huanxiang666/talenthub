#!/usr/bin/env python3
"""端到端验收测试：对运行中的 TalentHub 服务打真实 HTTP 请求，覆盖全链路。

前置：服务已在 BASE 地址运行；samples/ 已由 make_samples.py 生成。
用法：python scripts/e2e_test.py [base_url]   （默认 http://127.0.0.1:8300）

覆盖：健康检查 → 设置读写 → 简历导入(文字PDF/OCR扫描PDF/Word) → 草稿入库 →
搜索/筛选 → 表格导入预览+合并(fill) → 手动新增 → 批量操作 → 跟进记录 →
Excel/CSV/JSON 导出读回对账 → JSON 合并幂等 → 原件下载 → 统计 → 删除 → 事件日志。
每项断言失败立即抛错退出（红即停），全部通过打印汇总。
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import requests
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8300").rstrip("/")

PASSED = []


def check(name: str, cond: bool, detail: str = ""):
    if not cond:
        raise AssertionError(f"[FAIL] {name} {detail}")
    PASSED.append(name)
    print(f"  ✓ {name}")


def main():
    s = requests.Session()

    print("== 1. 健康与系统信息 ==")
    h = s.get(f"{BASE}/api/health", timeout=10).json()
    check("health.ok", h["ok"] is True)
    sysinfo = s.get(f"{BASE}/api/system", timeout=10).json()
    check("system.urls 非空", len(sysinfo["urls"]) >= 1, str(sysinfo["urls"]))
    check("system.ocr installed", sysinfo["ocr"]["installed"] is True)

    print("== 2. 设置读写 ==")
    cfg0 = s.get(f"{BASE}/api/settings", timeout=10).json()
    r = s.put(f"{BASE}/api/settings", json={"ocr_max_pages": 25}, timeout=10)
    check("PUT settings 200", r.status_code == 200 and r.json()["config"]["ocr_max_pages"] == 25)
    r = s.put(f"{BASE}/api/settings", json={"host": "8.8.8.8"}, timeout=10)
    check("非法 host 被拒", r.status_code == 400, str(r.text[:80]))
    s.put(f"{BASE}/api/settings", json={"ocr_max_pages": cfg0.get("ocr_max_pages", 30)}, timeout=10)

    print("== 3. 简历导入：文字PDF + 扫描PDF(OCR) + Word ==")
    files = [
        ("files", ("张伟-Java开发工程师-8年.pdf", (SAMPLES / "张伟-Java开发工程师-8年.pdf").read_bytes(), "application/pdf")),
        ("files", ("李娜-质量经理-扫描版.pdf", (SAMPLES / "李娜-质量经理-扫描版.pdf").read_bytes(), "application/pdf")),
        ("files", ("王强-机械设计工程师.docx", (SAMPLES / "王强-机械设计工程师.docx").read_bytes(),
                   "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
    ]
    r = s.post(f"{BASE}/api/import/resumes", files=files, timeout=300)
    check("import/resumes 200", r.status_code == 200, str(r.text[:200]))
    imp = r.json()
    check("3 份全部解析成功", imp["parsed"] == 3, json.dumps(imp["results"], ensure_ascii=False)[:400])
    by_name = {res["draft"]["name"]: res for res in imp["results"] if res["ok"]}
    check("张伟=文字层直读", by_name["张伟"]["method"] == "pdf_text")
    check("李娜=OCR 识别", by_name["李娜"]["method"] == "pdf_ocr" and by_name["李娜"]["ocr_used"] is True)
    check("王强=docx", by_name["王强"]["method"] == "docx")
    check("张伟电话正确", by_name["张伟"]["draft"]["phone"] == "13812345678")
    check("李娜(OCR)电话正确", by_name["李娜"]["draft"]["phone"] == "13957654321")
    check("李娜(OCR)专业=机械工程", by_name["李娜"]["draft"]["major"] == "机械工程")
    check("王强邮箱正确", by_name["王强"]["draft"]["email"] == "wangqiang.me@example.com")
    check("置信度均≥0.8", all(res["draft"]["parse_confidence"] >= 0.8 for res in imp["results"] if res["ok"]))

    print("== 4. 草稿确认入库 ==")
    drafts = [res["draft"] for res in imp["results"] if res["ok"]]
    r = s.post(f"{BASE}/api/talents/bulk", json={"drafts": drafts, "strategy": "skip"}, timeout=60)
    bulk = r.json()
    check("入库 3 条", bulk["inserted"] == 3, json.dumps(bulk, ensure_ascii=False)[:200])

    print("== 5. 搜索 / 筛选 / 详情 ==")
    r = s.get(f"{BASE}/api/talents", params={"q": "张伟"}, timeout=10).json()
    check("搜索张伟=1 条", r["total"] == 1)
    zw = r["items"][0]
    check("张伟学历=本科", zw["education"] == "本科")
    r = s.get(f"{BASE}/api/talents", params={"skill": "六西格玛"}, timeout=10).json()
    check("技能筛选六西格玛命中李娜", r["total"] == 1 and r["items"][0]["name"] == "李娜")
    one = s.get(f"{BASE}/api/talents/{zw['id']}", timeout=10).json()
    check("详情含原文", "自我评价" in (one.get("raw_text") or ""))

    print("== 6. 原件下载一致性 ==")
    rf = s.get(f"{BASE}/api/files/{zw['id']}", timeout=10)
    check("原件下载 200", rf.status_code == 200)
    check("原件字节一致", rf.content == (SAMPLES / "张伟-Java开发工程师-8年.pdf").read_bytes())

    print("== 7. 表格导入：预览 + fill 合并 ==")
    xf = (SAMPLES / "外部人才名单.xlsx").read_bytes()
    r = s.post(f"{BASE}/api/import/table/preview",
               files={"up": ("外部人才名单.xlsx", xf)}, timeout=60)
    check("table/preview 200", r.status_code == 200, str(r.text[:200]))
    prev = r.json()
    check("识别 3 行", len(prev["rows"]) == 3)
    check("别名表头映射(手机号码→phone)", any(f == "phone" for f in prev["mapping"].values()))
    check("重复 1 条(张伟按电话)", prev["duplicate_count"] == 1, json.dumps(prev["duplicates"], ensure_ascii=False))
    dup_row = next(d for d in prev["duplicates"] if d["duplicate_of"])
    check("重复判定=电话", dup_row["matched_by"] == "电话")
    r = s.post(f"{BASE}/api/import/table/commit",
               json={"rows": prev["rows"], "strategy": "fill", "selected": None}, timeout=60)
    com = r.json()
    check("fill 合并：新增 2", com["inserted"] == 2, json.dumps(com, ensure_ascii=False)[:300])
    check("fill 合并：张伟跳过(字段已全)", com["skipped"] == 1)
    zw2 = s.get(f"{BASE}/api/talents/{zw['id']}", timeout=10).json()
    check("张伟跟进备注已合并", any("招聘会" in n["text"] for n in zw2["notes"]),
          json.dumps(zw2["notes"], ensure_ascii=False))

    print("== 8. 手动新增 + 更新 + 批量 + 跟进 ==")
    r = s.post(f"{BASE}/api/talents", json={"data": {"name": "测试候选人", "phone": "13000000001",
                                                     "status": "潜在", "source": "手动录入"}}, timeout=10)
    check("手动新增 200", r.status_code == 200 and r.json()["id"] > 0)
    tid = r.json()["id"]
    r = s.post(f"{BASE}/api/talents", json={"data": {"phone": "13000000002"}}, timeout=10)
    check("无姓名被拒", r.status_code == 400)
    r = s.put(f"{BASE}/api/talents/{tid}", json={"data": {"rating": "a", "education": "博士"}}, timeout=10)
    check("更新评级 a→A", r.json()["rating"] == "A")
    r = s.post(f"{BASE}/api/talents/batch", json={"ids": [tid, zw["id"]], "action": "status", "value": "跟进中"}, timeout=10)
    check("批量改状态 x2", r.json()["affected"] == 2)
    r = s.post(f"{BASE}/api/talents/batch", json={"ids": [tid], "action": "tag_add", "value": "重点"}, timeout=10)
    check("批量打标签", r.json()["affected"] == 1)
    r = s.post(f"{BASE}/api/talents/{tid}/notes", json={"text": "电话初筛通过"}, timeout=10)
    check("跟进记录写入", any("初筛" in n["text"] for n in r.json()["notes"]))
    r = s.get(f"{BASE}/api/talents", params={"status": "跟进中"}, timeout=10).json()
    check("状态筛选=跟进中 ≥2", r["total"] >= 2)

    print("== 9. Excel 导出读回对账 ==")
    r = s.get(f"{BASE}/api/export/excel", timeout=60)
    check("export/excel 200", r.status_code == 200 and len(r.content) > 3000)
    wb = load_workbook(io.BytesIO(r.content))
    check("工作表=人才列表+数据概览", wb.sheetnames[0] == "人才列表" and "数据概览" in wb.sheetnames)
    ws = wb["人才列表"]
    total = s.get(f"{BASE}/api/talents", params={"size": 1}, timeout=10).json()["total"]
    check("导出行数=库内总数", ws.max_row - 1 == total, f"{ws.max_row - 1} vs {total}")
    header = [c.value for c in ws[1]]
    check("表头含姓名/电话/技能/状态", all(x in header for x in ("姓名", "电话", "技能", "状态")))
    names = {ws.cell(row=i, column=1).value for i in range(2, ws.max_row + 1)}
    check("导出含全部 5 人", {"张伟", "李娜", "王强", "陈晓东", "赵敏"} <= names, str(names))
    zw_row = next(i for i in range(2, ws.max_row + 1) if ws.cell(row=i, column=1).value == "张伟")
    check("张伟电话单元格正确", str(ws.cell(row=zw_row, column=header.index("电话") + 1).value) == "13812345678")

    print("== 10. 条件导出 / CSV ==")
    r = s.get(f"{BASE}/api/export/excel", params={"q": "SolidWorks"}, timeout=30)
    wb2 = load_workbook(io.BytesIO(r.content))
    check("条件导出 SolidWorks=1 行(王强)",
          wb2.active.max_row - 1 == 1 and wb2.active.cell(row=2, column=1).value == "王强")
    r = s.get(f"{BASE}/api/export/excel", params={"q": "机械"}, timeout=30)
    wb3 = load_workbook(io.BytesIO(r.content))
    check("条件导出 机械=2 行(王强+李娜专业命中)", wb3.active.max_row - 1 == 2)
    r = s.get(f"{BASE}/api/export/csv", timeout=30)
    check("CSV 带 BOM(Excel 兼容)", r.content[:3] == b"\xef\xbb\xbf")
    check("CSV 行数正确", len(r.content.decode("utf-8-sig").strip().splitlines()) == total + 1)
    r = s.get(f"{BASE}/api/export/template", timeout=30)
    wbt = load_workbook(io.BytesIO(r.content))
    check("模板含填写说明", "填写说明" in wbt.sheetnames)

    print("== 11. JSON 备份导出 + 幂等合并 ==")
    r = s.get(f"{BASE}/api/export/json", timeout=30)
    payload = r.json()
    check("JSON 备份条数一致", payload["count"] == total)
    r = s.post(f"{BASE}/api/import/json", params={"strategy": "skip"},
               files={"up": ("backup.json", json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                             "application/json")}, timeout=60)
    j = r.json()
    check("skip 策略幂等(0 新增)", j["inserted"] == 0 and j["skipped"] == total, json.dumps(j, ensure_ascii=False)[:200])

    print("== 12. 统计仪表盘数据 ==")
    st = s.get(f"{BASE}/api/stats", timeout=10).json()
    check("total 一致", st["total"] == total)
    check("学历分布含本科", any(x["name"] == "本科" for x in st["by_education"]))
    check("趋势 12 个月", len(st["monthly_trend"]) == 12)
    check("本月新增≥6", st["month_new"] >= 6, str(st["month_new"]))
    check("技能TOP含Java", any(x["name"] == "Java" for x in st["top_skills"]))
    check("高潜(A/B)统计存在", "high_potential" in st)

    print("== 13. 删除 + 事件日志 ==")
    r = s.delete(f"{BASE}/api/talents/{tid}", timeout=10)
    check("删除测试记录", r.json()["deleted"] == 1)
    check("删除后 total 回落 1",
          s.get(f"{BASE}/api/talents", params={"size": 1}, timeout=10).json()["total"] == total - 1)
    ev = s.get(f"{BASE}/api/events", params={"limit": 30}, timeout=10).json()["items"]
    actions = {e["action"] for e in ev}
    check("日志含 import_resumes/bulk_insert/import_table/delete",
          {"import_resumes", "bulk_insert", "import_table", "delete"} <= actions, str(actions))

    print("== 14. 错误路径 ==")
    r = s.post(f"{BASE}/api/import/resumes",
               files=[("files", ("恶意.exe", b"MZ\x00\x01", "application/octet-stream"))], timeout=30)
    check("不支持类型返回失败项而非 500", r.status_code == 200 and r.json()["results"][0]["ok"] is False)
    r = s.post(f"{BASE}/api/import/resumes",
               files=[("files", ("空.pdf", b"", "application/pdf"))], timeout=30)
    check("空文件被拒", r.json()["results"][0]["ok"] is False)
    r = s.get(f"{BASE}/api/talents/999999", timeout=10)
    check("不存在记录 404", r.status_code == 404)
    r = s.post(f"{BASE}/api/import/table/preview",
               files={"up": ("bad.csv", "无关表头A,无关表头B\n1,2\n".encode("utf-8"), "text/csv")}, timeout=30)
    check("表头无法识别→400", r.status_code == 400, str(r.text[:100]))
    r = s.post(f"{BASE}/api/talents/batch", json={"ids": [1], "action": "status", "value": "不存在的状态"}, timeout=10)
    check("非法状态被拒", r.status_code == 400)

    print("== 15. 页面与静态资源 ==")
    r = s.get(f"{BASE}/", timeout=10)
    check("首页 200 且含标题", r.status_code == 200 and "TalentHub" in r.text)
    for asset in ("/style.css", "/app.js", "/vendor/echarts.min.js"):
        rr = s.get(f"{BASE}{asset}", timeout=10)
        check(f"静态资源 {asset}", rr.status_code == 200 and len(rr.content) > 500)

    print("== 16. AI 模型端点 ==")
    r = s.get(f"{BASE}/api/llm/status", timeout=10).json()
    check("llm/status 结构完整", all(k in r for k in ("installed", "model_found", "enabled", "engine")))
    r = s.get(f"{BASE}/api/llm/models", timeout=10).json()
    check("模型目录 ≥3 个且含多源站", len(r["models"]) >= 3 and
          all(len(m["urls"]) >= 2 for m in r["models"] if not m["label"].endswith("（用户自行放置）")))
    check("模型目录含 models_dir", bool(r.get("models_dir")))
    r = s.get(f"{BASE}/api/llm/download", timeout=10).json()
    check("下载进度端点结构", "active" in r and "percent" in r)
    r = s.post(f"{BASE}/api/llm/download", json={"model_id": "不存在", "source": "x"}, timeout=10)
    check("非法模型 id 被拒 400", r.status_code == 400)
    sysinfo = s.get(f"{BASE}/api/system", timeout=10).json()
    check("system 含 llm 状态", "llm" in sysinfo and "engine" in sysinfo["llm"])

    print("== 17. 新字段（v1.1）入库与导出往返 ==")
    r = s.post(f"{BASE}/api/talents", json={"data": {
        "name": "新字段测试", "phone": "13000009999", "wechat": "wx_abc",
        "hometown": "浙江宁波", "political_status": "中共党员", "graduate_year": 2020,
        "certificates": "CET-6、PMP", "languages": "英语（CET-6）",
        "work_history": '[{"period":"2020.07-至今","company":"某某公司","title":"工程师"}]',
        "project_experience": "主导过三个项目"}}, timeout=10)
    check("新字段入库", r.status_code == 200 and r.json()["wechat"] == "wx_abc")
    nid = r.json()["id"]
    one = s.get(f"{BASE}/api/talents/{nid}", timeout=10).json()
    check("work_history_list 已解析", isinstance(one.get("work_history_list"), list)
          and one["work_history_list"][0]["company"] == "某某公司")
    r = s.get(f"{BASE}/api/export/excel", params={"q": "新字段测试"}, timeout=30)
    wb4 = load_workbook(io.BytesIO(r.content))
    hdr4 = [c.value for c in wb4.active[1]]
    row4 = {h: c.value for h, c in zip(hdr4, wb4.active[2])}
    check("xlsx 含新列且值正确",
          row4.get("微信") == "wx_abc" and row4.get("籍贯") == "浙江宁波"
          and row4.get("毕业年份") == 2020 and "某某公司" in str(row4.get("工作经历")))
    r = s.get(f"{BASE}/api/export/csv", params={"q": "新字段测试"}, timeout=30)
    check("csv 含新字段", "wx_abc" in r.content.decode("utf-8-sig"))
    s.delete(f"{BASE}/api/talents/{nid}", timeout=10)

    print("== 18. v1.2：自定义状态 / 日志删除 / 版权资源 / 锁屏 / 投递 ==")
    # 自定义状态
    s.put(f"{BASE}/api/settings", json={"custom_statuses": "新线索、初筛、复试、Offer、已归档"}, timeout=10)
    sts = s.get(f"{BASE}/api/statuses", timeout=10).json()["statuses"]
    check("自定义状态生效", sts == ["新线索", "初筛", "复试", "Offer", "已归档"], str(sts))
    r = s.post(f"{BASE}/api/talents", json={"data": {"name": "状态测试", "phone": "13000008888", "status": "复试"}}, timeout=10)
    check("自定义状态可入库", r.status_code == 200 and r.json()["status"] == "复试")
    sid = r.json()["id"]
    r = s.post(f"{BASE}/api/talents/batch", json={"ids": [sid], "action": "status", "value": "跟进中"}, timeout=10)
    check("旧默认状态被自定义列表拒绝", r.status_code == 400)
    s.delete(f"{BASE}/api/talents/{sid}", timeout=10)
    s.put(f"{BASE}/api/settings", json={"custom_statuses": ""}, timeout=10)
    sts = s.get(f"{BASE}/api/statuses", timeout=10).json()["statuses"]
    check("清空自定义回退默认七状态", sts[0] == "潜在" and len(sts) == 7)
    # 日志删除
    n0 = len(s.get(f"{BASE}/api/events", timeout=10).json()["items"])
    r = s.request("DELETE", f"{BASE}/api/events", json={}, timeout=10)
    check("日志可清空", r.status_code == 200 and r.json()["deleted"] >= 1)
    n1 = len(s.get(f"{BASE}/api/events", timeout=10).json()["items"])
    check("清空后仅剩本次清空事件", n1 < n0 and n1 <= 1, f"{n0}->{n1}")
    # 版权与资源
    ab = s.get(f"{BASE}/api/about", timeout=10).json()
    check("版权信息完整", ab["copyright"]["owner"] == "慈溪八凯八威电子商务经营部"
          and "胡利杰" in ab["copyright"]["author"]
          and ab["copyright"]["phone"] == "13208088838"
          and ab["copyright"]["email"] == "admin@8k8w.com")
    res = s.get(f"{BASE}/api/system/resources", timeout=10).json()
    check("内存/GPU 探测结构", res["ram"]["total_mb"] > 0 and "available" in res["gpu"]
          and "model_id" in res["recommend"] and res["recommend"]["reason"])
    models = s.get(f"{BASE}/api/llm/models", timeout=10).json()
    check("模型目录 ≥7 档且带内存估算", len(models["models"]) >= 7
          and all(m.get("ram_mb", 0) > 0 for m in models["models"] if m.get("urls")))
    # 锁屏（API 层快验，UI 层另有 CDP 套件）
    check("锁初始未设置", s.get(f"{BASE}/api/auth/status", timeout=10).json()["set"] is False)
    check("短密码被拒", s.post(f"{BASE}/api/auth/setup", json={"password": "123", "auto_lock_minutes": 30}, timeout=10).status_code == 400)
    s.post(f"{BASE}/api/auth/setup", json={"password": "e2e123456", "auto_lock_minutes": 0}, timeout=10)
    check("锁定后无令牌 401", s.get(f"{BASE}/api/talents", timeout=10).status_code == 401)
    check("health 白名单仍通", s.get(f"{BASE}/api/health", timeout=10).status_code == 200)
    tok = s.post(f"{BASE}/api/auth/unlock", json={"password": "e2e123456"}, timeout=10).json()["token"]
    hd = {"Authorization": f"Bearer {tok}"}
    check("解锁令牌可访问", s.get(f"{BASE}/api/talents", headers=hd, timeout=10).status_code == 200)
    check("伪造令牌 401", s.get(f"{BASE}/api/talents", headers={"Authorization": "Bearer x.y"}, timeout=10).status_code == 401)
    r = s.post(f"{BASE}/api/auth/disable", json={"password": "e2e123456"}, timeout=10)
    check("锁屏可关闭", r.status_code == 200 and r.json()["enabled"] is False)
    # 投递门户（第二端口）
    APPLY = "http://127.0.0.1:8301"
    info = s.get(f"{APPLY}/apply-api/info", timeout=10).json()
    check("投递门户 info", info["captcha_required"] is True and "慈溪" in info["company"])
    cap = s.get(f"{APPLY}/apply-api/captcha", timeout=10).json()
    check("验证码图片可生成", cap["image"].startswith("data:image/png;base64,") and len(cap["captcha_id"]) > 10)
    r = s.post(f"{APPLY}/apply-api/submit",
               data={"name": "E2E投递", "phone": "13600006666", "captcha_id": cap["captcha_id"], "captcha_code": "XXXX"},
               timeout=10)
    check("错误验证码被拒", r.status_code == 400)
    lst = s.get(f"{BASE}/api/applications", timeout=10).json()
    check("管理端投递列表结构", "items" in lst and "pending" in lst and "total" in lst)

    print(f"\n全部通过：{len(PASSED)} 项断言 ✓")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\n{e}\n已通过 {len(PASSED)} 项后失败")
        sys.exit(1)
