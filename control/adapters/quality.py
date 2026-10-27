#!/usr/bin/env python3
"""agent-quality-security 门禁适配器。

做法：直接跑项目自己的 `qualityctl.py pipeline`（十道门禁 + 证据哈希 + 账本），
再读它生成的 `reports/<UTC>/gate-report.json`，如实映射成逐项状态。

为什么这么做：
  * 不复制它的门禁逻辑，避免两套口径；
  * 证据哈希、失败回流、审计都由它自己算；
  * 门禁报告是唯一事实来源。

环境注意（都在实测里踩过）：
  1. 必须用**系统 Python**（D:\\cpython3.14）。工作区 venv 的 python.exe 带沙箱能力标记，
     从它启动的进程开不了 docker 命名管道、也写不了项目目录。
  2. 子进程要注入 TMP/TEMP，否则 tempfile 报 "No usable temporary directory"。
  3. `scripts/*` 直接 import agent_quality，需要 PYTHONPATH=<root>/src。
  4. Phoenix 走 PHOENIX_HOST；Playwright 走 PLAYWRIGHT_BROWSERS_PATH。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_PYTHON = r"D:\cpython3.14\python.exe"
CACHE_TTL = 900  # 15 分钟；pipeline 一次约 4-5 分钟
PIPELINE_TIMEOUT = 1800


def _cache_path() -> Path:
    p = Path(os.environ.get("EVOLUTION_GATE_CACHE",
                            r"D:\start\ds-harness\work\_qs-store\gates-cache.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_cache() -> dict:
    p = _cache_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_cache(data: dict) -> None:
    try:
        _cache_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _env(project_root: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(project_root / "src")
    tmp = os.environ.get("EVOLUTION_TMP", r"D:\start\ds-harness\work\_tmp")
    os.makedirs(tmp, exist_ok=True)
    env["TMP"] = env["TEMP"] = tmp
    env.setdefault("PHOENIX_HOST", "http://127.0.0.1:6006")
    env.setdefault("PLAYWRIGHT_BROWSERS_PATH", r"D:\start\ds-harness\work\_pw-browsers")
    return env


def _python() -> str:
    cand = os.environ.get("EVOLUTION_PYTHON", DEFAULT_PYTHON)
    return cand if Path(cand).exists() else sys.executable


def _latest_report(root: Path) -> Path | None:
    reports = root / "reports"
    if not reports.is_dir():
        return None
    dirs = [d for d in reports.iterdir() if d.is_dir() and d.name[:8].isdigit()]
    if not dirs:
        return None
    dirs.sort(key=lambda p: p.name, reverse=True)
    for d in dirs:
        f = d / "gate-report.json"
        if f.exists():
            return f
    return None


def _run_pipeline(project_root: Path, timeout: int) -> tuple[bool, str]:
    cmd = [_python(), "qualityctl.py", "pipeline"]
    try:
        proc = subprocess.run(cmd, cwd=str(project_root), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              env=_env(project_root))
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, " ".join(out.split())[-400:]
    except subprocess.TimeoutExpired:
        return False, "pipeline timeout"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def _map_report(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    gates = []
    for c in data.get("checks", []):
        if c.get("skipped"):
            status = "unavailable"
        elif c.get("passed"):
            status = "passed"
        else:
            status = "failed"
        gates.append({
            "id": c.get("id"),
            "layer": c.get("layer"),
            "status": status,
            "required": c.get("required"),
            "seconds": round(float(c.get("duration_seconds") or 0), 2),
            "detail": (c.get("detail") or "")[:240],
            "evidence_sha256": ((c.get("evidence") or {}).get("sha256") or "")[:16],
        })
    return gates



def _light_gates(project_root: Path) -> list[dict]:
    """EVOLUTION_GATE_SET=light：只跑两道纯 Python 门禁，供快速自检用（明确是子集）。"""
    py = _python()
    env = _env(project_root)
    out = []
    for gate, cmd in (("unit-tests", [py, "-m", "unittest", "discover", "-s", "tests", "-q"]),
                      ("business-outcome", [py, "qualityctl.py", "demo"])):
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, cwd=str(project_root), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=300, env=env)
            status = "passed" if proc.returncode == 0 else "failed"
            detail = " ".join(((proc.stdout or "") + (proc.stderr or "")).split())[-200:]
        except Exception as exc:  # noqa: BLE001
            status, detail = "failed", f"{type(exc).__name__}: {exc}"
        out.append({"id": gate, "status": status, "seconds": round(time.time() - t0, 2),
                    "detail": detail, "subset": True})
    return out

def run_gates(project_root: Path, python_exe: str | None = None,
              timeout: int = PIPELINE_TIMEOUT, heavy: bool = False) -> list[dict]:
    """heavy=False 时优先读 15 分钟内的缓存报告；heavy=True 强制重跑 pipeline。"""
    cache = _load_cache()
    fresh = bool(cache.get("gates")) and (time.time() - cache.get("at", 0) < CACHE_TTL)
    if fresh and not heavy:
        return cache["gates"]

    ok, detail = _run_pipeline(project_root, timeout)
    report = _latest_report(project_root)
    if report is None:
        return [{"id": "pipeline", "status": "failed", "seconds": 0,
                 "detail": f"没找到 gate-report.json；pipeline ok={ok} {detail}"}]

    gates = _map_report(report)
    _save_cache({"at": time.time(), "ok": ok, "report": str(report), "gates": gates})
    return gates


def summarize(gates: list[dict]) -> dict:
    passed = sum(1 for g in gates if g["status"] == "passed")
    failed = sum(1 for g in gates if g["status"] == "failed")
    unavailable = sum(1 for g in gates if g["status"] == "unavailable")
    return {"total": len(gates), "passed": passed, "failed": failed,
            "unavailable": unavailable,
            "all_required_passed": failed == 0 and unavailable == 0}
