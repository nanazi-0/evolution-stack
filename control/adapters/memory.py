#!/usr/bin/env python3
"""记忆适配器：local（默认，立即可用）或 openviking（HTTP，需起服务）。

ADR-004 由调用方保证：只有 approved 的 run 才会走到这里。

local 后端：JSONL 追加写，简单、可审计、零依赖。
openviking 后端：POST {url}/api/v1/resources 写入，
                 POST {url}/api/v1/search/search 检索。
                 接口路径取自其 Python SDK，字段做了宽松处理——没有起服务时无法验证，
                 所以返回里带 backend 字段，调用方一看就知道用的是哪个。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

LOCAL_FILE = Path(os.environ.get(
    "EVOLUTION_MEMORY_FILE", r"D:\start\ds-harness\work\evolution-state\memory.jsonl"))
OV_URL = os.environ.get("OPENVIKING_URL", "").rstrip("/")
OV_KEY = os.environ.get("OPENVIKING_API_KEY", "")


def _backend() -> str:
    forced = os.environ.get("MEMORY_BACKEND", "").lower()
    if forced in ("local", "openviking"):
        return forced
    return "openviking" if OV_URL else "local"


def _post(url: str, payload: dict, timeout: int = 30) -> tuple[int, dict]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    if OV_KEY:
        req.add_header("X-API-Key", OV_KEY)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode("utf-8", "replace")[:300]}
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def remember(project: str, run_id: str, summary: str, meta: dict | None = None) -> dict:
    backend = _backend()
    record = {"project": project, "run_id": run_id, "summary": summary,
              "meta": meta or {}, "at": int(time.time())}

    if backend == "openviking":
        uri = f"viking://~/memories/evolution/{project}/{run_id}"
        status, body = _post(f"{OV_URL}/api/v1/content/write", {
            "uri": uri,
            "content": summary,
            "tags": [f"project:{project}", "source:evolution-stack"],
            "wait": True,
        })
        return {"backend": "openviking", "ok": 200 <= status < 300,
                "status": status, "detail": body if status >= 300 else "written"}
    LOCAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCAL_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"backend": "local", "ok": True, "file": str(LOCAL_FILE)}


def recall(project: str, query: str, top_k: int = 5) -> dict:
    backend = _backend()
    if backend == "openviking":
        status, body = _post(f"{OV_URL}/api/v1/search/search", {
            "query": query,
            "limit": max(top_k * 4, 8),
        })
        raw = ((body.get("result") or {}).get("memories") or []) if isinstance(body, dict) else []
        # 服务端把 ~ 解析成 viking://user/<user>/...，所以按路径里的项目段过滤
        marker = f"/evolution/{project}/"
        hits = [{"uri": h.get("uri"), "abstract": h.get("abstract"), "score": h.get("score"),
                 "level": h.get("level")}
                for h in raw if marker in str(h.get("uri", ""))]
        return {"backend": "openviking", "ok": 200 <= status < 300, "status": status,
                "hits": hits[:top_k], "scanned": len(raw),
                "detail": None if status < 300 else str(body)[:200]}
    if not LOCAL_FILE.exists():
        return {"backend": "local", "ok": True, "hits": [], "note": "记忆库为空", "project": project}
    rows = []
    for line in LOCAL_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    # 先按项目过滤，再做朴素子串匹配；够用且可解释，不做语义检索
    rows = [r for r in rows if r.get("project", "default") == project]
    q = query.lower()
    hits = [r for r in rows if q in (r.get("summary", "") + json.dumps(r.get("meta", {}), ensure_ascii=False)).lower()]
    if not hits:
        hits = rows[-top_k:]
    return {"backend": "local", "ok": True, "hits": hits[-top_k:], "total": len(rows), "project": project}
