"""通用工作流 runner：按 TOML 声明的链路执行步骤。

设计目标：**加一条链路只写配置，不改代码。**

  python runner.py --lane lanes/clip-refresh.toml
  python runner.py --lanes-dir lanes/ --list
  python runner.py --lane lanes/clip-refresh.toml --resume <run_id>
  python runner.py --lane lanes/clip-refresh.toml --approve <run_id>   # 人工步骤放行

步骤类型：
  script  跑一个确定性脚本（相对 control/ 的路径），有超时与重试
  llm     按能力解析模型并调用，累计预算，超预算即中断
  human   暂停，等待人工批准（状态置 awaiting_human，退出码 0）

状态：work/_workflows/<run_id>/state.json（支持断点续跑）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

CONTROL = Path(__file__).resolve().parent.parent      # .../control
STATE_ROOT = Path(r"D:\start\ds-harness\work\_workflows")
MODELS_FILE = Path(__file__).resolve().parent / "models.toml"


def load_toml(p: Path) -> dict:
    with p.open("rb") as fh:
        return tomllib.load(fh)


def load_models() -> dict:
    if not MODELS_FILE.exists():
        return {}
    return load_toml(MODELS_FILE).get("models", {})


def resolve_model(models: dict, requires: list[str], explicit: str | None) -> tuple[str, dict] | tuple[None, str]:
    """按能力挑模型；explicit 指定时优先。返回 (name, spec) 或 (None, 原因)。"""
    if explicit:
        spec = models.get(explicit)
        if not spec:
            return None, f"模型 {explicit} 不在注册表"
        return explicit, spec
    candidates = [(n, s) for n, s in models.items()
                  if all(c in (s.get("capabilities") or []) for c in requires)]
    # 优先 deepseek（已验证），其次其他已验证，最后未验证的排除
    candidates.sort(key=lambda x: (0 if x[0].startswith("deepseek") else 1))
    for n, s in candidates:
        if s.get("verified_at"):
            return n, s
    if candidates:
        return None, f"候选模型 {[c[0] for c in candidates]} 均未核实（verified_at 为空），拒绝调用"
    return None, f"没有模型具备能力 {requires}"


def call_llm(spec: dict, messages: list[dict], timeout: int = 120) -> tuple[str, dict]:
    key_env = spec.get("key_env") or ""
    key = os.environ.get(key_env, "") if key_env else ""
    if key_env and not key:
        raise RuntimeError(f"缺少环境变量 {key_env}（fail-closed）")
    body = {
        "model": spec.get("model"),
        "messages": messages,
        "temperature": float(spec.get("temperature", 0.2)),
    }
    req = urllib.request.Request(
        f"{spec['endpoint'].rstrip('/')}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {key}"} if key else {})},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    text = payload["choices"][0]["message"]["content"]
    usage = payload.get("usage") or {}
    return text, usage


def run_script(step: dict, run_dir: Path) -> dict:
    script = CONTROL / step["run"]
    if not script.exists():
        return {"status": "failed", "error": f"脚本不存在: {script}"}
    cmd = [sys.executable, str(script)] + [str(a) for a in step.get("args", [])]
    timeout = int(step.get("timeout_s", 300))
    retries = int(step.get("retries", 0))
    last = None
    for attempt in range(1, retries + 2):
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, cwd=str(CONTROL), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout)
            out = (proc.stdout or "") + (proc.stderr or "")
            (run_dir / f"{step['id']}-attempt{attempt}.log").write_text(out, encoding="utf-8")
            last = {"status": "ok" if proc.returncode == 0 else "failed",
                    "returncode": proc.returncode, "seconds": round(time.time() - t0, 1),
                    "attempt": attempt, "tail": " ".join(out.split())[-300:]}
            if proc.returncode == 0:
                return last
        except subprocess.TimeoutExpired:
            last = {"status": "failed", "error": f"超时 {timeout}s", "attempt": attempt}
        except Exception as exc:  # noqa: BLE001
            last = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "attempt": attempt}
    return last or {"status": "failed"}


def run_llm(step: dict, run_dir: Path, models: dict, state: dict, lane: dict) -> dict:
    name, spec = resolve_model(models, step.get("requires", []), step.get("model"))
    if name is None:
        return {"status": "failed", "error": spec}
    prompt = step.get("prompt", "")
    if step.get("prompt_file"):
        pf = CONTROL / step["prompt_file"]
        prompt = pf.read_text(encoding="utf-8") if pf.exists() else prompt
    if not prompt:
        return {"status": "failed", "error": "步骤没有 prompt / prompt_file"}
    # 注入输入文件（可限行数），让模型基于真实数据回答，而不是凭排名编造
    input_file = step.get("input_file")
    if input_file:
        ip = CONTROL / input_file
        if not ip.exists():
            return {"status": "failed", "error": f"input_file 不存在: {ip}"}
        raw = ip.read_text(encoding="utf-8-sig", errors="replace")
        max_lines = int(step.get("input_max_lines", 0) or 0)
        if max_lines:
            raw = "\n".join(raw.splitlines()[:max_lines])
        prompt = f"{prompt}\n\n===== 数据（{input_file}）=====\n{raw}"
    messages = [{"role": "system", "content": step.get("system", "你是一个严谨的分析助手。")},
                {"role": "user", "content": prompt}]
    budget = float(lane.get("budget_yuan") or 0)
    spent = float(state.get("spent_yuan") or 0)
    if budget and spent >= budget:
        return {"status": "failed", "error": f"预算已用尽 {spent:.2f}/{budget:.2f} 元"}
    try:
        text, usage = call_llm(spec, messages, int(step.get("timeout_s", 120)))
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    cost = None
    ci, co = spec.get("cost_in_per_1k"), spec.get("cost_out_per_1k")
    if ci is not None and co is not None:   # 未写价格 = 未核实，不计费
        cost = round(usage.get("prompt_tokens", 0) / 1000 * ci +
                     usage.get("completion_tokens", 0) / 1000 * co, 4)
        state["spent_yuan"] = round(spent + cost, 4)
    out_file = run_dir / f"{step['id']}.md"
    out_file.write_text(text, encoding="utf-8")
    return {"status": "ok", "model": name, "usage": usage, "cost_yuan": cost,
            "output": str(out_file), "chars": len(text)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane")
    ap.add_argument("--lanes-dir", default=str(Path(__file__).resolve().parent / "lanes"))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--resume")
    ap.add_argument("--approve")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.list:
        for f in sorted(Path(args.lanes_dir).glob("*.toml")):
            d = load_toml(f).get("lane", {})
            print(f"  {f.name:<28} {d.get('name','?'):<16} 步骤 {len(load_toml(f).get('step', []))} 个  {d.get('description','')}")
        return 0

    if not args.lane:
        print("需要 --lane 或 --list")
        return 2
    lane_doc = load_toml(Path(args.lane))
    lane = lane_doc.get("lane", {})
    steps = lane_doc.get("step", [])
    models = load_models()

    # 复用或新建 run
    if args.resume or args.approve:
        run_id = args.resume or args.approve
        run_dir = STATE_ROOT / run_id
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        if args.approve:
            for st in state["steps"]:
                if st["status"] == "awaiting_human":
                    st["status"] = "approved"
                    st["approved_at"] = int(time.time())
                    print(f"[approve] 步骤 {st['id']} 已放行")
            state["status"] = "running"
    else:
        run_id = datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        run_dir = STATE_ROOT / run_id
        state = {"run_id": run_id, "lane": lane.get("name"), "project": lane.get("project"),
                 "started_at": int(time.time()), "status": "running", "spent_yuan": 0.0,
                 "steps": [{"id": s["id"], "kind": s.get("kind"), "status": "pending"} for s in steps]}

    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[run] {run_id}  lane={lane.get('name')}  步骤 {len(steps)} 个")

    for i, step in enumerate(steps):
        st = state["steps"][i]
        if st["status"] in {"ok", "approved"}:
            print(f"  [{i+1}/{len(steps)}] {step['id']:<16} 跳过（已完成）")
            continue
        if args.dry_run:
            print(f"  [{i+1}/{len(steps)}] {step['id']:<16} (dry-run) kind={step.get('kind')}")
            continue

        kind = step.get("kind", "script")
        t0 = time.time()
        if kind == "script":
            res = run_script(step, run_dir)
        elif kind == "llm":
            res = run_llm(step, run_dir, models, state, lane)
        elif kind == "human":
            st["status"] = "awaiting_human"
            st["prompt"] = step.get("prompt", "")
            state["status"] = "awaiting_human"
            (run_dir / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  [{i+1}/{len(steps)}] {step['id']:<16} 等待人工：{step.get('prompt','')}")
            print(f"[pause] 用 --approve {run_id} 放行后继续")
            return 0
        else:
            res = {"status": "failed", "error": f"未知步骤类型 {kind}"}

        st.update(res)
        st["seconds"] = st.get("seconds") or round(time.time() - t0, 1)
        print(f"  [{i+1}/{len(steps)}] {step['id']:<16} {st['status']}" +
              (f"  ({st.get('seconds')}s)" if st.get("seconds") else "") +
              (f"  err={st.get('error')}" if st.get("error") else ""))
        (run_dir / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        if st["status"] == "failed":
            state["status"] = "failed"
            (run_dir / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[fail] 步骤 {step['id']} 失败，run 中止")
            return 1

    state["status"] = "completed"
    state["finished_at"] = int(time.time())
    (run_dir / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] {run_id} 完成，花费 {state.get('spent_yuan', 0)} 元 -> {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
