#!/usr/bin/env python3
"""evolution-control 冒烟测试：验证 MCP 协议 + ADR 规则是否真的生效。

跑法：python control/_smoke.py
退出码 0 = 全部通过。
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE / "server.py"

proc = subprocess.Popen([sys.executable, str(SERVER)],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True, encoding="utf-8")

n = [0]
fail = []


def call(method, params=None):
    n[0] += 1
    msg = {"jsonrpc": "2.0", "id": n[0], "method": method}
    if params is not None:
        msg["params"] = params
    proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    return json.loads(line)


def tool(name, **args):
    r = call("tools/call", {"name": name, "arguments": args})
    text = r["result"]["content"][0]["text"]
    return json.loads(text)


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"  <- {detail}"))
    if not cond:
        fail.append(label)


print("== MCP 协议 ==")
init = call("initialize", {})
check("initialize 返回 serverInfo",
      init.get("result", {}).get("serverInfo", {}).get("name") == "evolution-control", init)
tl = call("tools/list", {})
names = [t["name"] for t in tl["result"]["tools"]]
check("tools/list 有 8 个工具", len(names) == 8, names)

print("== ADR-005 预算分级 ==")
r = tool("evolve_run", level="l1", objective="提高售后话术合规性")
check("l1 能起 run，状态是 eligible", r.get("status") == "eligible", r)
rid = r.get("run_id")
r2 = tool("evolve_run", level="l1", max_generations=2)
check("l1 拒绝多代（不得越级）", "error" in r2, r2)
r3 = tool("evolve_run", level="l9")
check("未知级别被拒", "error" in r3, r3)

print("== ADR-002 批准必须具名 ==")
a1 = tool("approve_release", run_id=rid)
check("缺 actor/reason 被拒", "error" in a1, a1)
a2 = tool("approve_release", run_id=rid, actor="测试员", reason="人工复核通过")
check("具名批准成功", a2.get("status") == "approved", a2)

print("== ADR-004 记忆只收 approved ==")
r4 = tool("evolve_run", level="l1", objective="第二个 run")
rid2 = r4.get("run_id")
m1 = tool("memory_commit", run_id=rid2)
check("未批准的 run 禁止写记忆", "error" in m1, m1)
m2 = tool("memory_commit", run_id=rid)
check("已批准的 run 可以写记忆", m2.get("committed") is True, m2)

print("== 审计链 ==")
av = tool("audit_verify")
check("哈希链完整", av.get("ok") is True and av.get("events", 0) >= 4, av)

print("== 门禁视图 ==")
g = tool("gate_status", run_id=rid)
check("返回 10 道门禁且全部未通过（骨架态）", g.get("total") == 10 and g.get("passed") == 0, g)

proc.stdin.close()
proc.wait(timeout=5)

print()
if fail:
    print(f"FAILED: {len(fail)} 项 -> {fail}")
    sys.exit(1)
print("ALL PASS")
