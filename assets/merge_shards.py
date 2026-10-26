#!/usr/bin/env python3
"""把并行 agent 产出的分片研究文件合并成一份 jobs.json。

用法:
    python3 merge_shards.py <out_dir>/agent_out <out_dir>/jobs.json [--dry-run]

设计要点（对齐 phd-application-planner 的研究流水线）:
- 稳定身份：公司+职位ID字段（没有时依次退到 岗位名里的编号、JD 链接的 position id、
  公司+岗位名+城市+招聘类型 归一化），不依赖数组顺序或岗位名单独判重；
- 用户状态不可覆盖：已存在 jobs.json 里的 投递状态/下一步/备注 会保留；
- 失败不静默丢弃：分片里的 errors[] 汇总进 meta.研究错误，来源的 访问失败 原样保留；
- 覆盖度合并：每家公司的 命中/收录/筛掉 汇总进 meta.公司覆盖，并按实际条数校正 收录。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Tuple

KEEP_USER_FIELDS = ("投递状态", "下一步", "备注")
JOB_ID_RE = re.compile(r"[A-Z]\d{4,}[A-Z]?")


def norm(text: Any) -> str:
    s = unicodedata.normalize("NFKC", str(text or "")).strip().lower()
    return re.sub(r"[\s　（）()【】\[\]・·、,，/|-]+", "", s)


def identity(job: Dict[str, Any]) -> Tuple[str, str]:
    company = norm(job.get("公司"))
    jid = norm(job.get("职位ID"))
    if jid and jid not in ("未知", "无", "none", "null"):
        return company, "jid:" + jid
    title = str(job.get("岗位") or "")
    m = JOB_ID_RE.search(title)
    if m:
        return company, "id:" + m.group(0).lower()
    link = str(job.get("jd链接") or "")
    m2 = re.search(r"/position/(\d{6,})", link)
    if m2:
        return company, "url:" + m2.group(1)
    # 同名岗位的校招/实习/社招是不同的岗，招聘类型也要进键
    return company, "t:" + norm(title) + "|" + norm(job.get("城市")) + "|" + norm(job.get("招聘类型"))


CHANNEL_MARK = re.compile(
    r"[（(【\[]\s*(校招|社招|校园招聘|社会招聘|(20)?\d{2}届?(校招|校园招聘)?|应届生?|全职)\s*[）)】\]]")


def channel_of(job: Dict[str, Any]) -> str:
    """岗位渠道：实习 / 校招 / 社招 / 校招/社招（已合并）。"""
    title, t = str(job.get("岗位") or ""), str(job.get("招聘类型") or "")
    if "实习" in title and not re.search(r"实习\s*[/／]\s*校招|校招\s*[/／]\s*实习|[/／]\s*实习生", title):
        return "实习"
    if t in ("校招", "社招", "实习", "校招/社招"):
        return t
    if re.search(r"校招|校园招聘|应届|\d{2,4}届", title + str(job.get("经验要求") or "")):
        return "校招"
    return "社招"


def channel_key(job: Dict[str, Any]) -> Tuple[str, str, str]:
    title = CHANNEL_MARK.sub("", str(job.get("岗位") or ""))
    title = re.sub(r"^(20)?\d{2}届?校招[-－ ]?", "", title)
    return norm(job.get("公司")), norm(title), norm(job.get("城市"))


def combine_channels(jobs: List[Dict[str, Any]]) -> int:
    """同公司、同岗位名、同城市的校招版和社招版合成一条：招聘类型记「校招/社招」，
    社招入口放进「其他投递链接」，社招版的职位ID记进「合并职位ID」（下次刷新据此认出，不再重复新增）。
    投递状态取已投的那一版，下一步/备注合并。返回合并掉的条数。"""
    groups: Dict[Tuple[str, str, str], List[Dict]] = {}
    for job in jobs:
        groups.setdefault(channel_key(job), []).append(job)
    drop = set()
    for group in groups.values():
        camp = [j for j in group if channel_of(j) in ("校招", "校招/社招")]
        soc = [j for j in group if channel_of(j) == "社招"]
        if not camp or not soc:
            continue
        main = camp[0]
        for s in soc:
            links = main.setdefault("其他投递链接", [])
            if s.get("jd链接") and all(l.get("链接") != s["jd链接"] for l in links):
                links.append({"类型": "社招", "链接": s["jd链接"], "职位ID": s.get("职位ID") or ""})
            alias = main.setdefault("合并职位ID", [])
            for x in (s.get("职位ID"), s.get("id")):
                if x and x not in alias:
                    alias.append(x)
            have = {src.get("链接") for src in main.get("来源") or []}
            main.setdefault("来源", []).extend(src for src in (s.get("来源") or []) if src.get("链接") not in have)
            if (main.get("投递状态") in (None, "", "未投")) and s.get("投递状态") not in (None, "", "未投"):
                main["投递状态"] = s["投递状态"]
            for f in ("下一步", "备注"):
                if s.get(f) and s[f] not in (main.get(f) or ""):
                    main[f] = ((main.get(f) or "") + " ｜社招版：" + s[f]).strip(" ｜")
            if s.get("hc状态") == "在招":
                main["hc状态"] = "在招"
            note = f"同岗也挂在社招入口（{s.get('岗位')}），已合并为一条，社招链接见「其他投递链接」"
            if note not in (main.get("存疑") or []):
                main.setdefault("存疑", []).append(note)
            drop.add(id(s))
        main["招聘类型"] = "校招/社招"
    jobs[:] = [j for j in jobs if id(j) not in drop]
    return len(drop)


def load_shards(shard_dir: Path) -> Tuple[List[Dict], Dict[str, Dict], List[Dict]]:
    jobs: List[Dict] = []
    coverage: Dict[str, Dict] = {}
    errors: List[Dict] = []
    for path in sorted(shard_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # 分片坏了要报出来，不能当没有
            errors.append({"分片": path.name, "code": "parse_error", "message": str(exc)})
            continue
        shard_jobs = data.get("jobs") or []
        for job in shard_jobs:
            if isinstance(job, dict):
                job.setdefault("_分片", path.stem)
                jobs.append(job)
        for name, info in (data.get("coverage") or {}).items():
            if name in coverage:
                errors.append({"分片": path.name, "code": "duplicate_coverage",
                               "message": f"{name} 的覆盖记录在多个分片里出现，已保留先到的一份"})
                continue
            coverage[name] = info
        for err in (data.get("errors") or []):
            errors.append({"分片": path.name, **(err if isinstance(err, dict) else {"message": str(err)})})
    return jobs, coverage, errors


def merge(shard_dir: Path, target: Path, dry_run: bool = False) -> int:
    base: Dict[str, Any] = {"meta": {}, "jobs": []}
    if target.exists():
        base = json.loads(target.read_text(encoding="utf-8"))
    old_jobs = base.get("jobs") or []
    old_by_key = {identity(j): j for j in old_jobs}
    # 已合并进「校招/社招」记录的社招版，用它的职位ID也能认出来，避免刷新时又加回一条
    alias_keys = set()
    for j in old_jobs:
        for link in j.get("其他投递链接") or []:
            k = identity({"公司": j.get("公司"), "职位ID": link.get("职位ID"), "jd链接": link.get("链接"),
                          "岗位": "", "城市": j.get("城市")})
            if not k[1].startswith("t:"):
                alias_keys.add(k)
                old_by_key.setdefault(k, j)

    new_jobs, coverage, errors = load_shards(shard_dir)
    if not new_jobs and not coverage:
        print(f"error: {shard_dir} 下没有可用的分片文件", file=sys.stderr)
        return 2

    merged = list(old_jobs)
    added = updated = 0
    seen_new: Dict[Tuple[str, str], Dict] = {}
    for job in new_jobs:
        key = identity(job)
        if key in seen_new:
            errors.append({"code": "duplicate_in_shards", "message": f"分片间重复岗位：{job.get('公司')} / {job.get('岗位')}"})
            continue
        seen_new[key] = job
        old = old_by_key.get(key)
        if key in alias_keys:
            continue  # 社招版已并入「校招/社招」记录，不覆盖主记录、也不重复新增
        if old is None:
            merged.append(job)
            added += 1
        else:
            for field in KEEP_USER_FIELDS:  # 用户状态永远以本地为准
                if old.get(field):
                    job[field] = old[field]
            job["id"] = old.get("id", job.get("id"))
            merged[merged.index(old)] = job
            updated += 1

    combined = combine_channels(merged)
    if combined:
        print(f"同名校招/社招合并 {combined} 条（招聘类型记为「校招/社招」）")

    counts: Dict[str, int] = {}
    for job in merged:
        name = str(job.get("公司") or "").strip()
        counts[name] = counts.get(name, 0) + 1
    meta_cov = dict(base.get("meta", {}).get("公司覆盖") or {})
    for name, info in coverage.items():
        info = dict(info)
        actual = counts.get(name)
        if actual is not None and info.get("收录") != actual:
            info["收录"] = actual  # 以实际条数为准，避免 agent 自报数与表里对不上
        meta_cov[name] = info
    for name, actual in counts.items():
        if name not in meta_cov:
            meta_cov[name] = {"检索词": [], "命中": None, "收录": actual, "筛掉": 0,
                              "说明": "合并时无覆盖记录，需补充"}
        else:
            meta_cov[name]["收录"] = actual

    base.setdefault("meta", {})["公司覆盖"] = meta_cov
    if errors:
        base["meta"]["研究错误"] = errors
    base["jobs"] = merged

    print(f"分片 {len(list(shard_dir.glob('*.json')))} 个 → 新增 {added}、更新 {updated}、"
          f"合计 {len(merged)} 个岗位 / {len(counts)} 家公司")
    if errors:
        print(f"⚠️ {len(errors)} 条研究错误已写入 meta.研究错误：")
        for err in errors[:5]:
            print("   -", err.get("code", ""), err.get("message", ""))
    if dry_run:
        print("(dry-run，未写文件)")
        return 0
    target.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {target}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="合并并行 agent 的分片研究文件")
    ap.add_argument("shard_dir", type=Path)
    ap.add_argument("target", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.shard_dir.is_dir():
        print(f"error: 找不到分片目录 {args.shard_dir}", file=sys.stderr)
        return 2
    return merge(args.shard_dir, args.target, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
