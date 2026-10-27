#!/usr/bin/env python3
"""evolution-control 冒烟测试：验证 MCP 协议 + ADR 规则是否真的生效。

跑法：python control/_smoke.py
退出码 0 = 全部通过。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE / "server.py"

# 测试进程里去掉真实 key，保证 engine=dsh 走 fail-closed 分支（不花钱、结果确定）
_env = dict(os.environ)
_env.pop("DEEPSEEK_API_KEY", None)
_env["EVOLUTION_ENGINE"] = "echo"
_env["EVOLUTION_GATE_SET"] = "light"

proc = subprocess.Popen([sys.executable, str(SERVER)], env=_env,
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
check("run 带真实 evaluation 信封", isinstance(r.get("evaluation"), dict) and "score" in r["evaluation"], r.get("evaluation"))
rid = r.get("run_id")
r2 = tool("evolve_run", level="l1", max_generations=2)
check("l1 拒绝多代（不得越级）", "error" in r2, r2)
r3 = tool("evolve_run", level="l9")
check("未知级别被拒", "error" in r3, r3)

print("== ADR-002 批准必须具名 ==")
a1 = tool("approve_release", run_id=rid)
check("缺 actor/reason 被拒", "error" in a1, a1)
_g = tool("gate_status", run_id=rid)["summary"]
a0 = tool("approve_release", run_id=rid, actor="测试员", reason="未确认门禁状态")
# 规则断言：失败→拒绝；缺测→要求显式确认；全过→允许批准
if _g["failed"] > 0:
    check("有门禁失败时拒绝批准", "error" in a0 and "门禁失败" in a0["error"], a0)
elif _g["unavailable"] > 0:
    check("有缺测门禁时要求显式确认", "error" in a0 and "acknowledge_unavailable" in a0["error"], a0)
else:
    check("门禁全通过时无需确认即可批准", a0.get("status") == "approved", a0)
a2 = tool("approve_release", run_id=rid, actor="测试员", reason="人工复核通过", acknowledge_unavailable=True)
check("具名批准成功", a2.get("status") == "approved", a2)

print("== ADR-004 记忆只收 approved ==")
r4 = tool("evolve_run", level="l1", objective="第二个 run")
rid2 = r4.get("run_id")
m1 = tool("memory_commit", run_id=rid2)
check("未批准的 run 禁止写记忆", "error" in m1, m1)
m2 = tool("memory_commit", run_id=rid)
check("已批准的 run 可以写记忆", m2.get("committed") is True, m2)
check("记忆写到了真实后端", (m2.get("memory") or {}).get("backend") in ("local","openviking"), m2)
rec = tool("memory_recall", query="score")
check("召回能查到刚写的记忆", bool(rec.get("hits")), rec)

print("== 审计链 ==")
av = tool("audit_verify")
check("哈希链完整", av.get("ok") is True and av.get("events", 0) >= 4, av)

print("== 门禁视图（已接 agent-quality-security）==")
g = tool("gate_status", run_id=rid)
by_id = {x["id"]: x["status"] for x in g.get("gates", [])}
check("返回十道门禁", len(g.get("gates", [])) == 10, g.get("gates"))
check("unit-tests 通过", by_id.get("unit-tests") == "passed", by_id)
check("业务结果门禁通过", by_id.get("business-outcome-and-safety-demo") == "passed", by_id)
check("门禁全部通过（十道真实报告）", g["summary"]["failed"] == 0 and g["summary"]["unavailable"] == 0, g.get("summary"))
check("多数门禁带证据哈希（纯命令门禁除外）", sum(1 for x in g["gates"] if x.get("evidence_sha256")) >= 8, [x["id"] for x in g["gates"] if not x.get("evidence_sha256")])

print("== engine 参数 ==")
bad = tool("evolve_run", level="l1", engine="nope")
check("未知引擎被拒", "error" in bad, bad)
real = tool("evolve_run", level="l1", engine="dsh")
ev = real.get("evaluation") or {}
detail = str(((ev.get("failures") or [{}])[0]).get("detail", ""))
check("缺 key 时真引擎 fail-closed", ev.get("passed") is False and "DEEPSEEK_API_KEY" in detail, real)

print("== 项目隔离 ==")
pa = tool("evolve_run", level="l1", project="alpha", objective="alpha 项目")
pb = tool("evolve_run", level="l1", project="beta", objective="beta 项目")
check("两个项目各自起 run", pa.get("project") == "alpha" and pb.get("project") == "beta", (pa, pb))
tool("approve_release", run_id=pa["run_id"], actor="tester", reason="alpha 通过", acknowledge_unavailable=True)
mc = tool("memory_commit", run_id=pa["run_id"])
check("alpha 记忆写入成功", mc.get("committed") is True, mc)
ra = tool("memory_recall", project="alpha", query="alpha")
rb = tool("memory_recall", project="beta", query="alpha")
check("alpha 能召回自己的记忆", bool(ra.get("hits")), ra)
check("beta 看不到 alpha 的记忆", not rb.get("hits"), rb)
sa = tool("evolve_status", project="alpha")
sb = tool("evolve_status", project="beta")
check("latest 按项目定位", sa.get("run_id") == pa["run_id"] and sb.get("run_id") == pb["run_id"], (sa, sb))

proc.stdin.close()
proc.wait(timeout=5)

print()
if fail:
    print(f"FAILED: {len(fail)} 项 -> {fail}")
    sys.exit(1)
print("ALL PASS")
