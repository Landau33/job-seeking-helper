#!/usr/bin/env python3
"""job-seek-planner · 由 jobs.json 生成校验报告、单文件 HTML 看板和 Excel。

用法:
  python3 build_report.py jobs.json --html report.html --xlsx jobs.xlsx
  python3 build_report.py jobs.json --check-only
  python3 build_report.py jobs.json --merge-status status.json   # 合并看板导出的投递状态

错误(error)会阻止生成产物；警告(warning)只提示，并会显示在看板的「数据质量」区。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HC_STATES = ["在招", "暂停", "已关闭", "未知"]
APPLY_STATES = ["未投", "已投", "笔试", "一面", "二面", "三面", "HR面", "Offer", "挂", "暂缓"]
COMP_CONF = ["高", "中", "低", "未知"]
COMP_SOURCE = ["官方JD", "官方公告", "网传", "推断", "未知"]
SOURCE_TYPES = ["官方", "半官方", "网传", "访问失败", "其他"]
REQUIRED = ["公司", "岗位", "城市"]
STALE_DAYS = 30


# --------------------------------------------------------------------------- 工具


def stable_id(*parts: Any) -> str:
    raw = "\x1f".join(str(p or "").strip().lower() for p in parts)
    return "job_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def as_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [s.strip() for s in re.split(r"[,，/、|]", str(value)) if s.strip()]


def parse_date(value: Any) -> Optional[date]:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y-%m", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_salary(text: Any) -> Optional[float]:
    """把常见写法折算成年包（万元），无法解析返回 None。

    支持: 35-50k×15 / 30k*14 / 25-35k / 40万 / 50-60万 / 年包 45w / 面议(->None)
    """
    raw = str(text or "").strip()
    if not raw or raw in {"未知", "面议", "-", "n/a", "N/A"}:
        return None
    s = raw.lower().replace("，", ",").replace("～", "-").replace("~", "-").replace("—", "-")
    s = s.replace("×", "*").replace("x", "*").replace("X", "*")

    def _mid(a: str, b: Optional[str]) -> float:
        lo = float(a)
        hi = float(b) if b else lo
        return (lo + hi) / 2.0

    # 年包：45万 / 40-60万 / 45w
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-\s*(\d+(?:\.\d+)?))?\s*(?:万|w)", s)
    if m and "k" not in s:
        return round(_mid(m.group(1), m.group(2)), 2)
    # 月薪：35-50k*15 / 30k
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-\s*(\d+(?:\.\d+)?))?\s*k", s)
    if m:
        monthly = _mid(m.group(1), m.group(2))
        months_match = re.search(r"\*\s*(\d+(?:\.\d+)?)", s)
        months = float(months_match.group(1)) if months_match else 12.0
        return round(monthly * months / 10.0, 2)  # k*月 -> 万
    return None


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def sheet_safe(value: Any) -> Any:
    """防公式注入：以 = + - @ 开头的文本前加单引号。"""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


# --------------------------------------------------------------------------- 校验


class Issue(dict):
    def __init__(self, level: str, where: str, message: str) -> None:
        super().__init__(level=level, where=where, message=message)


def validate(data: Any) -> Tuple[List[Issue], List[Issue]]:
    errors: List[Issue] = []
    warnings: List[Issue] = []

    if not isinstance(data, dict):
        errors.append(Issue("error", "$", "顶层必须是 object，含 meta 和 jobs。"))
        return errors, warnings
    meta = data.get("meta")
    if meta is None:
        warnings.append(Issue("warning", "meta", "缺少 meta（方向/城市/薪资底线/更新时间），看板信息会不完整。"))
    elif not isinstance(meta, dict):
        errors.append(Issue("error", "meta", "meta 必须是 object。"))
    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        errors.append(Issue("error", "jobs", "jobs 必须是数组。"))
        return errors, warnings
    if not jobs:
        warnings.append(Issue("warning", "jobs", "jobs 为空，看板不会有内容。"))

    # 公司覆盖度：防止“只挑几个代表岗”造成的静默截断
    coverage = (meta or {}).get("公司覆盖") if isinstance(meta, dict) else None
    counted: Dict[str, int] = {}
    for job in jobs:
        if isinstance(job, dict) and str(job.get("公司") or "").strip():
            name = str(job["公司"]).strip()
            counted[name] = counted.get(name, 0) + 1
    if coverage is None:
        if counted:
            warnings.append(Issue("warning", "meta.公司覆盖",
                "没有 meta.公司覆盖 记录，无法判断每家公司的岗位是否收全（不写覆盖记录默认视为没查全）。"))
    elif not isinstance(coverage, dict):
        errors.append(Issue("error", "meta.公司覆盖", "meta.公司覆盖 必须是 object。"))
    else:
        for name in sorted(counted):
            if name not in coverage:
                warnings.append(Issue("warning", f"meta.公司覆盖.{name}",
                    f"{name}：有 {counted[name]} 条岗位记录，但没有覆盖记录（命中/收录/筛掉）。"))
        for name, info in coverage.items():
            where = f"meta.公司覆盖.{name}"
            if not isinstance(info, dict):
                errors.append(Issue("error", where, f"{name}：覆盖记录必须是 object。"))
                continue
            hit, took, dropped = info.get("命中"), info.get("收录"), info.get("筛掉") or 0
            for key, value in (("命中", hit), ("收录", took), ("筛掉", dropped)):
                if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                    errors.append(Issue("error", f"{where}.{key}", f"{name}：{key} 必须是非负整数。"))
            if isinstance(hit, int) and isinstance(took, int) and isinstance(dropped, int):
                gap = hit - dropped - took
                if gap > 0:
                    warnings.append(Issue("warning", where,
                        f"{name}：命中 {hit} 个、筛掉 {dropped} 个，只收录了 {took} 个，还差 {gap} 个没入表。"))
            actual = counted.get(name)
            if isinstance(took, int) and actual is not None and took != actual:
                warnings.append(Issue("warning", where,
                    f"{name}：覆盖记录写收录 {took} 个，实际表里有 {actual} 条，对不上。"))
            if not (info.get("检索词") or []):
                warnings.append(Issue("warning", f"{where}.检索词",
                    f"{name}：没记检索词，无法复核是否只用一个词搜了一遍。"))

    today = date.today()
    seen_ids: Dict[str, int] = {}
    for i, job in enumerate(jobs):
        where = f"jobs[{i}]"
        if not isinstance(job, dict):
            errors.append(Issue("error", where, "每个岗位必须是 object。"))
            continue
        label = job.get("公司") or where
        for key in REQUIRED:
            if not str(job.get(key) or "").strip():
                errors.append(Issue("error", f"{where}.{key}", f"{label}：缺少必填字段「{key}」。"))

        jid = str(job.get("id") or "").strip()
        if not jid:
            warnings.append(Issue("warning", f"{where}.id", f"{label}：没有 id，已按 公司+岗位+城市 自动生成。"))
        elif jid in seen_ids:
            errors.append(Issue("error", f"{where}.id", f"id「{jid}」与 jobs[{seen_ids[jid]}] 重复。"))
        else:
            seen_ids[jid] = i

        hc = str(job.get("hc状态") or "").strip()
        if hc and hc not in HC_STATES:
            errors.append(Issue("error", f"{where}.hc状态", f"{label}：hc状态「{hc}」不在 {HC_STATES} 中。"))
        elif not hc:
            warnings.append(Issue("warning", f"{where}.hc状态", f"{label}：没写 hc状态，按「未知」处理。"))

        st = str(job.get("投递状态") or "").strip()
        if st and st not in APPLY_STATES:
            errors.append(Issue("error", f"{where}.投递状态", f"{label}：投递状态「{st}」不在 {APPLY_STATES} 中。"))

        conf = str(job.get("薪资可信度") or "").strip()
        if conf and conf not in COMP_CONF:
            errors.append(Issue("error", f"{where}.薪资可信度", f"{label}：薪资可信度「{conf}」不在 {COMP_CONF} 中。"))
        src = str(job.get("薪资来源") or "").strip()
        if src and src not in COMP_SOURCE:
            errors.append(Issue("error", f"{where}.薪资来源", f"{label}：薪资来源「{src}」不在 {COMP_SOURCE} 中。"))

        salary = str(job.get("薪资") or "").strip()
        if salary and salary != "未知":
            if not src or src == "未知":
                warnings.append(Issue("warning", f"{where}.薪资来源", f"{label}：写了薪资却没写来源，看板会标为未核实。"))
            if src == "官方JD" and conf and conf != "高":
                warnings.append(Issue("warning", f"{where}.薪资可信度", f"{label}：来源是官方 JD，可信度却不是「高」，确认一下。"))
            if src in {"网传", "推断"} and conf == "高":
                errors.append(Issue("error", f"{where}.薪资可信度", f"{label}：网传/推断的薪资不能标「高」可信度。"))
            if parse_salary(salary) is None:
                warnings.append(Issue("warning", f"{where}.薪资", f"{label}：薪资「{salary}」无法解析成年包，排序时会排在最后。"))

        fit = job.get("匹配度")
        if fit is not None and fit != "":
            if isinstance(fit, bool) or not isinstance(fit, (int, float)):
                errors.append(Issue("error", f"{where}.匹配度", f"{label}：匹配度必须是 0-10 的数字。"))
            elif not 0 <= float(fit) <= 10:
                errors.append(Issue("error", f"{where}.匹配度", f"{label}：匹配度 {fit} 超出 0-10。"))

        link = str(job.get("jd链接") or "").strip()
        if link and not link.startswith(("http://", "https://")):
            errors.append(Issue("error", f"{where}.jd链接", f"{label}：jd链接必须是 http(s) 开头。"))
        if not link and hc == "在招":
            warnings.append(Issue("warning", f"{where}.jd链接", f"{label}：标了在招但没有 JD 链接。"))

        for key in ("截止时间", "jd更新时间"):
            value = job.get(key)
            if value and parse_date(value) is None:
                warnings.append(Issue("warning", f"{where}.{key}", f"{label}：{key}「{value}」不是可识别日期（建议 YYYY-MM-DD）。"))
        deadline = parse_date(job.get("截止时间"))
        if deadline and deadline < today and st in ("", "未投"):
            warnings.append(Issue("warning", f"{where}.截止时间", f"{label}：截止时间已过（{deadline}）但还是未投状态。"))

        sources = job.get("来源") or []
        if not isinstance(sources, list):
            errors.append(Issue("error", f"{where}.来源", f"{label}：来源必须是数组。"))
            sources = []
        newest: Optional[date] = None
        for j, s in enumerate(sources):
            sw = f"{where}.来源[{j}]"
            if not isinstance(s, dict):
                errors.append(Issue("error", sw, f"{label}：来源条目必须是 object。"))
                continue
            url = str(s.get("链接") or "").strip()
            if url and not url.startswith(("http://", "https://")):
                warnings.append(Issue("warning", sw, f"{label}：来源链接「{url}」不是 http(s) 开头。"))
            stype = str(s.get("类型") or "").strip()
            if stype and stype not in SOURCE_TYPES:
                warnings.append(Issue("warning", sw, f"{label}：来源类型「{stype}」不在 {SOURCE_TYPES} 中。"))
            visited = parse_date(s.get("访问日期"))
            if visited is None:
                warnings.append(Issue("warning", sw, f"{label}：来源缺少可识别的访问日期。"))
            elif newest is None or visited > newest:
                newest = visited
        if not sources:
            warnings.append(Issue("warning", f"{where}.来源", f"{label}：没有任何来源，这条信息无法核实。"))
        elif newest and (today - newest).days > STALE_DAYS:
            warnings.append(Issue("warning", f"{where}.来源", f"{label}：最新来源已是 {(today - newest).days} 天前（{newest}），建议复查。"))

        if not (job.get("存疑") or []) and not sources:
            pass  # 已在上面提示

    return errors, warnings


# --------------------------------------------------------------------------- 规范化


def normalize(data: Dict[str, Any]) -> Dict[str, Any]:
    meta = dict(data.get("meta") or {})
    jobs_out: List[Dict[str, Any]] = []
    for job in data.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        record = dict(job)
        record["id"] = str(job.get("id") or "").strip() or stable_id(
            job.get("公司"), job.get("岗位"), job.get("城市")
        )
        record["方向标签"] = as_list(job.get("方向标签"))
        record["核心要求"] = as_list(job.get("核心要求"))
        record["加分项"] = as_list(job.get("加分项"))
        record["存疑"] = as_list(job.get("存疑"))
        record["hc状态"] = str(job.get("hc状态") or "未知").strip()
        record["投递状态"] = str(job.get("投递状态") or "未投").strip()
        record["薪资可信度"] = str(job.get("薪资可信度") or "未知").strip()
        record["薪资来源"] = str(job.get("薪资来源") or "未知").strip()
        record["年包万"] = parse_salary(job.get("薪资"))
        fit = job.get("匹配度")
        record["匹配度"] = float(fit) if isinstance(fit, (int, float)) and not isinstance(fit, bool) else None
        sources = []
        for s in job.get("来源") or []:
            if isinstance(s, dict):
                sources.append({
                    "标题": str(s.get("标题") or s.get("链接") or "来源"),
                    "链接": str(s.get("链接") or ""),
                    "类型": str(s.get("类型") or "其他"),
                    "访问日期": str(s.get("访问日期") or ""),
                })
        record["来源"] = sources
        deadline = parse_date(job.get("截止时间"))
        record["截止天数"] = (deadline - date.today()).days if deadline else None
        jobs_out.append(record)
    return {"meta": meta, "jobs": jobs_out}


# --------------------------------------------------------------------------- Excel


def write_xlsx(bundle: Dict[str, Any], issues: List[Issue], path: Path) -> None:
    try:
        import xlsxwriter
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(f"需要 XlsxWriter 才能导出 Excel：{exc}（pip install XlsxWriter）")

    book = xlsxwriter.Workbook(str(path), {"constant_memory": False})
    head = book.add_format({"bold": True, "bg_color": "#1e3a8a", "font_color": "#ffffff", "border": 1})
    wrap = book.add_format({"text_wrap": True, "valign": "top"})
    plain = book.add_format({"valign": "top"})

    def sheet(name: str, columns: List[str], rows: List[List[Any]], widths: List[int]) -> None:
        ws = book.add_worksheet(name)
        ws.freeze_panes(1, 0)
        for c, title in enumerate(columns):
            ws.write(0, c, title, head)
            ws.set_column(c, c, widths[c] if c < len(widths) else 16, wrap if (widths[c] if c < len(widths) else 16) > 24 else plain)
        for r, row in enumerate(rows, start=1):
            for c, value in enumerate(row):
                ws.write(r, c, sheet_safe(value if value is not None else ""))
        if rows:
            ws.autofilter(0, 0, len(rows), max(0, len(columns) - 1))

    meta = bundle["meta"]
    jobs = bundle["jobs"]

    def _meta_cell(value: Any) -> Any:
        if isinstance(value, list):
            return "、".join(str(x) for x in value)
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        return value

    sheet("说明", ["项", "值"],
          [[k, _meta_cell(v)] for k, v in meta.items() if k != "公司覆盖"] +
          [["岗位数", len(jobs)], ["生成时间", datetime.now().strftime("%Y-%m-%d %H:%M")],
           ["提醒", "薪资/HC 以来源标注为准，网传数据未经官方确认；请以官方 JD 复核后再投递。"]],
          [18, 60])

    companies: Dict[str, Dict[str, Any]] = {}
    for j in jobs:
        name = str(j.get("公司") or "未知")
        row = companies.setdefault(name, {
            "岗位数": 0, "在招": 0, "方向": set(), "城市": set(),
            "最早截止": None, "最高匹配": None, "已投": 0,
        })
        row["岗位数"] += 1
        if j.get("hc状态") == "在招":
            row["在招"] += 1
        if j.get("投递状态") not in (None, "", "未投", "暂缓"):
            row["已投"] += 1
        row["方向"].update(j.get("方向标签") or [])
        if j.get("城市"):
            row["城市"].add(str(j.get("城市")))
        deadline = parse_date(j.get("截止时间"))
        if deadline and (row["最早截止"] is None or deadline < row["最早截止"]):
            row["最早截止"] = deadline
        fit = j.get("匹配度")
        if isinstance(fit, (int, float)) and (row["最高匹配"] is None or fit > row["最高匹配"]):
            row["最高匹配"] = fit
    cov = (meta or {}).get("公司覆盖") or {}
    def _cov(name: str, key: str) -> Any:
        info = cov.get(name)
        return info.get(key) if isinstance(info, dict) else ""
    def _gap(name: str) -> Any:
        info = cov.get(name)
        if not isinstance(info, dict):
            return ""
        hit, took, dropped = info.get("命中"), info.get("收录"), info.get("筛掉") or 0
        if isinstance(hit, int) and isinstance(took, int) and isinstance(dropped, int):
            return hit - dropped - took
        return ""
    sheet(
        "公司汇总",
        ["公司", "岗位数", "在招", "已投", "最高匹配度", "最早截止",
         "命中", "收录", "筛掉", "未入表", "检索词", "方向", "城市"],
        [[name, v["岗位数"], v["在招"], v["已投"], v["最高匹配"],
          v["最早截止"].isoformat() if v["最早截止"] else "",
          _cov(name, "命中"), _cov(name, "收录"), _cov(name, "筛掉"), _gap(name),
          "、".join(_cov(name, "检索词") or []) if isinstance(_cov(name, "检索词"), list) else "",
          "、".join(sorted(v["方向"])), "、".join(sorted(v["城市"]))]
         for name, v in sorted(companies.items(), key=lambda kv: (-(kv[1]["最高匹配"] or 0), kv[0]))],
        [24, 8, 8, 8, 12, 12, 8, 8, 8, 10, 26, 30, 16],
    )

    job_cols = ["id", "公司", "岗位", "团队", "方向标签", "城市", "hc状态", "薪资", "年包万",
                "薪资来源", "薪资可信度", "学历要求", "经验要求", "匹配度", "匹配理由",
                "核心要求", "加分项", "面试流程", "公司阶段", "投递渠道", "截止时间",
                "投递状态", "下一步", "jd链接", "存疑"]
    sheet("岗位总表", job_cols,
          [[j.get("id"), j.get("公司"), j.get("岗位"), j.get("团队"), "、".join(j.get("方向标签") or []),
            j.get("城市"), j.get("hc状态"), j.get("薪资"), j.get("年包万"), j.get("薪资来源"),
            j.get("薪资可信度"), j.get("学历要求"), j.get("经验要求"), j.get("匹配度"), j.get("匹配理由"),
            "；".join(j.get("核心要求") or []), "；".join(j.get("加分项") or []), j.get("面试流程"),
            j.get("公司阶段"), j.get("投递渠道"), j.get("截止时间"), j.get("投递状态"),
            j.get("下一步"), j.get("jd链接"), "；".join(j.get("存疑") or [])] for j in jobs],
          [16, 18, 28, 16, 20, 10, 10, 16, 10, 12, 12, 14, 12, 8, 40, 46, 34, 34, 16, 14, 12, 10, 30, 40, 44])

    sheet("投递追踪", ["公司", "岗位", "城市", "投递状态", "截止时间", "剩余天数", "下一步", "备注", "jd链接"],
          [[j.get("公司"), j.get("岗位"), j.get("城市"), j.get("投递状态"), j.get("截止时间"),
            j.get("截止天数"), j.get("下一步"), j.get("备注"), j.get("jd链接")] for j in jobs],
          [18, 28, 10, 10, 12, 10, 34, 30, 40])

    src_rows = [[j.get("公司"), j.get("岗位"), s.get("标题"), s.get("类型"), s.get("访问日期"), s.get("链接")]
                for j in jobs for s in j.get("来源") or []]
    sheet("来源", ["公司", "岗位", "标题", "类型", "访问日期", "链接"], src_rows, [18, 28, 34, 10, 12, 50])

    sheet("数据质量", ["级别", "位置", "说明"],
          [[i["level"], i["where"], i["message"]] for i in issues] or [["ok", "-", "没有发现问题。"]],
          [8, 24, 70])

    book.close()


# --------------------------------------------------------------------------- HTML

HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--brand:#1f5eff;--ink:#0f172a;--muted:#64748b;--line:#e6ebf3;--bg:#f5f7fb;--surface:#fff;
 --ok:#059669;--warn:#d97706;--danger:#dc2626;--radius:14px}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1280px;margin:0 auto;padding:24px 20px 60px}
.hero{border-radius:18px;padding:26px 30px;color:#fff;background:linear-gradient(135deg,#12306e,#1f5eff 60%,#0ea5e9)}
.hero h1{margin:0;font-size:24px;font-weight:800;letter-spacing:-.3px}
.hero p{margin:6px 0 0;opacity:.9;font-size:13px}
.pills{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
.pill{background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.25);border-radius:999px;padding:5px 12px;font-size:12px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px}
.kpi b{display:block;font-size:22px;font-weight:800}
.kpi span{font-size:12px;color:var(--muted)}
.bar{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:12px 14px;display:flex;flex-wrap:wrap;gap:10px;align-items:center;position:sticky;top:0;z-index:9}
.bar label{font-size:12px;color:var(--muted);margin-right:4px}
select,input[type=search]{border:1px solid var(--line);border-radius:8px;padding:6px 10px;font:inherit;background:#fff}
input[type=search]{min-width:220px;flex:1}
button{border:1px solid var(--line);background:#fff;border-radius:8px;padding:6px 12px;font:inherit;cursor:pointer}
button:hover{background:#eef3ff;border-color:#c7d7ff}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px;margin-top:14px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:16px 18px;position:relative;overflow:hidden}
.card:before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--brand)}
.card.s-Offer:before{background:var(--ok)} .card.s-挂:before{background:#94a3b8} .card.s-暂缓:before{background:var(--warn)}
.card h3{margin:0;font-size:16px;font-weight:800}
.card .role{color:var(--muted);font-size:13px;margin:2px 0 10px}
.tags{display:flex;flex-wrap:wrap;gap:5px;margin:8px 0}
.tag{font-size:11px;font-weight:700;padding:2px 9px;border-radius:999px;background:#eef3ff;color:#1e40af}
.tag.gray{background:#f1f5f9;color:#475569}
.tag.warn{background:#fef3c7;color:#92400e}
.tag.ok{background:#d1fae5;color:#065f46}
.tag.bad{background:#fee2e2;color:#991b1b}
.meta{font-size:12.5px;color:#334155;line-height:1.75}
.meta b{color:var(--muted);font-weight:600}
.fit{position:absolute;right:14px;top:14px;font-weight:800;font-size:18px}
.card details{margin-top:8px;font-size:12.5px;color:#334155}
.card details summary{cursor:pointer;color:var(--brand);font-weight:600;font-size:12px}
.card ul{margin:6px 0;padding-left:18px}
.foot-row{display:flex;gap:8px;align-items:center;margin-top:12px;border-top:1px dashed var(--line);padding-top:10px;flex-wrap:wrap}
.foot-row a{color:var(--brand);text-decoration:none;font-size:12px;font-weight:600}
.q{margin-top:22px;background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:16px 18px}
.q h2{margin:0 0 8px;font-size:16px}
.q li{font-size:12.5px;color:#334155}
.lv-error{color:var(--danger);font-weight:700}
.lv-warning{color:var(--warn);font-weight:700}
.note{margin-top:20px;background:#0f172a;color:#cbd5e1;border-radius:var(--radius);padding:16px 18px;font-size:12.5px;line-height:1.8}
.note b{color:#fff}
.empty{padding:40px;text-align:center;color:var(--muted)}
@media(max-width:640px){.grid{grid-template-columns:1fr}.hero{padding:20px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <h1>__TITLE__</h1>
    <p>__SUBTITLE__</p>
    <div class="pills" id="metaPills"></div>
  </div>
  <div class="kpis" id="kpis"></div>
  <div class="bar">
    <label>公司</label><select id="fCo"></select>
    <label>方向</label><select id="fDir"></select>
    <label>城市</label><select id="fCity"></select>
    <label>HC</label><select id="fHc"></select>
    <label>状态</label><select id="fSt"></select>
    <label>排序</label><select id="fSort">
      <option value="fit">匹配度</option><option value="pay">年包</option>
      <option value="deadline">截止时间</option><option value="company">公司名</option>
    </select>
    <input type="search" id="fQ" placeholder="搜索公司 / 岗位 / 要求 / 关键词">
    <button id="btnExport">导出投递状态</button>
    <button id="btnReset">重置筛选</button>
  </div>
  <div class="grid" id="grid"></div>
  <div class="empty" id="empty" style="display:none">没有符合条件的岗位。</div>
  <div class="q" id="coverage"></div>
  <div class="q" id="quality"></div>
  <div class="note">
    <b>怎么用</b>：投递状态可以直接在卡片上改，保存在本浏览器；点「导出投递状态」拿到一段 JSON，
    交给 Claude 或用 <code>build_report.py jobs.json --merge-status status.json</code> 合并回 jobs.json。<br>
    <b>可信度</b>：薪资、HC、截止时间以卡片上的来源标注为准；标「网传」的数据来自社区帖，未经官方确认。
    投递前请以官方 JD 复核。数据生成于 __GENTIME__。
  </div>
</div>
<script>
const DATA = __DATA__;
const ISSUES = __ISSUES__;
const COVERAGE = __COVERAGE__;
const KEY = "jobseek_status_" + (DATA.meta && DATA.meta.更新时间 ? DATA.meta.更新时间 : "v1");
const STATES = __STATES__;
const local = JSON.parse(localStorage.getItem(KEY) || "{}");
const jobs = DATA.jobs.map(j => Object.assign({}, j, {投递状态: local[j.id] || j.投递状态 || "未投"}));

function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function uniq(arr){return [...new Set(arr.filter(Boolean))].sort();}
function fill(sel, values, all){sel.innerHTML = [`<option value="">${all}</option>`].concat(values.map(v=>`<option>${esc(v)}</option>`)).join("");}

fill(document.getElementById("fCo"), uniq(jobs.map(j=>j.公司)), "全部公司");
fill(document.getElementById("fDir"), uniq(jobs.flatMap(j=>j.方向标签||[])), "全部方向");
fill(document.getElementById("fCity"), uniq(jobs.map(j=>j.城市)), "全部城市");
fill(document.getElementById("fHc"), uniq(jobs.map(j=>j.hc状态)), "全部 HC");
fill(document.getElementById("fSt"), STATES, "全部状态");

const pills = [];
const m = DATA.meta || {};
if (m.身份) pills.push("身份：" + m.身份);
if (m.方向) pills.push("方向：" + [].concat(m.方向).join(" / "));
if (m.城市) pills.push("城市：" + [].concat(m.城市).join(" / "));
if (m.薪资底线) pills.push("底线：" + m.薪资底线 + (m.口径 ? "（" + m.口径 + "）" : ""));
if (m.更新时间) pills.push("更新：" + m.更新时间);
document.getElementById("metaPills").innerHTML = pills.map(p=>`<div class="pill">${esc(p)}</div>`).join("");

function fitColor(v){return v==null?"#94a3b8":v>=8.5?"#059669":v>=7?"#0ea5e9":v>=5?"#d97706":"#94a3b8";}
function known(v){const s=String(v==null?"":v).trim();return s && s!=="未知" && s!=="面议" ? s : "";}
function payText(j){const p=known(j.薪资);return p ? esc(p) + (j.年包万?` <span class="tag gray">≈${j.年包万}万/年</span>`:"") : '<span class="tag gray">薪资未知</span>';}
function confTag(j){
  if(!known(j.薪资)) return "";
  const c=j.薪资可信度||"未知", cls=c==="高"?"ok":(c==="中"?"":"warn");
  return `<span class="tag ${cls}">${esc(j.薪资来源||"未知")}·可信度${esc(c)}</span>`;
}
function hcTag(j){const h=j.hc状态||"未知";const cls=h==="在招"?"ok":(h==="已关闭"?"bad":(h==="暂停"?"warn":"gray"));return `<span class="tag ${cls}">HC ${esc(h)}</span>`;}
function ddTag(j){
  if(j.截止天数==null) return j.截止时间?`<span class="tag gray">截止 ${esc(j.截止时间)}</span>`:"";
  const d=j.截止天数;
  const cls = d<0?"bad":(d<=7?"warn":"gray");
  return `<span class="tag ${cls}">${d<0?"已截止":"剩 "+d+" 天"} · ${esc(j.截止时间)}</span>`;
}

function card(j){
  const src = (j.来源||[]).map(s=>s.链接?`<a href="${esc(s.链接)}" target="_blank" rel="noopener">${esc(s.标题)}<\/a>`:esc(s.标题)).join(" · ");
  const doubt = (j.存疑||[]).length?`<details><summary>存疑 ${j.存疑.length} 条</summary><ul>${j.存疑.map(d=>`<li>${esc(d)}<\/li>`).join("")}<\/ul><\/details>`:"";
  const req = (j.核心要求||[]).length?`<details><summary>岗位要求 / 面试流程</summary><ul>${(j.核心要求||[]).map(r=>`<li>${esc(r)}<\/li>`).join("")}<\/ul>${(j.加分项||[]).length?`<div><b>加分：</b>${esc((j.加分项||[]).join("；"))}<\/div>`:""}${j.面试流程?`<div><b>流程：</b>${esc(j.面试流程)}<\/div>`:""}<\/details>`:"";
  return `<div class="card s-${esc(j.投递状态)}" data-id="${esc(j.id)}">
    <div class="fit" style="color:${fitColor(j.匹配度)}">${j.匹配度==null?"—":j.匹配度}</div>
    <h3>${esc(j.公司)}</h3>
    <div class="role">${esc(j.岗位)}${j.团队?" · "+esc(j.团队):""}</div>
    <div class="tags">${(j.方向标签||[]).map(t=>`<span class="tag">${esc(t)}<\/span>`).join("")}
      <span class="tag gray">${esc(j.城市)}</span>${hcTag(j)}${ddTag(j)}</div>
    <div class="meta">
      <div><b>薪资</b> ${payText(j)} ${confTag(j)}</div>
      ${j.学历要求||j.经验要求?`<div><b>门槛</b> ${esc([j.学历要求,j.经验要求].filter(Boolean).join(" · "))}<\/div>`:""}
      ${j.公司阶段?`<div><b>阶段</b> ${esc(j.公司阶段)}<\/div>`:""}
      ${j.匹配理由?`<div><b>匹配</b> ${esc(j.匹配理由)}<\/div>`:""}
      ${j.下一步?`<div><b>下一步</b> ${esc(j.下一步)}<\/div>`:""}
    </div>
    ${req}${doubt}
    <div class="foot-row">
      <select class="stsel">${STATES.map(s=>`<option ${s===j.投递状态?"selected":""}>${s}<\/option>`).join("")}<\/select>
      ${j.jd链接?`<a href="${esc(j.jd链接)}" target="_blank" rel="noopener">JD ↗<\/a>`:""}
      ${src?`<span style="font-size:11.5px;color:#94a3b8">来源：${src}<\/span>`:'<span class="tag warn">无来源<\/span>'}
    <\/div>
  <\/div>`;
}

function apply(){
  const co=fCo.value, dir=fDir.value, city=fCity.value, hc=fHc.value, st=fSt.value, q=fQ.value.trim().toLowerCase();
  let list = jobs.filter(j =>
    (!co || j.公司===co) &&
    (!dir || (j.方向标签||[]).includes(dir)) &&
    (!city || j.城市===city) && (!hc || j.hc状态===hc) && (!st || j.投递状态===st) &&
    (!q || JSON.stringify(j).toLowerCase().includes(q)));
  const sort=fSort.value;
  list.sort((a,b)=>{
    if(sort==="fit") return (b.匹配度??-1)-(a.匹配度??-1);
    if(sort==="pay") return (b.年包万??-1)-(a.年包万??-1);
    if(sort==="deadline") return (a.截止天数??9999)-(b.截止天数??9999);
    return String(a.公司).localeCompare(String(b.公司),"zh");
  });
  document.getElementById("grid").innerHTML = list.map(card).join("");
  document.getElementById("empty").style.display = list.length?"none":"block";
  document.querySelectorAll(".stsel").forEach(sel=>sel.addEventListener("change",e=>{
    const id=e.target.closest(".card").dataset.id;
    local[id]=e.target.value;
    localStorage.setItem(KEY, JSON.stringify(local));
    const job=jobs.find(x=>x.id===id); if(job) job.投递状态=e.target.value;
    renderKpis(); apply();
  }));
}

function renderKpis(){
  const n=jobs.length, open=jobs.filter(j=>j.hc状态==="在招").length;
  const applied=jobs.filter(j=>j.投递状态!=="未投"&&j.投递状态!=="暂缓").length;
  const inflow=jobs.filter(j=>["笔试","一面","二面","三面","HR面","Offer"].includes(j.投递状态)).length;
  const pays=jobs.map(j=>j.年包万).filter(v=>typeof v==="number").sort((a,b)=>a-b);
  const med=pays.length?(pays.length%2?pays[(pays.length-1)/2]:((pays[pays.length/2-1]+pays[pays.length/2])/2)).toFixed(1):"—";
  const soon=jobs.filter(j=>j.截止天数!=null&&j.截止天数>=0&&j.截止天数<=7).length;
  const nco=new Set(jobs.map(j=>j.公司)).size;
  const k=[[nco,"公司"],[n,"岗位"],[open,"在招"],[applied,"已投"],[inflow,"进流程"],[med+"万",`年包中位数（n=${pays.length}）`],[soon,"7天内截止"]];
  document.getElementById("kpis").innerHTML=k.map(([a,b])=>`<div class="kpi"><b>${a}</b><span>${b}</span></div>`).join("");
}

(function renderCoverage(){
  const counted={}; jobs.forEach(j=>{counted[j.公司]=(counted[j.公司]||0)+1;});
  const names=[...new Set(Object.keys(counted).concat(Object.keys(COVERAGE||{})))].sort();
  if(!names.length){document.getElementById("coverage").style.display="none";return;}
  const rows=names.map(n=>{
    const c=(COVERAGE||{})[n]||{}; const hit=c.命中, took=c.收录, drop=c.筛掉||0;
    const gap=(typeof hit==="number"&&typeof took==="number")?hit-drop-took:null;
    const tag = gap===null ? '<span class="tag warn">无覆盖记录</span>'
              : gap>0 ? `<span class="tag bad">还差 ${gap} 个没入表</span>`
              : '<span class="tag ok">已收全</span>';
    return `<tr><td>${esc(n)}</td><td style="text-align:right">${counted[n]||0}</td>
      <td style="text-align:right">${hit==null?"—":hit}</td>
      <td style="text-align:right">${drop||0}</td><td>${tag}</td>
      <td style="color:#64748b;font-size:12px">${esc((c.检索词||[]).join("、"))}${c.说明?" · "+esc(c.说明):""}</td></tr>`;
  }).join("");
  const bad=names.filter(n=>{const c=(COVERAGE||{})[n]; if(!c)return true;
    const g=(typeof c.命中==="number"&&typeof c.收录==="number")?c.命中-(c.筛掉||0)-c.收录:null; return g===null||g>0;}).length;
  document.getElementById("coverage").innerHTML=
    `<h2>公司覆盖度（${names.length} 家，${bad} 家有缺口或没记录）</h2>
     <p style="font-size:12.5px;color:#64748b;margin:4px 0 10px">「命中」是该公司符合你方向的在招岗位总数，「表内」是实际入表条数。两者不一致说明岗位没收全，看板会低估这家公司的机会。</p>
     <div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:12.5px">
     <thead><tr style="text-align:left;color:#64748b">
       <th style="padding:4px 8px 4px 0">公司</th><th style="text-align:right">表内</th><th style="text-align:right">命中</th>
       <th style="text-align:right">筛掉</th><th>状态</th><th>检索词 / 说明</th></tr></thead><tbody>${rows}</tbody></table></div>`;
})();

document.getElementById("quality").innerHTML =
  `<h2>数据质量（${ISSUES.length} 条）</h2>` + (ISSUES.length
    ? `<ul>${ISSUES.map(i=>`<li><span class="lv-${i.level}">${i.level==="error"?"错误":"提示"}<\/span> · ${esc(i.where)} — ${esc(i.message)}<\/li>`).join("")}<\/ul>`
    : "<p>校验没有发现问题。注意：校验只检查结构与来源标注，不能证明页面内容为真。<\/p>");

document.getElementById("btnExport").addEventListener("click",()=>{
  const out=JSON.stringify(jobs.reduce((acc,j)=>{acc[j.id]=j.投递状态;return acc;},{}),null,2);
  navigator.clipboard && navigator.clipboard.writeText(out);
  const w=window.open("","_blank","width=520,height=560");
  if(w) w.document.write("<pre>"+out.replace(/</g,"&lt;")+"</pre>");
  else alert(out);
});
document.getElementById("btnReset").addEventListener("click",()=>{
  ["fCo","fDir","fCity","fHc","fSt"].forEach(id=>document.getElementById(id).value="");
  fQ.value=""; fSort.value="fit"; apply();
});
["fCo","fDir","fCity","fHc","fSt","fSort"].forEach(id=>document.getElementById(id).addEventListener("change",apply));
document.getElementById("fQ").addEventListener("input",apply);
renderKpis(); apply();
</script>
</body>
</html>
"""


