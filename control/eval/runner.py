#!/usr/bin/env python3
"""DSH 执行器适配器（ADR-006）。

职责：拿一份候选 harness（prompt / skills 快照），在隔离工作区里用 DSH 跑评测用例，
输出 agent-evolution-platform 认可的信封：

    {"passed": bool, "score": 0..1, "metrics": {...}, "failures": [...]}

ADR 约束在这里被强制：
  ADR-006  只允许 DSH 作为执行器；出现 --substrate / Codex 立即拒绝
  ADR-005  单次运行有用例数与超时上限
  ADR-002  分数只来自断言，不来自模型自述

用法：
  python runner.py --candidate <dir> --cases cases/foo.json --out result.json
  python runner.py ... --engine echo                 # 离线自检，不调用模型
  python runner.py ... --engine dsh --profile headless --dsh-version 0.2.1-rc.3
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

MAX_CASES = 50           # ADR-005 冒烟上限
DEFAULT_TIMEOUT = 180


# ---------------------------------------------------------------- 引擎
@dataclass
class RunResult:
    text: str
    seconds: float
    engine: str
    error: str = ""


class EchoEngine:
    """离线引擎：把候选的 system_prompt 原样返回。用于验证管道，不产生真实能力信号。"""

    name = "echo"

    def run(self, candidate: Path, prompt: str, timeout: int) -> RunResult:
        t0 = time.time()
        p = candidate / "system_prompt.md"
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        return RunResult(text=f"[echo] {text}\n{prompt}", seconds=time.time() - t0, engine=self.name)


class DshEngine:
    """真引擎：npx 调起 DSH headless，在候选工作区里跑一条任务。"""

    name = "dsh"

    def __init__(self, version: str, profile: str, extra_args: list[str] | None = None):
        # ADR-006：禁止把 harness 交给 Codex/OpenCode 等外部 substrate
        joined = " ".join(extra_args or [])
        if "--substrate" in joined or "codex" in joined.lower():
            raise ValueError("ADR-006：禁止使用非 DSH 执行器（--substrate / codex 一律拒绝）")
        self.version = version
        self.profile = profile
        self.extra = extra_args or []
        self._ws_cache: dict[str, Path] = {}

    def _workspace(self, candidate: Path) -> Path:
        """把候选 harness 映射成 DSH 能读的工作区。

        DSH 只读工作区里的 AGENTS.md，不认 system_prompt.md，
        所以这里做一次适配：candidate/system_prompt.md -> <ws>/AGENTS.md。
        每个候选一个临时工作区，不改动候选本身。
        """
        key = str(candidate.resolve())
        if key in self._ws_cache:
            return self._ws_cache[key]
        base = Path(os.environ.get("EVOLUTION_WORKSPACE_ROOT", r"D:\start\ds-harness\work\_eval-ws"))
        base.mkdir(parents=True, exist_ok=True)
        ws = base / re.sub(r"[^A-Za-z0-9_.-]", "_", candidate.name)
        if ws.exists():
            shutil.rmtree(ws, ignore_errors=True)
        ws.mkdir(parents=True, exist_ok=True)
        src = candidate / "system_prompt.md"
        body = src.read_text(encoding="utf-8") if src.exists() else ""
        (ws / "AGENTS.md").write_text(
            "# Candidate Harness\n\n" + body + "\n", encoding="utf-8")
        skills = candidate / "skills"
        if skills.is_dir():
            shutil.copytree(skills, ws / "skills", dirs_exist_ok=True)
        self._ws_cache[key] = ws
        return ws

    def run(self, candidate: Path, prompt: str, timeout: int) -> RunResult:
        t0 = time.time()
        ws = self._workspace(candidate)
        cmd = ["npx", "-y", f"@deepseek-ai/dsh@{self.version}",
               "--profile", self.profile, prompt] + self.extra
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        # 评测用独立 DSH_HOME：子进程只有工作区写权限，且不该碰生产 profile
        env["DSH_HOME"] = os.environ.get("EVOLUTION_DSH_HOME", r"D:\start\ds-harness\work\_eval-home")
        Path(env["DSH_HOME"]).mkdir(parents=True, exist_ok=True)
        try:
            # Windows 上 npx 是 .cmd，必须经 cmd.exe 解析；list2cmdline 负责正确加引号
            if os.name == "nt":
                proc = subprocess.run(subprocess.list2cmdline(cmd), cwd=str(ws),
                                      capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", timeout=timeout, env=env, shell=True)
            else:
                proc = subprocess.run(cmd, cwd=str(candidate), capture_output=True,
                                      text=True, encoding="utf-8", errors="replace",
                                      timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return RunResult("", time.time() - t0, self.name, error="timeout")
        except FileNotFoundError as exc:
            return RunResult("", time.time() - t0, self.name, error=f"engine missing: {exc}")
        out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.returncode else "")
        return RunResult(out, time.time() - t0, self.name,
                         error="" if proc.returncode == 0 else f"exit {proc.returncode}")


# ---------------------------------------------------------------- 断言
def check_assertion(text: str, a: dict) -> tuple[bool, str]:
    kind = a.get("type")
    val = a.get("value", "")
    if kind == "contains":
        ok = val in text
        return ok, "" if ok else f"缺少必需内容: {val!r}"
    if kind == "not_contains":
        ok = val not in text
        return ok, "" if ok else f"出现禁止内容: {val!r}"
    if kind == "regex":
        ok = re.search(val, text) is not None
        return ok, "" if ok else f"正则未匹配: {val!r}"
    if kind == "max_chars":
        ok = len(text) <= int(val)
        return ok, "" if ok else f"输出过长: {len(text)} > {val}"
    if kind == "min_chars":
        ok = len(text) >= int(val)
        return ok, "" if ok else f"输出过短: {len(text)} < {val}"
    return False, f"未知断言类型: {kind}"


# ---------------------------------------------------------------- 主流程
def run_eval(candidate: Path, cases_file: Path, engine, timeout: int) -> dict:
    spec = json.loads(cases_file.read_text(encoding="utf-8"))
    cases = spec.get("cases", [])
    if not cases:
        return {"passed": False, "score": 0.0, "metrics": {},
                "failures": [{"id": "no-cases", "detail": "用例文件里没有 cases"}]}
    if len(cases) > MAX_CASES:
        return {"passed": False, "score": 0.0, "metrics": {},
                "failures": [{"id": "too-many-cases", "detail": f"{len(cases)} > {MAX_CASES}（ADR-005）"}]}

    failures, latencies, passed = [], [], 0
    for c in cases:
        res = engine.run(candidate, c.get("prompt", ""), timeout)
        latencies.append(res.seconds)
        if res.error:
            failures.append({"id": c["id"], "layer": c.get("layer", "quality"),
                             "detail": f"engine error: {res.error}"})
            continue
        bad = [msg for ok, msg in (check_assertion(res.text, a) for a in c.get("assertions", [])) if not ok]
        if bad:
            failures.append({"id": c["id"], "layer": c.get("layer", "quality"),
                             "detail": "; ".join(bad)})
        else:
            passed += 1

    total = len(cases)
    score = passed / total if total else 0.0
    lat = sorted(latencies)
    p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else 0.0
    return {
        "passed": passed == total and total > 0,
        "score": round(score, 4),
        "metrics": {
            "task_success": round(score, 4),
            "cases_total": total,
            "cases_passed": passed,
            "p95_latency_ms": int(p95 * 1000),
            "engine": engine.name,
        },
        "failures": failures,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="DSH executor adapter for the evolution stack")
    ap.add_argument("--candidate", required=True, type=Path)
    ap.add_argument("--cases", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--engine", choices=["echo", "dsh"], default="echo")
    ap.add_argument("--profile", default="headless")
    ap.add_argument("--dsh-version", default="0.2.1-rc.3")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = ap.parse_args()

    if not args.candidate.is_dir():
        print(f"candidate 不存在: {args.candidate}", file=sys.stderr)
        return 2

    engine = EchoEngine() if args.engine == "echo" else DshEngine(args.dsh_version, args.profile)
    envelope = run_eval(args.candidate, args.cases, engine, args.timeout)
    envelope["candidate"] = str(args.candidate)
    envelope["evaluated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report={args.out}")
    print(json.dumps({k: envelope[k] for k in ("passed", "score", "metrics")}, ensure_ascii=False))
    return 0 if envelope["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
