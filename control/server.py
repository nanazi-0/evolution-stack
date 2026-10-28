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
import shutil
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STACK_ROOT = ROOT.parent
STATE_DIR = Path(os.environ.get("EVOLUTION_STATE_DIR", r"D:\start\ds-harness\work\evolution-state"))
STATE_FILE = STATE_DIR / "state.json"

EVAL_DIR = ROOT / "eval"
PROJECT_DEFAULT = os.environ.get("EVOLUTION_PROJECT", "default")
CANDIDATES_ROOT = Path(os.environ.get(
    "EVOLUTION_CANDIDATES_ROOT",
    str(STACK_ROOT / "local" / "agent-evolution-platform" / "targets")))
CANDIDATE_DIR = Path(os.environ.get(
    "EVOLUTION_CANDIDATE",
    STACK_ROOT / "local" / "agent-evolution-platform" / "targets" / "demo-agent" / "baseline"))
CASES_FILE = Path(os.environ.get("EVOLUTION_CASES", EVAL_DIR / "cases" / "smoke.json"))


ENGINE = os.environ.get("EVOLUTION_ENGINE", "echo").lower()
DSH_VERSION = os.environ.get("EVOLUTION_DSH_VERSION", "0.2.1-rc.3")
DSH_PROFILE = os.environ.get("EVOLUTION_DSH_PROFILE", "headless")
DSH_TIMEOUT = int(os.environ.get("EVOLUTION_DSH_TIMEOUT", "240"))


def candidate_for(project: str) -> Path:
    """每个项目一个候选目录；不存在则回退到默认基线。"""
    cand = CANDIDATES_ROOT / project / "baseline"
    return cand if cand.is_dir() else CANDIDATE_DIR


def cases_for(project: str, override: str | None = None) -> Path:
    """用例优先级：显式参数 > 项目自带 cases.json > 环境变量 > 默认。"""
    if override:
        p = Path(override)
        return p if p.exists() else CASES_FILE
    proj = CANDIDATES_ROOT / project / "cases.json"
    if proj.exists():
        return proj
    return CASES_FILE


def latest_run(state: dict, project: str | None = None):
    """只在指定项目内取最新 run，避免跨项目误批。"""
    runs = [r for r in state["runs"] if project is None or r.get("project", "default") == project]
    return runs[-1] if runs else None


