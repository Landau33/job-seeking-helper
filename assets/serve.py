#!/usr/bin/env python3
"""job-seek-planner · 本机看板服务：在看板上改投递状态，自动写回 jobs.json。

用法:
  python3 serve.py <out_dir> [--port 8778]
  然后浏览器打开 http://127.0.0.1:8778/report.html

设计要点:
- 只监听 127.0.0.1，不对外；只接受同源请求；
- 只改 jobs.json 里每条岗位的 投递状态 / 下一步 / 备注 三个字段，其余字段一律不碰；
- 每次写入前把旧文件备份为 jobs.json.bak，用临时文件 + 原子替换写入，写到一半崩溃也不会弄坏数据；
- jobs.json 比 report.html 新时，打开看板前自动重建一次，刷新页面看到的就是最新数据。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build_report.py"
APPLY_STATES = {"未投", "已投", "笔试", "一面", "二面", "三面", "HR面", "Offer", "挂", "暂缓"}
EDITABLE = {"投递状态", "下一步", "备注"}
LOCK = threading.Lock()


def rebuild(out: Path) -> None:
    cmd = [sys.executable, str(BUILD), str(out / "jobs.json"), "--html", str(out / "report.html")]
    if (out / "jobs.xlsx").exists():
        cmd += ["--xlsx", str(out / "jobs.xlsx")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write("[看板服务] 重建失败：\n" + r.stdout[-2000:] + r.stderr[-2000:] + "\n")


def apply_updates(out: Path, updates: dict) -> dict:
    """updates: {id: "已投"} 或 {id: {"投递状态": "...", "下一步": "...", "备注": "..."}}"""
    path = out / "jobs.json"
    with LOCK:
        data = json.loads(path.read_text(encoding="utf-8"))
        by_id = {str(j.get("id")): j for j in data.get("jobs") or []}
        changed, unknown, rejected = 0, [], []
        for jid, val in updates.items():
            job = by_id.get(str(jid))
            if job is None:
                unknown.append(jid)
                continue
            fields = {"投递状态": val} if isinstance(val, str) else (val if isinstance(val, dict) else {})
            for k, v in fields.items():
                if k not in EDITABLE or not isinstance(v, str):
                    rejected.append(f"{jid}.{k}")
                    continue
                if k == "投递状态" and v not in APPLY_STATES:
                    rejected.append(f"{jid}.{k}={v}")
                    continue
                if job.get(k) != v:
                    job[k] = v
                    changed += 1
        if changed:
            shutil.copy2(path, path.with_suffix(".json.bak"))
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        return {"ok": True, "changed": changed, "unknown": unknown, "rejected": rejected}


def make_handler(out: Path):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(out), **kw)

        def log_message(self, fmt, *args):  # 安静一点，只打印写入
            pass

        def _json(self, code: int, obj: dict) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/api/ping"):
                return self._json(200, {"ok": True})
            if self.path.split("?")[0] in ("/", "/report.html"):
                rep, src = out / "report.html", out / "jobs.json"
                with LOCK:
                    if not rep.exists() or src.stat().st_mtime > rep.stat().st_mtime:
                        rebuild(out)
                if self.path == "/":
                    self.path = "/report.html"
            return super().do_GET()

        def do_POST(self):
            if not self.path.startswith("/api/status"):
                return self._json(404, {"ok": False, "error": "not found"})
            origin = self.headers.get("Origin") or ""
            host = self.headers.get("Host") or ""
            if origin and origin.split("//")[-1] != host:
                return self._json(403, {"ok": False, "error": "只接受同源请求"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                updates = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                if not isinstance(updates, dict):
                    raise ValueError("body 必须是 object")
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
            res = apply_updates(out, updates)
            if res["changed"]:
                print(f"[看板服务] 已写回 jobs.json：{res['changed']} 处改动", flush=True)
            return self._json(200, res)

    return Handler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path, help="求职输出目录（含 jobs.json）")
    ap.add_argument("--port", type=int, default=8778)
    a = ap.parse_args(argv)
    out = a.out.resolve()
    if not (out / "jobs.json").exists():
        print(f"error: {out}/jobs.json 不存在", file=sys.stderr)
        return 2
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(out))
    print(f"看板服务已启动：http://127.0.0.1:{a.port}/report.html （Ctrl+C 停止）", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
