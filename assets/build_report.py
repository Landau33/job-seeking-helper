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
        # 月数写法：×15 / *15 / ·15薪 / 15薪 / 13+2；都没有才按 12 个月
        plus = re.search(r"(\d+)\s*\+\s*(\d+)\s*薪?", s[m.end():])
        months_match = (re.search(r"[*·]\s*(\d+(?:\.\d+)?)", s[m.end():])
                        or re.search(r"(\d+(?:\.\d+)?)\s*薪", s[m.end():]))
        if plus:
            months = float(plus.group(1)) + float(plus.group(2))
        elif months_match:
            months = float(months_match.group(1))
        else:
            months = 12.0
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
            pending = info.get("待补查")
            if pending:
                chans = "、".join(pending) if isinstance(pending, list) else str(pending)
                warnings.append(Issue("warning", where,
                    f"{name}：还有渠道没查（{chans}），数字对得上也不能算收全。"))
            elif hit == 0 and took == 0 and not info.get("检索词"):
                warnings.append(Issue("warning", where,
                    f"{name}：命中 0。请确认是真的没有对口岗位，还是岗位没取到；没取到要写 待补查。"))
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
            # 招聘平台（BOSS/猎聘）上公司自己标的区间按规则记「官方JD + 中」，不报警
            via_platform = any(isinstance(x, dict) and re.search(r"zhipin|liepin", str(x.get("链接") or ""))
                               for x in (job.get("来源") or [])) or re.search(r"zhipin|liepin", str(job.get("jd链接") or ""))
            if src == "官方JD" and conf and conf != "高" and not (via_platform and conf == "中"):
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
        if deadline and deadline < today and st in ("", "未投") and hc != "已关闭":
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
         "命中", "收录", "筛掉", "未入表", "待补查", "检索词", "方向", "城市"],
        [[name, v["岗位数"], v["在招"], v["已投"], v["最高匹配"],
          v["最早截止"].isoformat() if v["最早截止"] else "",
          _cov(name, "命中"), _cov(name, "收录"), _cov(name, "筛掉"), _gap(name),
          "、".join(_cov(name, "待补查")) if isinstance(_cov(name, "待补查"), list) else (_cov(name, "待补查") or ""),
          "、".join(_cov(name, "检索词") or []) if isinstance(_cov(name, "检索词"), list) else "",
          "、".join(sorted(v["方向"])), "、".join(sorted(v["城市"]))]
         for name, v in sorted(companies.items(), key=lambda kv: (-(kv[1]["最高匹配"] or 0), kv[0]))],
        [24, 8, 8, 8, 12, 12, 8, 8, 8, 10, 24, 26, 30, 16],
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

# 看板的 HTML/CSS/JS 框架在同目录的 report_template.html，这里只负责填数据（__DATA__ 等占位符）。
# 改界面直接改那个文件，不用动这里的数据处理。
HTML_TEMPLATE = (Path(__file__).resolve().parent / "report_template.html").read_text(encoding="utf-8")


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