def run_evaluation(engine_name: str | None = None, candidate: Path | None = None,
                   cases: Path | None = None) -> dict:
    """调用执行器适配器拿一份信封（ADR-006：只有 DSH 或离线引擎，禁止外部 substrate）。

    engine_name: "echo"（默认，秒回零成本）或 "dsh"（真模型，每条用例约 1-2 分钟）。
    """
    name = (engine_name or ENGINE or "echo").lower()
    if str(EVAL_DIR) not in sys.path:
        sys.path.insert(0, str(EVAL_DIR))
    if name == "dsh":
        if not os.environ.get("DEEPSEEK_API_KEY"):
            raise RuntimeError("真引擎需要 DEEPSEEK_API_KEY（当前进程环境里没有）")
        from runner import DshEngine, run_eval
        extra = []
        patch = os.environ.get("EVOLUTION_PATCH")
        if patch:
            extra = ["--patch", patch]
        engine = DshEngine(DSH_VERSION, DSH_PROFILE, extra_args=extra)
    elif name == "echo":
        from runner import EchoEngine, run_eval
        engine = EchoEngine()
    else:
        raise ValueError(f"未知引擎 {name}，只允许 echo / dsh")
    return run_eval(candidate or CANDIDATE_DIR, cases or CASES_FILE, engine, DSH_TIMEOUT)

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
    project = str(args.get("project") or PROJECT_DEFAULT)
    engine_name = str(args.get("engine", "echo")).lower()
    if engine_name not in ("echo", "dsh"):
        return {"error": f"未知引擎 {engine_name}，只允许 echo / dsh"}
    baseline_dir = candidate_for(project)
    cases_path = cases_for(project, args.get("cases"))
    try:
        evaluation = run_evaluation(engine_name, baseline_dir, cases_path)
    except Exception as exc:                      # 评估失败不能静默变通过
        evaluation = {"passed": False, "score": 0.0, "metrics": {},
                      "failures": [{"id": "evaluator-error", "detail": f"{type(exc).__name__}: {exc}"}]}
    run = {
        "run_id": run_id,
        "level": level,
        "objective": str(args.get("objective", "")),
        "project": project,
        "budget": budget,
        "max_generations": requested,
        "status": "eligible",           # ADR-002：对比式/断言式只能产 eligible
        "created_at": int(time.time()),
        "engine": engine_name,
        "candidates": [],
        "gates": [],
        "evaluation": evaluation,       # 真实信封（执行器适配器产出）
        "approved": None,
    }
    # ---- PPC：产生并探测候选（ADR-002 只到 eligible，不自动晋升）----
    k = int(args.get("candidates", 0) or 0)
    cap = int(budget["candidates"])
    if k > cap:
        return {"error": f"ADR-005：{level} 最多 {cap} 个候选，请求了 {k}；要更多请升级 level",
                "level": level, "budget": budget}
    if k > 0:
        mode = str(args.get("propose_mode", "echo")).lower()
        base_score = float((evaluation.get("metrics") or {}).get("task_success") or 0.0)
        proposal_root = Path(os.environ.get(
            "EVOLUTION_PROPOSALS", r"D:\start\ds-harness\work\evolution-state\proposals")) / run_id
        try:
            if str(ADAPTER_DIR) not in sys.path:
                sys.path.insert(0, str(ADAPTER_DIR))
            from proposer import propose as _propose
            prop = _propose(baseline_dir, k, run.get("objective", ""), mode, proposal_root)
        except Exception as exc:  # noqa: BLE001
            prop = {"error": f"{type(exc).__name__}: {exc}", "variants": []}
        cands = []
        for v in prop.get("variants", []):
            if v.get("rejected"):
                cands.append({"id": v["id"], "status": "rejected", "reason": v["rejected"]})
                continue
            try:
                env_v = run_evaluation(engine_name, Path(v["path"]), cases_path)
                score = float((env_v.get("metrics") or {}).get("task_success") or 0.0)
                cands.append({"id": v["id"], "status": "probed", "score": score,
                              "path": v["path"],
                              "delta": round(score - base_score, 4)})
            except Exception as exc:  # noqa: BLE001
                cands.append({"id": v["id"], "status": "error", "reason": f"{type(exc).__name__}: {exc}"})
        run["candidates"] = cands
        probed = [c for c in cands if c.get("status") == "probed"]
        if probed:
            best = max(probed, key=lambda c: c["score"])
            run["champion"] = best
            run["baseline_score"] = round(base_score, 4)
            run["champion_improved"] = best["score"] >= base_score + 0.01
        run["proposal"] = {"mode": prop.get("mode", mode), "count": len(cands),
                           "error": prop.get("error")}

    state["runs"].append(run)
    _audit(state, "evolve.run", {"run_id": run_id, "level": level, "engine": engine_name,
                                 "score": evaluation.get("score"), "passed": evaluation.get("passed")})
    _save(state)
    return {"run_id": run_id, "project": project, "status": run["status"], "budget": budget, "engine": engine_name,
            "baseline_score": run.get("baseline_score"),
            "champion": run.get("champion"),
            "candidates": run.get("candidates", []),
            "evaluation": {k: evaluation.get(k) for k in ("passed", "score", "metrics", "failures")},
            "next": "用 gate_status 看门禁与评估；approve_release 才能晋升"}


def t_evolve_status(args: dict) -> dict:
    state = _load()
    project = str(args.get("project") or PROJECT_DEFAULT)
    run = _find(state, str(args.get("run_id", "latest")))
    if run is None and args.get("run_id", "latest") == "latest":
        run = latest_run(state, project)
    if run is None:
        return {"error": f"项目 {project} 下没有找到 run"}
    return {k: run[k] for k in ("run_id", "project", "level", "status", "objective", "created_at")}


QS_ROOT = Path(os.environ.get(
    "EVOLUTION_QUALITY_ROOT",
    STACK_ROOT / "local" / "agent-quality-security"))
ADAPTER_DIR = ROOT / "adapters"


