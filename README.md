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
| ADR-002 具名批准 | ✅ 生效：缺 actor/reason 拒绝；门禁失败拒绝；缺测门禁需显式确认 |
| ADR-004 记忆只收 approved | ✅ 生效：未批准的 run 写记忆会被拒 |
| SHA-256 审计链 | ✅ 生效：`audit_verify` 可校验 |
| 十道门禁 | ✅ **10/10 全通过**（真 pipeline + 证据哈希，见 gate-report.json）|
| 候选生成（PPC） | ⚠️ 占位：`evolve_run` 只建 run 记录 |
| 记忆读写 | ✅ 已接适配器：local 后端可用；`MEMORY_BACKEND=openviking` + `OPENVIKING_URL` 即切真服务 |
| DSH 执行器适配器 | ✅ 已实现 `control/eval/runner.py`：断言驱动打分，echo 引擎可直接跑 |

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

1. ~~写 DSH 执行器适配器~~ ✅ 已完成（`control/eval/runner.py`）；下一步是把它从 echo 引擎切到真 DSH。
2. 接 **agent-quality-security**：把十道门禁的真实结果灌进 `gate_status`。
3. 接 **OpenViking**：`memory_recall` / `memory_commit` 走它的 HTTP API。
4. 最后才把 **Harness² 的 PPC** 接成 proposer（只借算法，禁用其 Codex substrate）。

顺序不能反：**先有真值，再谈进化**。否则会拿占位分数训练出占位经验，写进记忆后再也清不掉。

### 真引擎的运行前提（实测）

`npx @deepseek-ai/dsh --profile headless` 能用，但有两个坑：

1. **桌面端的账号登录不能跨进程复用**
   直接跑会报 `ACCOUNT_SIGN_IN_REQUIRED: The request destination must allow account authentication`。
   桌面端用的是 `deepseek-account`（账号授权），headless 进程拿不到。
2. **默认路由需要 `DEEPSEEK_API_KEY`**
   否则报 `MISSING_CREDENTIAL: llm-deepseek: no API key for provider route "deepseek-official"`。

所以切真引擎需要一条 API key：

```powershell
$env:DEEPSEEK_API_KEY = 'sk-...'
$env:EVOLUTION_ENGINE = 'dsh'
```

若用其他兼容端点，再配 `EVOLUTION_PATCH` 指向一份改了 provider 的 patch 文件
（参考 `control/eval/headless-model.patch.yml`）。

`EVOLUTION_ENGINE=echo` 时不消耗任何模型额度，适合验证管道。
### 评测用独立 DSH_HOME（实测必需）

runner 会把 `DSH_HOME` 指向 `work/_eval-home`，原因有两条：

1. 沙箱只允许子进程写工作区，写 `data/home` 会 `EPERM`；
2. 评测不该碰生产 profile —— 独立的 home 让评测完全隔离。

`EVOLUTION_DSH_HOME` 可覆盖该路径。

### 真引擎实测数据（2026-10-27）

3 条用例、`deepseek-flash`：**score=0.3333（1/3 通过）**，p95 延迟 **83 秒/条**。

延迟主要来自每条用例都要起一个 `npx dsh` 进程。批量评测时这个成本要算进去。
### 门禁接了哪些（2026-10-27 实测）

本机有 Docker CLI 但 daemon 未运行，k6/semgrep/trivy 未安装，所以：

| 门禁 | 状态 |
|---|---|
| unit-tests | ✅ 真跑（9 项通过） |
| business-outcome | ✅ 真跑（4/4 通过） |
| 其余 8 道 | ⚠️ unavailable（缺 docker / k6 / semgrep / trivy / zap） |

`approve_release` 现在会先跑门禁：有 failed 直接拒绝；有 unavailable 时必须显式传
`acknowledge_unavailable=true` 并把原因写进 reason，否则拒绝。这条会写进审计链。

项目自己的输出目录（`.runtime` `reports` `data` `test-results`）已用目录联接重定向到
`work/_qs-store/`，因为沙箱不允许子进程写工作区外。
### 记忆适配器（2026-10-27）

