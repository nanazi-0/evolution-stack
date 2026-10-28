#!/usr/bin/env python3
"""一轮自动进化（供计划任务调用）。

做三件事：
  1. 对配置里的每个项目跑一次 evolve（预算按 level 卡住）
  2. 只有"冠军有实质改进"的才留在待批准队列
  3. 写 work/evolution-state/pending.json，供 pending_approvals / 人工批准

**绝不自动批准**——ADR-002 要求具名批准，机器只能把候选摆到桌上。

配置：control/cycle.toml
  [cycle]
  level = "l1"
  candidates = 1
  propose_mode = "echo"
  engine = "echo"
  projects = ["shop-support"]

用法：python control/evolve-cycle.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import server  # noqa: E402

CONF = Path(os.environ.get("EVOLUTION_CYCLE_CONF",
                           r"D:\start\ds-harness\evolution-stack\control\cycle.toml"))
OUT = Path(os.environ.get("EVOLUTION_PENDING",
                          r"D:\start\ds-harness\work\evolution-state\pending.json"))


def load_conf() -> dict:
    cfg, section = {}, ""
    if not CONF.exists():
        return cfg
    for line in CONF.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1]
            continue
        if "=" in s:
            k, v = s.split("=", 1)
            v = v.strip().strip('"')
            if v.startswith("[") and v.endswith("]"):
                v = [x.strip().strip('"') for x in v[1:-1].split(",") if x.strip()]
            cfg[f"{section}.{k.strip()}"] = v
    return cfg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--project", action="append")
    args = ap.parse_args()

    cfg = load_conf()
    level = str(cfg.get("cycle.level", "l1"))
    candidates = int(cfg.get("cycle.candidates", 1))
    propose_mode = str(cfg.get("cycle.propose_mode", "echo"))
    engine = str(cfg.get("cycle.engine", "echo"))
    projects = args.project or cfg.get("cycle.projects") or []
    if isinstance(projects, str):
        projects = [projects]

    print(f"cycle start {time.strftime('%Y-%m-%d %H:%M:%S')} level={level} "
          f"cand={candidates} engine={engine} projects={projects}", flush=True)
    if args.dry_run:
        print("dry-run: 不实际执行")
        return 0

    results = []
    for p in projects:
        try:
            res = server.t_evolve_run({
                "level": level, "project": p, "candidates": candidates,
                "propose_mode": propose_mode, "engine": engine,
                "objective": f"自动进化周期：{p}",
            })
            champ = res.get("champion") or {}
            improved = bool(res.get("champion_improved"))
            results.append({"project": p, "run_id": res.get("run_id"),
                            "error": res.get("error"),
                            "baseline": res.get("baseline_score"),
                            "champion": champ.get("score"),
                            "delta": champ.get("delta"),
                            "pending": improved})
            print(f"  {p}: baseline={res.get('baseline_score')} champion={champ.get('score')} "
                  f"delta={champ.get('delta')} pending={improved}", flush=True)
        except Exception as exc:  # noqa: BLE001
            results.append({"project": p, "error": f"{type(exc).__name__}: {exc}"})
            print(f"  {p}: ERROR {exc}", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    prev = []
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8")).get("runs", [])
        except Exception:  # noqa: BLE001
            prev = []
    merged = (prev + results)[-50:]
    OUT.write_text(json.dumps({"at": int(time.time()), "runs": merged},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    waiting = sum(1 for r in results if r.get("pending"))
    print(f"cycle done: {waiting} 个等待批准，写入 {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