import threading

_GATE_JOB = {"running": False, "started": 0, "finished": 0, "error": None, "summary": None}


def gate_job_state() -> dict:
    return dict(_GATE_JOB)


def start_gate_run() -> dict:
    """后台跑一遍完整 pipeline（约 4-5 分钟），立即返回。"""
    if _GATE_JOB["running"]:
        return {"started": False, "reason": "already running", "job": gate_job_state()}
    _GATE_JOB.update({"running": True, "started": int(time.time()), "error": None})

    def _work():
        try:
            gates, summary = collect_gates(heavy=True)
            _GATE_JOB["summary"] = summary
        except Exception as exc:  # noqa: BLE001
            _GATE_JOB["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            _GATE_JOB["running"] = False
            _GATE_JOB["finished"] = int(time.time())

    threading.Thread(target=_work, daemon=True).start()
    return {"started": True, "job": gate_job_state(), "hint": "用 gate_status 轮询，完成后会给逐项结果"}


def collect_gates(heavy: bool = False) -> tuple[list[dict], dict]:
    """跑 agent-quality-security 里当前环境能跑的门禁。"""
    if str(ADAPTER_DIR) not in sys.path:
        sys.path.insert(0, str(ADAPTER_DIR))
    from quality import run_gates, summarize
    gates = run_gates(QS_ROOT, heavy=heavy)
    return gates, summarize(gates)


def t_gate_status(args: dict) -> dict:
    state = _load()
    run = _find(state, str(args.get("run_id", ""))) or latest_run(state, str(args.get("project") or PROJECT_DEFAULT))
    if run is None:
        return {"error": "没有找到 run"}
    try:
        gates, summary = collect_gates(heavy=False)
        err = None
    except Exception as exc:  # noqa: BLE001
        gates, summary, err = [], {"total": 0}, f"{type(exc).__name__}: {exc}"
    evaluation = run.get("evaluation")
    out = {"run_id": run["run_id"], "status": run["status"],
           "evaluation": evaluation, "gates": gates, "summary": summary,
           "gate_job": gate_job_state(),
           "note": "gates 来自 agent-quality-security 最近一次报告；要重跑请调 gate_run"}
    if err:
        out["gate_error"] = err
    return out


def t_pending(args: dict) -> dict:
    """列出等待批准的 run（eligible + 冠军有实质改进 + 未批准）。"""
    state = _load()
    project = args.get("project")
    out = []
    for r in state["runs"]:
        if r.get("status") != "eligible":
            continue
        if project and r.get("project", "default") != project:
            continue
        champ = r.get("champion")
        if not champ or not r.get("champion_improved"):
            continue
        out.append({"run_id": r["run_id"], "project": r.get("project", "default"),
                    "created_at": r.get("created_at"), "engine": r.get("engine"),
                    "baseline_score": r.get("baseline_score"),
                    "champion": {"id": champ.get("id"), "score": champ.get("score"),
                                 "delta": champ.get("delta")},
                    "objective": r.get("objective", "")[:60]})
    out.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return {"pending": out, "count": len(out),
            "hint": "用 approve_release(run_id, actor, reason) 批准"}


def t_gate_run(args: dict) -> dict:
    return start_gate_run()


def t_approve(args: dict) -> dict:
    actor = str(args.get("actor", "")).strip()
    reason = str(args.get("reason", "")).strip()
    if not actor or not reason:
        return {"error": "ADR-002：批准必须给出具名 actor 和 reason，不允许匿名自动化"}
    state = _load()
    run = _find(state, str(args.get("run_id", ""))) or latest_run(state, str(args.get("project") or PROJECT_DEFAULT))
    if run is None:
        return {"error": "没有找到 run"}

    # ADR-002：门禁是批准的硬前提
    try:
        gates, summary = collect_gates(heavy=False)
        gate_err = None
    except Exception as exc:  # noqa: BLE001
        gates, summary, gate_err = [], {"failed": -1, "unavailable": -1}, f"{type(exc).__name__}: {exc}"
    if gate_err:
        return {"error": f"ADR-002：门禁无法执行，拒绝批准（{gate_err}）"}
    if summary.get("failed", 0) > 0:
        bad = [g["id"] for g in gates if g["status"] == "failed"]
        return {"error": f"ADR-002：有门禁失败，拒绝批准: {bad}"}
    if summary.get("unavailable", 0) > 0 and not args.get("acknowledge_unavailable"):
        miss = [g["id"] for g in gates if g["status"] == "unavailable"]
        return {"error": (f"ADR-002：{len(miss)} 道门禁未执行（环境缺依赖）: {miss}。"
                          f"如确认接受，请传 acknowledge_unavailable=true 并在 reason 里写明原因"),
                "gates": gates, "summary": summary}

    # ADR-002：冠军必须有实质改进才允许晋升（minimum_improvement 默认 0.01）
    champ0 = run.get("champion")
    if champ0 and not run.get("champion_improved", False):
        return {"error": (f"ADR-002：冠军 {champ0.get('id')} 相对基线没有实质改进"
                          f"（delta={champ0.get('delta')}），拒绝晋升"),
                "baseline_score": run.get("baseline_score"), "champion": champ0}

    # 晋升：把冠军变体固化成 release，并更新 current 指针（ADR-004：只有 approved 才落盘）
    promotion = None
    champ = run.get("champion")
    if champ and champ.get("path"):
        try:
            proj_root = CANDIDATES_ROOT / run.get("project", PROJECT_DEFAULT)
            rel_dir = proj_root / "releases" / run["run_id"]
            if rel_dir.exists():
                shutil.rmtree(rel_dir, ignore_errors=True)
            shutil.copytree(Path(champ["path"]), rel_dir)
            cur = proj_root / "current.json"
            prev = json.loads(cur.read_text(encoding="utf-8")) if cur.exists() else None
            cur.write_text(json.dumps({"run_id": run["run_id"], "path": str(rel_dir),
                                       "previous": (prev or {}).get("run_id"),
                                       "approved_by": actor, "at": int(time.time())},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
            promotion = {"release": str(rel_dir), "previous": (prev or {}).get("run_id")}
        except Exception as exc:  # noqa: BLE001
            promotion = {"error": f"{type(exc).__name__}: {exc}"}

    run["approved"] = {"actor": actor, "reason": reason, "at": int(time.time()),
                       "promotion": promotion,
                       "gates": summary, "acknowledged_unavailable": bool(args.get("acknowledge_unavailable"))}
    run["status"] = "approved"
    _audit(state, "release.approve", {"run_id": run["run_id"], "actor": actor,
                                      "gates": summary,
                                      "acknowledged": bool(args.get("acknowledge_unavailable"))})
    _save(state)
    return {"run_id": run["run_id"], "status": "approved", "actor": actor, "gates": summary}


def t_rollback(args: dict) -> dict:
    actor = str(args.get("actor", "")).strip() or "unknown"
    project = str(args.get("project") or PROJECT_DEFAULT)
    state = _load()
    cur_path = CANDIDATES_ROOT / project / "current.json"
    if not cur_path.exists():
        return {"error": f"项目 {project} 没有发布指针，无法回滚"}
    try:
        cur = json.loads(cur_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"current.json 损坏: {exc}"}
    prev = cur.get("previous")
    if not prev:
        return {"error": "没有上一版可回滚（previous 为空）", "current": cur.get("run_id")}
    prev_dir = CANDIDATES_ROOT / project / "releases" / prev
    if not prev_dir.is_dir():
        return {"error": f"上一版 {prev} 的目录不存在", "current": cur.get("run_id")}

    # 真正把指针移回去：current -> prev，并把 prev 的 previous 记录成刚被回滚的版本
    prev_meta_path = prev_dir.parent.parent / "releases" / prev / "release.json"
    cur_after = {"run_id": prev, "path": str(prev_dir),
                 "previous": None,   # 回滚是单向的：再想前进要重新批准
                 "approved_by": cur.get("approved_by"),
                 "rolled_back_by": actor, "rolled_back_at": int(time.time())}
    cur_path.write_text(json.dumps(cur_after, ensure_ascii=False, indent=2), encoding="utf-8")

    run = _find(state, str(cur.get("run_id") or "")) or latest_run(state, project)
    if run is not None:
        run["status"] = "rolled_back"
    _audit(state, "release.rollback", {"from": cur.get("run_id"), "to": prev, "actor": actor,
                                       "project": project})
    _save(state)
    return {"project": project, "from": cur.get("run_id"), "to": prev,
            "status": "rolled_back", "path": str(prev_dir)}


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


def _memory():
    if str(ADAPTER_DIR) not in sys.path:
        sys.path.insert(0, str(ADAPTER_DIR))
    import memory as mem
    return mem


def t_memory_recall(args: dict) -> dict:
    mem = _memory()
    project = str(args.get("project") or PROJECT_DEFAULT)
    out = mem.recall(project, str(args.get("query", "")), int(args.get("top_k", 5)))
    out["query"] = args.get("query", "")
    return out


def t_memory_commit(args: dict) -> dict:
    state = _load()
    run = _find(state, str(args.get("run_id", ""))) or latest_run(state, str(args.get("project") or PROJECT_DEFAULT))
    if run is None:
        return {"error": "没有找到 run"}
    if run["status"] != "approved":   # ADR-004 硬规则
        return {"error": f"ADR-004：只有 approved 的 run 能写入记忆，当前是 {run['status']}"}
    mem = _memory()
    ev = run.get("evaluation") or {}
    summary = (f"run={run['run_id']} level={run['level']} engine={run.get('engine')} "
               f"score={ev.get('score')} passed={ev.get('passed')} objective={run.get('objective','')}")
    res = mem.remember(run.get("project", PROJECT_DEFAULT), run["run_id"], summary,
                       {"level": run["level"], "engine": run.get("engine")})
    state["memory"].append({"run_id": run["run_id"], "at": int(time.time()),
                            "backend": res.get("backend"), "ok": res.get("ok")})
    _audit(state, "memory.commit", {"run_id": run["run_id"], "backend": res.get("backend")})
    _save(state)
    return {"committed": bool(res.get("ok")), "run_id": run["run_id"], "memory": res}


TOOLS = [
    ("evolve_run", "跑一代进化。参数 level(l1/l2/l3)、objective、max_generations、engine(echo 秒回不花钱 / dsh 真模型约 4 分钟)", t_evolve_run,
     {"type": "object", "properties": {"level": {"type": "string"}, "objective": {"type": "string"},
                                       "project": {"type": "string"},
                                       "candidates": {"type": "integer"},
                                       "cases": {"type": "string"},
                                       "propose_mode": {"type": "string", "enum": ["echo", "llm"]},
                                       "max_generations": {"type": "integer"},
                                       "engine": {"type": "string", "enum": ["echo", "dsh"]}}}),
    ("evolve_status", "查看某个 run 的状态（按项目隔离）", t_evolve_status,
     {"type": "object", "properties": {"run_id": {"type": "string"}, "project": {"type": "string"}}}),
    ("gate_status", "查看十道门禁的逐项结果与证据", t_gate_status,
     {"type": "object", "properties": {"run_id": {"type": "string"}}}),
    ("approve_release", "具名批准晋升（必须给 actor 和 reason）", t_approve,
     {"type": "object", "properties": {"run_id": {"type": "string"}, "actor": {"type": "string"},
                                       "reason": {"type": "string"}, "acknowledge_unavailable": {"type": "boolean"}}, "required": ["actor", "reason"]}),
    ("rollback_release", "回滚到上一 approved", t_rollback,
     {"type": "object", "properties": {"actor": {"type": "string"}, "reason": {"type": "string"}}}),
    ("gate_run", "后台重跑十道门禁（约 4-5 分钟，立即返回）", t_gate_run, {"type": "object", "properties": {}}),
    ("pending_approvals", "列出等待批准的 run", t_pending, {"type": "object", "properties": {"project": {"type": "string"}}}),
    ("audit_verify", "校验 SHA-256 审计链", t_audit_verify, {"type": "object", "properties": {}}),
    ("memory_recall", "按项目查历史经验", t_memory_recall,
     {"type": "object", "properties": {"query": {"type": "string"}, "project": {"type": "string"}, "top_k": {"type": "integer"}}}),
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