`control/adapters/memory.py` 两个后端：

| 后端 | 触发条件 | 状态 |
|---|---|---|
| `local` | 默认（未配 `OPENVIKING_URL`） | ✅ 已验证：commit → recall 闭环 |
| `openviking` | 配了 `OPENVIKING_URL`，或 `MEMORY_BACKEND=openviking` | ⚠️ 按官方 SDK 的 `/api/v1/resources`、`/api/v1/search/search` 实现，**未起服务无法验证** |

local 后端写 `work/evolution-state/memory.jsonl`，一行一条，可直接 grep。
召回是朴素子串匹配——够用、可解释，但不是语义检索；要语义就切 OpenViking。
### 与 D:\Portfolio 的隔离（2026-10-27 核实）

结论：**不会污染**。依据：

1. `D:\Portfolio` 下今天没有任何文件被修改；
2. `evolution-stack` 里四个目录联接全部指向 `work/_qs-store`，没有指向 Portfolio；
3. 项目输出目录（`.runtime` `reports` `data` `test-results`）都在工作区。

但发现过一处遗留指向并已修复：

```
configs/demo.toml / llm-example.toml
  root = "D:/Portfolio/agent-quality-security"   ← 会让 evolutionctl 在 Portfolio 里跑并写 reports/data
改成
  root = "D:/start/ds-harness/evolution-stack/local/agent-quality-security"
```

同时给 `quality.py` 加了 venv 缺失时回退到当前解释器的逻辑（复制时排除了 `.venv`）。

另：沙箱下系统 Temp 不可写，所有子进程都注入 `TMP/TEMP=work/_tmp`，否则 `tempfile` 会报
"No usable temporary directory found"。
### 十道门禁全部跑通（2026-10-27 实测）

```
[PASS] unit-tests            [PASS] web-ui-accessibility
[PASS] business-outcome      [PASS] performance
[PASS] telemetry             [PASS] source-security
[PASS] phoenix-trace-ingest  [PASS] dependency-secret
[PASS] agent-security        [PASS] web-dynamic-security
QUALITY_GATE=PASS
```

关键前提（每条都是踩出来的）：

1. **必须用系统 Python `D:\cpython3.14\python.exe`**。工作区 venv 的 python.exe 带沙箱能力标记，
   从它启动的进程开不了 docker 命名管道、也写不了项目目录（`PermissionError`）。
2. 子进程注入 `TMP/TEMP=work/_tmp`，否则 tempfile 报"No usable temporary directory"。
3. `PYTHONPATH=<quality-security>/src`，因为 `scripts/*` 直接 import `agent_quality`。
4. Playwright 需要 `chromium-headless-shell`（不只是 chromium），且 CDN 在国内拉不动，
   用 `PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright`。
5. Phoenix 走 `PHOENIX_HOST`，浏览器走 `PLAYWRIGHT_BROWSERS_PATH`。

**不要再用目录联接重定向 `reports/data/.runtime`** —— 容器内看到联接会报 `ENOTDIR`。

### 项目隔离（2026-10-27）

记忆、运行状态、候选目录现在都带 `project` 维度：

| 项 | 隔离方式 |
|---|---|
| 记忆 | 每条记录带 `project`；recall 先按项目过滤；OpenViking URI 为 `viking://user/memories/evolution/<project>/<run_id>` |
| 运行状态 | run 带 `project`；`latest` 只在同项目内取 |
| 候选目录 | `targets/<project>/baseline`，不存在时回退到默认基线 |

调用侧：`evolve_run(project="alpha", ...)`、`memory_recall(project="alpha", ...)`。
测试里验证过：alpha 写的记忆，beta 查不到。

### 与 Docker Desktop 共享的边界

* 镜像按 digest 钉死，与其他项目**共享同一份层**，不重复占盘、不冲突。
* 容器都是 `docker run --rm`，无 `--name`、无 `-p`，不会和别的项目撞名撞端口。
* **唯一需要协调的是长期服务容器**：`observability-phoenix-1` 固定占用 4317/6006。
  如果 Portfolio 也要起 Phoenix，两边会抢端口。到时候用独立 compose 项目名 + 改端口：
  `docker compose -p evolution-stack up -d`，并用 `PHOENIX_HOST` 指到新端口。
