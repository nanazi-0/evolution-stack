#!/usr/bin/env python3
"""evolution-control: 四系统整合的唯一入口（MCP stdio server）。

暴露 8 个工具，把 ADR 的裁决编码进实现：
  ADR-002  eligible 只能由对比式产生，approved 必须经人工具名批准
  ADR-004  只有 approved 的 run 才允许写入记忆
  ADR-005  预算分级，参数不得越级
  ADR-006  只用 DSH 作为执行器（不调用 Harness² 的 Codex substrate）

当前实现为骨架：真实子系统尚未接线，但治理规则是生效的。
只依赖标准库。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STACK_ROOT = ROOT.parent
STATE_DIR = Path(os.environ.get("EVOLUTION_STATE_DIR", r"D:\start\ds-harness\work\evolution-state"))
STATE_FILE = STATE_DIR / "state.json"

EVAL_DIR = ROOT / "eval"
CANDIDATE_DIR = Path(os.environ.get(
    "EVOLUTION_CANDIDATE",
    STACK_ROOT / "local" / "agent-evolution-platform" / "targets" / "demo-agent" / "baseline"))
CASES_FILE = Path(os.environ.get("EVOLUTION_CASES", EVAL_DIR / "cases" / "smoke.json"))


def run_evaluation() -> dict:
    """调用执行器适配器拿一份真实信封（ADR-006：只有 DSH 或离线引擎，禁止外部 substrate）。"""
    if str(EVAL_DIR) not in sys.path:
        sys.path.insert(0, str(EVAL_DIR))
    from runner import EchoEngine, run_eval  # 延迟导入，避免无关工具启动变慢
    return run_eval(CANDIDATE_DIR, CASES_FILE, EchoEngine(), 60)

LEVELS = {  # ADR-005 预算分级
    "l1": {"candidates": 1, "tasks": 5, "gates": 3},
    "l2": {"candidates": 2, "tasks": 10, "gates": 6},
    "l3": {"candidates": 3, "tasks": 20, "gates": 10},
}

GATES = [  # agent-quality-security 的十道门禁
    "unit-tests", "business-outcome", "telemetry", "agent-rag-eval",
    "agent-security", "web-ui-accessibility", "performance",
    "source-security", "dependency-secret", "web-dynamic",
]


# ---------------------------------------------------------------- 状态
def _load() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"runs": [], "audit": [], "memory": []}


def _save(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _audit(state: dict, event: str, payload: dict) -> str:
    """ADR-002/004：追加式哈希链。"""
    prev = state["audit"][-1]["hash"] if state["audit"] else "genesis"
    body = {"seq": len(state["audit"]), "time": int(time.time()), "event": event,
            "payload": payload, "prev": prev}
    body["hash"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    state["audit"].append(body)
    return body["hash"]


def _find(state: dict, run_id: str) -> dict | None:
    for r in state["runs"]:
        if r["run_id"] == run_id:
            return r
    return None


# ---------------------------------------------------------------- 工具
def t_evolve_run(args: dict) -> dict:
    level = str(args.get("level", "l1")).lower()
    if level not in LEVELS:
        return {"error": f"未知级别 {level}，只允许 {list(LEVELS)}"}
    budget = LEVELS[level]
    requested = int(args.get("max_generations", 1))
    if requested > 1 and level == "l1":
        return {"error": "ADR-005：l1 只允许 1 代，先跑通再升级"}

    state = _load()
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
    try:
        evaluation = run_evaluation()
    except Exception as exc:                      # 评估失败不能静默变通过
        evaluation = {"passed": False, "score": 0.0, "metrics": {},
                      "failures": [{"id": "evaluator-error", "detail": f"{type(exc).__name__}: {exc}"}]}
    run = {
        "run_id": run_id,
        "level": level,
        "objective": str(args.get("objective", "")),
        "budget": budget,
        "max_generations": requested,
        "status": "eligible",           # ADR-002：对比式/断言式只能产 eligible
        "created_at": int(time.time()),
        "candidates": [],
        "gates": [],
        "evaluation": evaluation,       # 真实信封（执行器适配器产出）
        "approved": None,
    }
    state["runs"].append(run)
    _audit(state, "evolve.run", {"run_id": run_id, "level": level,
                                 "score": evaluation.get("score"), "passed": evaluation.get("passed")})
    _save(state)
    return {"run_id": run_id, "status": run["status"], "budget": budget,
            "evaluation": {k: evaluation.get(k) for k in ("passed", "score", "metrics")},
            "next": "用 gate_status 看门禁与评估；approve_release 才能晋升"}


def t_evolve_status(args: dict) -> dict:
    state = _load()
    run = _find(state, str(args.get("run_id", "latest")))
    if run is None and args.get("run_id", "latest") == "latest" and state["runs"]:
        run = state["runs"][-1]
    if run is None:
        return {"error": "没有找到 run"}
    return {k: run[k] for k in ("run_id", "level", "status", "objective", "created_at")}


def t_gate_status(args: dict) -> dict:
    state = _load()
    run = _find(state, str(args.get("run_id", ""))) or (state["runs"][-1] if state["runs"] else None)
    if run is None:
        return {"error": "没有找到 run"}
    gates = [{"id": g, "passed": False, "skipped": True,
              "detail": "未接 agent-quality-security（规则层就绪，执行未接）"} for g in GATES]
    evaluation = run.get("evaluation")
    return {"run_id": run["run_id"], "status": run["status"],
            "evaluation": evaluation,
            "gates": gates, "passed": 0, "total": len(gates),
            "note": "evaluation 来自执行器适配器（真实断言）；gates 仍需接 agent-quality-security"}


def t_approve(args: dict) -> dict:
    actor = str(args.get("actor", "")).strip()
    reason = str(args.get("reason", "")).strip()
    if not actor or not reason:
        return {"error": "ADR-002：批准必须给出具名 actor 和 reason，不允许匿名自动化"}
    state = _load()
    run = _find(state, str(args.get("run_id", ""))) or (state["runs"][-1] if state["runs"] else None)
    if run is None:
        return {"error": "没有找到 run"}
    run["approved"] = {"actor": actor, "reason": reason, "at": int(time.time())}
    run["status"] = "approved"
    _audit(state, "release.approve", {"run_id": run["run_id"], "actor": actor})
    _save(state)
    return {"run_id": run["run_id"], "status": "approved", "actor": actor}


def t_rollback(args: dict) -> dict:
    actor = str(args.get("actor", "")).strip() or "unknown"
    state = _load()
    run = state["runs"][-1] if state["runs"] else None
    if run is None:
        return {"error": "没有可回滚的 run"}
    run["status"] = "rolled_back"
    _audit(state, "release.rollback", {"run_id": run["run_id"], "actor": actor})
    _save(state)
    return {"run_id": run["run_id"], "status": "rolled_back"}


def t_audit_verify(args: dict) -> dict:
    state = _load()
    prev = "genesis"
    for i, ev in enumerate(state["audit"]):
        body = {k: ev[k] for k in ("seq", "time", "event", "payload", "prev")}
        expect = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if ev["hash"] != expect or ev["prev"] != prev:
            return {"ok": False, "broken_at": i}
        prev = ev["hash"]
    return {"ok": True, "events": len(state["audit"])}


def t_memory_recall(args: dict) -> dict:
    url = os.environ.get("OPENVIKING_URL", "")
    return {"query": args.get("query", ""), "hits": [],
            "note": "骨架：未接 OpenViking" if not url else f"骨架：已配置 {url}，但适配器未实现"}


def t_memory_commit(args: dict) -> dict:
    state = _load()
    run = _find(state, str(args.get("run_id", ""))) or (state["runs"][-1] if state["runs"] else None)
    if run is None:
        return {"error": "没有找到 run"}
    if run["status"] != "approved":   # ADR-004 硬规则
        return {"error": f"ADR-004：只有 approved 的 run 能写入记忆，当前是 {run['status']}"}
    state["memory"].append({"run_id": run["run_id"], "at": int(time.time())})
    _audit(state, "memory.commit", {"run_id": run["run_id"]})
    _save(state)
    return {"committed": True, "run_id": run["run_id"]}


TOOLS = [
    ("evolve_run", "跑一代进化。参数 level(l1/l2/l3)、objective、max_generations", t_evolve_run,
     {"type": "object", "properties": {"level": {"type": "string"}, "objective": {"type": "string"},
                                       "max_generations": {"type": "integer"}}}),
    ("evolve_status", "查看某个 run 的状态", t_evolve_status,
     {"type": "object", "properties": {"run_id": {"type": "string"}}}),
    ("gate_status", "查看十道门禁的逐项结果与证据", t_gate_status,
     {"type": "object", "properties": {"run_id": {"type": "string"}}}),
    ("approve_release", "具名批准晋升（必须给 actor 和 reason）", t_approve,
     {"type": "object", "properties": {"run_id": {"type": "string"}, "actor": {"type": "string"},
                                       "reason": {"type": "string"}}, "required": ["actor", "reason"]}),
    ("rollback_release", "回滚到上一 approved", t_rollback,
     {"type": "object", "properties": {"actor": {"type": "string"}, "reason": {"type": "string"}}}),
    ("audit_verify", "校验 SHA-256 审计链", t_audit_verify, {"type": "object", "properties": {}}),
    ("memory_recall", "查 OpenViking 历史经验", t_memory_recall,
     {"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}}}),
    ("memory_commit", "把已批准的 run 写入记忆（未批准会被拒绝）", t_memory_commit,
     {"type": "object", "properties": {"run_id": {"type": "string"}}}),
]


# ---------------------------------------------------------------- MCP 传输
def _reply(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(id_, payload: dict) -> None:
    _reply({"jsonrpc": "2.0", "id": id_,
            "result": {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
                       "isError": "error" in payload}})


def main() -> None:
    # MCP 走 UTF-8；Windows 默认 stdout 是 cp936，必须显式改，否则中文会乱码/解码失败
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        method = msg.get("method")
        id_ = msg.get("id")

        if method == "initialize":
            _reply({"jsonrpc": "2.0", "id": id_, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "evolution-control", "version": "0.1.0"}}})
        elif method in ("notifications/initialized", "initialized"):
            continue
        elif method == "ping":
            _reply({"jsonrpc": "2.0", "id": id_, "result": {}})
        elif method == "tools/list":
            _reply({"jsonrpc": "2.0", "id": id_, "result": {"tools": [
                {"name": n, "description": d, "inputSchema": s} for n, d, _, s in TOOLS]}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            fn = next((t[2] for t in TOOLS if t[0] == name), None)
            if fn is None:
                _reply({"jsonrpc": "2.0", "id": id_, "error": {"code": -32601, "message": f"unknown tool {name}"}})
                continue
            try:
                _result(id_, fn(args))
            except Exception as exc:  # 工具异常不崩服务
                _result(id_, {"error": f"{type(exc).__name__}: {exc}"})
        elif id_ is not None:
            _reply({"jsonrpc": "2.0", "id": id_, "error": {"code": -32601, "message": "method not found"}})


if __name__ == "__main__":
    main()
