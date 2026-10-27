#!/usr/bin/env python3
"""agent-quality-security 门禁适配器。

设计原则：**能跑的真跑，跑不了的说明原因，绝不假装通过。**

两类门禁：
  轻量（每次都能跑）：unit-tests、business-outcome —— 纯 Python，几秒
  容器（需要 docker）：其余八道 —— 首次或显式要求时才跑，结果缓存 10 分钟

容器门禁的落地方式是调用项目自己的 scripts/run_external.py <tool>，
它内部用 docker run --rm 起一次性容器，镜像按 digest 钉死。

注意沙箱边界：DSH 的受限进程无法打开 docker 的命名管道（npipe），
从这类进程调用会得到 "permission denied while trying to connect to the docker API"。
这种情况报 unavailable 并附上原始错误，而不是 failed —— 因为它是环境限制，不是质量缺陷。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# 门禁 id -> run_external.py 的 tool 名
CONTAINER_GATES = {
    "agent-security": "promptfoo",
    "performance": "k6",
    "source-security": "semgrep",
    "dependency-secret": "trivy",
    "web-dynamic": "zap",
}
# 需要外部服务（Phoenix / 浏览器）的门禁，单列以便给出更准确的原因
SERVICE_GATES = {
    "telemetry": "需要 OpenTelemetry 采集端 + Phoenix 容器",
    "agent-rag-eval": "需要 Phoenix 容器（observability/docker-compose.yml）",
    "web-ui-accessibility": "需要 node + playwright 浏览器 + 可访问的页面",
}

CACHE_TTL = 600  # 秒


def _cache_path() -> Path:
    base = Path(os.environ.get("EVOLUTION_GATE_CACHE",
                               r"D:\start\ds-harness\work\_qs-store\gates-cache.json"))
    base.parent.mkdir(parents=True, exist_ok=True)
    return base


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


def _run(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str, float]:
    t0 = time.time()
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    # 沙箱下系统 Temp 不可写，tempfile 会找不到可用目录；指向工作区
    _tmp = os.environ.get("EVOLUTION_TMP", r"D:\start\ds-harness\work\_tmp")
    os.makedirs(_tmp, exist_ok=True)
    env["TMP"] = env["TEMP"] = _tmp
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, env=env)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or ""), time.time() - t0
    except subprocess.TimeoutExpired:
        return 124, "timeout", time.time() - t0
    except Exception as exc:  # noqa: BLE001
        return 125, f"{type(exc).__name__}: {exc}", time.time() - t0


def _tail(text: str, n: int = 300) -> str:
    text = " ".join(text.split())
    return text[-n:] if len(text) > n else text


def _classify(rc: int, out: str) -> tuple[str, str]:
    low = out.lower()
    if "permission denied while trying to connect to the docker api" in low:
        return "unavailable", "沙箱不允许子进程访问 docker 命名管道（环境限制，非质量缺陷）"
    if "cannot connect to the docker daemon" in low or "is the docker daemon running" in low:
        return "unavailable", "docker daemon 未运行"
    if rc == 0:
        return "passed", _tail(out)
    return "failed", _tail(out)


def _light_gates(project_root: Path, py: str, timeout: int) -> list[dict]:
    out = []
    rc, txt, secs = _run([py, "-m", "unittest", "discover", "-s", "tests", "-q"], project_root, timeout)
    out.append({"id": "unit-tests", "status": "passed" if rc == 0 else "failed",
                "seconds": round(secs, 2), "detail": _tail(txt)})
    rc, txt, secs = _run([py, "qualityctl.py", "demo"], project_root, timeout)
    out.append({"id": "business-outcome", "status": "passed" if rc == 0 else "failed",
                "seconds": round(secs, 2), "detail": _tail(txt)})
    return out


def _container_gates(project_root: Path, py: str, timeout: int) -> list[dict]:
    out = []
    for gate, tool in CONTAINER_GATES.items():
        rc, txt, secs = _run([py, "scripts/run_external.py", tool], project_root, timeout)
        status, detail = _classify(rc, txt)
        out.append({"id": gate, "status": status, "seconds": round(secs, 2), "detail": detail})
    for gate, reason in SERVICE_GATES.items():
        out.append({"id": gate, "status": "unavailable", "seconds": 0, "detail": reason})
    return out


def run_gates(project_root: Path, python_exe: str | None = None,
              timeout: int = 600, heavy: bool = False) -> list[dict]:
    """heavy=False 时容器门禁读缓存（10 分钟有效），不会每次都跑 ZAP/promptfoo。"""
    py = python_exe or sys.executable
    gates = _light_gates(project_root, py, min(timeout, 180))
    # EVOLUTION_GATE_SET=light 只跑轻量门禁（测试/快速检查用），默认跑全部
    if os.environ.get("EVOLUTION_GATE_SET", "full").lower() == "light":
        return gates

    cache = _load_cache()
    fresh = bool(cache) and (time.time() - cache.get("at", 0) < CACHE_TTL)
    cached = {g["id"]: g for g in cache.get("gates", [])}

    if heavy or not fresh:
        heavy_results = _container_gates(project_root, py, timeout)
        _save_cache({"at": time.time(), "gates": heavy_results})
        gates.extend(heavy_results)
        if not heavy:
            # 非 heavy 但缓存过期时也跑了，标注一下来源
            for g in gates:
                if g["id"] in CONTAINER_GATES:
                    g["detail"] = "[fresh] " + g["detail"]
    else:
        for gate in list(CONTAINER_GATES) + list(SERVICE_GATES):
            gates.append(cached.get(gate, {"id": gate, "status": "unavailable",
                                           "seconds": 0, "detail": "尚无缓存，用 heavy=true 跑一次"}))
    return gates


def summarize(gates: list[dict]) -> dict:
    passed = sum(1 for g in gates if g["status"] == "passed")
    failed = sum(1 for g in gates if g["status"] == "failed")
    unavailable = sum(1 for g in gates if g["status"] == "unavailable")
    return {"total": len(gates), "passed": passed, "failed": failed,
            "unavailable": unavailable,
            "all_required_passed": failed == 0 and unavailable == 0}