### 完整闭环已跑通（2026-10-28）

```
evolve_run(project, candidates=k, propose_mode, engine)
  ├─ 评估基线            → baseline_score
  ├─ 提议 k 个变体        → proposer.py（echo 确定性 / llm 调 DeepSeek）
  ├─ 逐个探测             → 每个变体跑同一套用例
  ├─ 选冠军               → champion（含 delta）
  └─ status = eligible    ← ADR-002：绝不自动晋升

gate_status / gate_run    → 十道门禁（后台跑，不阻塞）
approve_release(actor)    → 门禁全过 + 具名 → 固化冠军到 releases/<run_id>
                            + 更新 targets/<project>/current.json
memory_commit             → 仅 approved 才写
audit_verify              → SHA-256 链
```

实测（echo 引擎，零成本）：

| 步骤 | 结果 |
|---|---|
| 基线 | score 0.0 |
| v1（加 SOURCE 标记） | **1.0，delta +1.0** |
| v2（另一处改动） | 0.0 |
| champion | v1 |
| approve 后 | `releases/<run_id>` + `current.json` 落盘 |

关键设计：`gate_status` 不再阻塞——门禁报告有缓存（15 分钟），
重跑走 `gate_run` 后台任务，用 `gate_status` 轮询。之前同步跑会 4-5 分钟超时。

### 候选与发布目录

```
targets/<project>/
├─ baseline/            基线（只读，不由进化改写）
├─ releases/<run_id>/   已批准的冠军快照
└─ current.json        当前发布的指针（含 previous，可回滚）
```
### 晋升的最后一道门（ADR-002）

冠军必须相对基线有实质改进（默认 delta ≥ 0.01），否则 `approve_release` 直接拒绝：

```
有改进  -> approved，固化到 releases/<run_id>
delta=0 -> "ADR-002：冠军 v1 相对基线没有实质改进（delta=0.0），拒绝晋升"
```

这条堵的是"批准一个没变好的版本"。实测两种情形都验证过。
### 评测用例的优先级

```
evolve_run(cases="...")            优先级最高
targets/<project>/cases.json       项目自带
EVOLUTION_CASES 环境变量
control/eval/cases/smoke.json      默认
```

每个项目可以有自己的用例集，互不影响。验收一条命令：

```
evolve_run(project="x", candidates=1, engine="echo", cases=".../cases/ppc.json")
  -> baseline 0.0 / champion delta +1.0 -> approve -> releases/<run_id>
```
### 依赖的两个本地服务（2026-10-28 打通）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File control\start-services.ps1
```

| 服务 | 端口 | 作用 |
|---|---|---|
| embedding（`control/adapters/embeddings_server.py`） | 18080 | OpenAI 兼容；BAAI/bge-small-zh-v1.5，512 维 |
| OpenViking（容器 `ov-evolution`） | 1933 | 记忆库，api_key 认证 |

为什么自己起 embedding：**OpenViking 必须要 embedding API，而 DeepSeek 只有对话模型**（`deepseek-flash`、`deepseek-v4-pro`）。
所以用 fastembed 在本地起一个，`api_base=http://host.docker.internal:18080/v1`。

启动 OpenViking 的三个坑：

1. `OPENVIKING_WITH_BOT=0` —— 默认会拉 VikingBot，它起不来会拖垮整个 entrypoint。
2. `server.host` 必须是 `0.0.0.0`，但 **dev 模式不允许**，所以要 `auth_mode=api_key` + `root_api_key`。
3. 配置文件**不能带 BOM**，否则报 "expected value at line 1 column 1"。

认证要点：请求头是 **`X-API-Key`**（不是 Bearer）。ROOT key 只能管账号，
数据读写要用 Admin API 创建的 **user key**；URI 用 `viking://~/memories/...`（`~` 展开成 `viking://user/<user>/`）。

实测：写入后按语义检索能召回（相似度 0.83），且**项目隔离生效**——
`other` 项目能查到自己的记忆，`evo` 查不到。

