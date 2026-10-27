# evolution-stack · 四系统整合

四个项目组装成的**受治理 RSI 闭环**，以 DSH 为执行器。

```
DSH（执行器/底盘）
  └─ control/                MCP 控制面（唯一入口，8 个工具）
       ├─ vendor/openviking  记忆层
       ├─ vendor/harness2    进化算法（PPC）
       ├─ local/agent-evolution-platform   治理层
       └─ local/agent-quality-security     裁判层
```

## 克隆

```powershell
git clone --recursive git@github.com:nanazi-0/evolution-stack.git
```

四个组件是 **submodule**，不是复制进来的代码：

- `local/*` → 你自己的仓库（`nanazi-0/agent-evolution-platform`、`nanazi-0/agent-quality-security`）
- `vendor/*` → 上游项目引用（`volcengine/OpenViking` 111 MB AGPLv3、`google-research/harness2` Apache-2.0）

只想要父仓库不拉大件：

```powershell
git clone git@github.com:nanazi-0/evolution-stack.git
git submodule update --init local/agent-evolution-platform local/agent-quality-security
```

更新上游到最新：

```powershell
git submodule update --remote vendor/openviking vendor/harness2
```

## 目录

| 路径 | 内容 | 来源 |
|---|---|---|
| `vendor/openviking/` | 上下文数据库 | `git clone` volcengine/OpenViking |
| `vendor/harness2/` | PPC 递归进化框架 | `git clone` google-research/harness2 |
| `local/agent-evolution-platform/` | 治理层（可变区/留出集/审批/审计） | `git clone` 本地裸仓库 `git-remote/*.git` |
| `local/agent-quality-security/` | 裁判层（十道门禁） | 同上 |
| `control/` | MCP server + 冒烟测试 | 本项目 |
| `vendor/LOCK.json` | 四个组件的来源、commit、指纹 | 生成 |

`local/` 里的两个项目已排除 `.venv node_modules .runtime reports data`，需要时按各自 `scripts/setup.ps1` 重建环境。

**来源说明**：四个组件**全部来自远端仓库**，工作副本都是 `git clone` 出来的：

| 组件 | 远端 |
|---|---|
| openviking | `github.com/volcengine/OpenViking` |
| harness2 | `github.com/google-research/harness2` |
| agent-evolution-platform | `github.com/nanazi-0/agent-evolution-platform` |
| agent-quality-security | `github.com/nanazi-0/agent-quality-security` |

后两个是本项目自建的，已上传到 GitHub，commit 与指纹记在 `vendor/LOCK.json`。
推送用 SSH 密钥 `control/.ssh/id_ed25519`（已装到 GitHub 账号），不需要 token。

### 同步与发布

改了 `local/` 里的代码：

```powershell
cd D:\start\ds-harness\evolution-stack\local\agent-evolution-platform
git add -A; git commit -m "feat: ..."; git push origin main
```

拉取远端最新（模拟"从仓库重新拉源码"）：

```powershell
git -C <仓库目录> fetch origin; git -C <仓库目录> reset --hard origin/main
```

**以后能上 GitHub 时**，只需给现有仓库加一个远端再推，不用重做：

```powershell
git -C local\agent-evolution-platform remote add github git@github.com:<你>/agent-evolution-platform.git
git -C local\agent-evolution-platform push -u github main
```

> 注意：`core.autocrlf` 必须为 `false`，否则 Windows 会把 LF 转成 CRLF，导致内容指纹对不上。
> `agent-quality-security` 另有一份 `.gitattributes` 保住 egg-info 的原始换行。

## 已实现 / 未接线

| 能力 | 状态 |
|---|---|
| MCP stdio 协议 | ✅ 已实现（8 个工具） |
| ADR-005 预算分级 | ✅ 生效：l1 拒绝多代，未知级别拒绝 |
| ADR-002 具名批准 | ✅ 生效：缺 actor/reason 直接拒绝 |
| ADR-004 记忆只收 approved | ✅ 生效：未批准的 run 写记忆会被拒 |
| SHA-256 审计链 | ✅ 生效：`audit_verify` 可校验 |
| 十道门禁 | ⚠️ 占位：返回 10 项，全部 skipped |
| 候选生成（PPC） | ⚠️ 占位：`evolve_run` 只建 run 记录 |
| OpenViking 读写 | ⚠️ 占位：`memory_recall` 返回空 |
| DSH 执行器适配器 | ❌ 未写（ADR-006 的唯一允许执行器） |

**当前状态 = 治理骨架可跑，真实子系统未接线。** 这不是"完成品"，但所有安全规则已经在代码里，接功能不会绕过它们。

## 跑起来

```powershell
# 冒烟测试（验证协议 + 四条 ADR 规则）
D:\start\ds-harness\work\.tools\venv\Scripts\python.exe control\_smoke.py
```

在 DSH 里使用：`~/.dsh/profiles/desktop/cordis.patch.yml` 已注册 MCP 条目（`mcp-evolution-control`），
重启桌面端后对话里会出现 `mcp__evolution__*` 工具。

## 两个必须知道的环境约束

**1. 状态目录必须在工作区内。**
DSH 沙箱只允许子进程写 `D:\start\ds-harness\work`。`evolution-stack` 在工作区外，
所以运行状态放 `work/evolution-state/`（可用 `EVOLUTION_STATE_DIR` 覆盖）。
代码放哪都行，**可写目录只能是工作区**。

**2. Windows 下必须显式 UTF-8。**
`server.py` 里 already `reconfigure(encoding="utf-8")`；自己写脚本调它时也建议
`PYTHONIOENCODING=utf-8`，否则中文 JSON 会乱码。

## 下一步

1. 写 **DSH 执行器适配器**（`control/eval/runner.py`）：调 `dsh --profile headless` 跑候选，输出评估信封。
2. 接 **agent-quality-security**：把十道门禁的真实结果灌进 `gate_status`。
3. 接 **OpenViking**：`memory_recall` / `memory_commit` 走它的 HTTP API。
4. 最后才把 **Harness² 的 PPC** 接成 proposer（只借算法，禁用其 Codex substrate）。

顺序不能反：**先有真值，再谈进化**。否则会拿占位分数训练出占位经验，写进记忆后再也清不掉。