def write_html(bundle: Dict[str, Any], issues: List[Issue], path: Path) -> None:
    meta = bundle.get("meta") or {}
    direction = meta.get("方向")
    direction_text = "、".join(direction) if isinstance(direction, list) else str(direction or "机器人算法")
    title = str(meta.get("标题") or f"{direction_text} · 求职看板")
    subtitle = "机器人 / 具身智能岗位调研结果；薪资与 HC 以来源标注为准，投递前请以官方 JD 复核。"
    page = (HTML_TEMPLATE
            .replace("__TITLE__", esc(title))
            .replace("__SUBTITLE__", esc(subtitle))
            .replace("__GENTIME__", datetime.now().strftime("%Y-%m-%d %H:%M"))
            .replace("__STATES__", json.dumps(APPLY_STATES, ensure_ascii=False))
            .replace("__ISSUES__", json.dumps(issues, ensure_ascii=False))
            .replace("__COVERAGE__", json.dumps((meta or {}).get("公司覆盖") or {}, ensure_ascii=False))
            .replace("__DATA__", json.dumps(bundle, ensure_ascii=False)))
    path.write_text(page, encoding="utf-8")


# --------------------------------------------------------------------------- CLI


def merge_status(source: Path, status_file: Path) -> int:
    data = json.loads(source.read_text(encoding="utf-8"))
    status = json.loads(status_file.read_text(encoding="utf-8"))
    if not isinstance(status, dict):
        print("error: status 文件必须是 {id: 状态} 的 object", file=sys.stderr)
        return 2
    changed = 0
    for job in data.get("jobs") or []:
        jid = str(job.get("id") or "").strip() or stable_id(job.get("公司"), job.get("岗位"), job.get("城市"))
        new = status.get(jid)
        if new and new in APPLY_STATES and new != job.get("投递状态"):
            job["投递状态"] = new
            changed += 1
    source.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已合并 {changed} 条投递状态到 {source}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path, help="jobs.json")
    ap.add_argument("--html", type=Path, help="输出单文件 HTML 看板")
    ap.add_argument("--xlsx", type=Path, help="输出 Excel")
    ap.add_argument("--check-only", action="store_true", help="只校验，不生成产物")
    ap.add_argument("--merge-status", type=Path, help="把看板导出的状态 JSON 合并回 jobs.json")
    ap.add_argument("--strict", action="store_true", help="把警告也当作失败")
    args = ap.parse_args(argv)

    if args.merge_status:
        return merge_status(args.input, args.merge_status)

    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: 读不了 {args.input}：{exc}", file=sys.stderr)
        return 2

    errors, warnings = validate(data)
    for issue in errors + warnings:
        tag = "错误" if issue["level"] == "error" else "提示"
        print(f"[{tag}] {issue['where']}: {issue['message']}")
    print(f"—— 校验完成：{len(errors)} 个错误，{len(warnings)} 个提示 ——")

    if errors:
        print("有错误，未生成产物。修好后重跑。", file=sys.stderr)
        return 1
    if args.strict and warnings:
        print("--strict 下警告即失败，未生成产物。", file=sys.stderr)
        return 1
    if args.check_only:
        return 0

    bundle = normalize(data)
    issues = errors + warnings
    if args.html:
        args.html.parent.mkdir(parents=True, exist_ok=True)
        write_html(bundle, issues, args.html)
        print(f"看板已生成：{args.html}")
    if args.xlsx:
        args.xlsx.parent.mkdir(parents=True, exist_ok=True)
        write_xlsx(bundle, issues, args.xlsx)
        print(f"Excel 已生成：{args.xlsx}")
    if not args.html and not args.xlsx:
        print("（没有指定 --html / --xlsx，只做了校验。）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