### Phoenix 端口隔离（2026-10-28）

原来的 `observability-phoenix-1` 固定占 4317/6006，会和 `D:\Portfolio` 里的项目抢端口。
现在改成独立 compose 项目 + 非默认端口：

```powershell
docker compose -p evolution-stack -f control\compose\phoenix.yml up -d
```

| | 旧 | 新 |
|---|---|---|
| 容器名 | `observability-phoenix-1` | `evolution-stack-phoenix-1` |
| UI/HTTP | 6006 | **16006** |
| OTLP | 4317 | **14317** |
| 环境变量 | — | `PHOENIX_HOST=http://127.0.0.1:16006` |

`Portfolio` 用它的 6006/4317，互不干扰。telemetry 与 phoenix 两道门禁已在 16006 上验证通过。
### 服务启停参数（control\start-services.ps1）

```powershell
.\start-services.ps1                    # 补齐缺的；已在跑的不动
.\start-services.ps1 -Status            # 只看状态
.\start-services.ps1 -Restart           # 两个都强制重启
.\start-services.ps1 -RestartEmbedding  # 只重启 embedding
.\start-services.ps1 -SkipEmbedding     # 只处理 OpenViking
```

配置在 `control\services.toml`：

```toml
[embedding]
port = 18080
model = "BAAI/bge-small-zh-v1.5"
autostart = true        # false = 完全手动
restart_always = false  # true = 每次都重启

[openviking]
port = 1933
container = "ov-evolution"
autostart = true
restart_always = false
```

**默认不重启已在运行的服务**（避免打断已建好的向量索引），需要时才加 `-Restart*`。
注意：在捕获输出的环境（IDE、脚本管道）里启动长驻进程会让父进程一直等，
所以这个脚本适合在**普通终端**里跑。
### 隔离自检（可复跑）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File control\check-isolation.ps1
```

输出 `ISOLATION=PASS` 表示与 `D:\Portfolio` 之间**没有共享的可变状态**。六项检查：

| # | 检查 | 结果 |
|---|---|---|
| 1 | 代码里没有指向 Portfolio 的可执行路径 | PASS（只剩注释与文档提及） |
| 2 | 没有联接/软链指向外部 | PASS（四个旧联接已全部移除） |
| 3 | Portfolio 里没有反向引用我们 | PASS |
| 4 | 我们的端口 1933/14317/16006/18080 在监听，6006/4317 留空 | PASS |
| 5 | 容器只挂载 `D:\start\ds-harness\*`；卷按 compose 项目名隔离 | PASS |
| 6 | 五个镜像全部按 `@sha256:` 引用 | PASS |

**共享镜像不等于会冲突**：镜像层是只读的，共享是设计上的节省；
每个容器有独立的可写层。真正会冲突的是四种：写死的路径、目录联接、
固定端口、全局命名卷 —— 这四样现在都处理掉了。

| 仍然共享的 | 性质 | 处理 |
|---|---|---|
| 镜像层 | 只读 | 无需处理，同 digest 只存一份 |
| CPU / 内存 | 运行时竞争 | 两边不同时跑重负载 |
| `docker system prune -a` | 会删掉钉死的镜像 | 删了重拉（`--pull=missing` 兜底） |

端口分工（记在 `control\compose\phoenix.yml` 与 `services.toml`）：

```
ours : 1933 OpenViking / 18080 embedding / 16006 Phoenix UI / 14317 OTLP
yours: 6006 / 4317 留空给 Portfolio
```
### 回滚（2026-10-28 修）

之前 `rollback_release` 只把 run 状态改成 `rolled_back`，**没有动 `current.json`** —— 是假回滚。
现在会真正把发布指针移回上一版：

```
current.json: {run_id: r2, previous: r1}
rollback -> {from: r2, to: r1, path: releases/r1}   指针真的回去了
rollback -> {"error": "没有上一版可回滚（previous 为空）"}   单向，不能靠回滚"前进"
```

回滚后 `previous` 置空，想前进必须重新批准一个 run —— 防止把旧版"回滚"成新版。