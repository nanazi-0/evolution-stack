#!/usr/bin/env python3
"""agent-quality-security 门禁适配器。

只做一件事：把它的十道门禁里**当前环境真能跑**的那几道跑一遍，如实返回状态。
跑不了的明确标 unavailable 并给出原因，绝不假装通过。

依赖约定（都在 agent-quality-security 自己的文档里）：
  unit-tests         python -m unittest discover -s tests
  business-outcome   python qualityctl.py demo
其余八道需要 Docker 或 k6/semgrep/trivy/zap，环境不具备时为 unavailable。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

# 需要容器或外部二进制的门禁 -> 缺失时报告的原因
CONTAINER_GATES = {
    "telemetry": "需要 docker + Phoenix",
    "agent-rag-eval": "需要 docker + Phoenix",
    "agent-security": "需要 docker + promptfoo",
    "web-ui-accessibility": "需要 node + playwright + axe",
    "performance": "需要 docker + k6",
    "source-security": "需要 docker + semgrep",
    "dependency-secret": "需要 docker + trivy",
    "web-dynamic": "需要 docker + ZAP",
}


def _run(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str, float]:
    t0 = time.time()
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, env=env)
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out, time.time() - t0
    except subprocess.TimeoutExpired:
        return 124, "timeout", time.time() - t0
    except Exception as exc:  # noqa: BLE001
        return 125, f"{type(exc).__name__}: {exc}", time.time() - t0


def _tail(text: str, n: int = 240) -> str:
    text = " ".join(text.split())
    return text[-n:] if len(text) > n else text


def run_gates(project_root: Path, python_exe: str | None = None, timeout: int = 180) -> list[dict]:
    py = python_exe or sys.executable
    results: list[dict] = []

    # 1) 单元测试
    rc, out, secs = _run([py, "-m", "unittest", "discover", "-s", "tests", "-q"], project_root, timeout)
    results.append({
        "id": "unit-tests",
        "status": "passed" if rc == 0 else "failed",
        "seconds": round(secs, 2),
        "detail": _tail(out),
    })

    # 2) 业务结果 + 安全 demo
    rc, out, secs = _run([py, "qualityctl.py", "demo"], project_root, timeout)
    results.append({
        "id": "business-outcome",
        "status": "passed" if rc == 0 else "failed",
        "seconds": round(secs, 2),
        "detail": _tail(out),
    })

    # 3) 其余八道：环境不具备时如实标 unavailable
    for gate, reason in CONTAINER_GATES.items():
        results.append({"id": gate, "status": "unavailable", "seconds": 0, "detail": reason})

    return results


def summarize(gates: list[dict]) -> dict:
    passed = sum(1 for g in gates if g["status"] == "passed")
    failed = sum(1 for g in gates if g["status"] == "failed")
    unavailable = sum(1 for g in gates if g["status"] == "unavailable")
    return {"total": len(gates), "passed": passed, "failed": failed,
            "unavailable": unavailable,
            "all_required_passed": failed == 0 and unavailable == 0}
