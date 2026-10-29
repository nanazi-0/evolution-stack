# 工作流层（声明式链路）

加一条业务链路 = **写一个 TOML**，不改代码。

```
control/workflow/
├─ runner.py            通用执行器（脚本 / LLM / 人工三类步骤）
├─ models.toml          模型注册表（能力 → 具体模型）
├─ lanes/
│  ├─ clip-refresh.toml     刷新商品池 → 打分 → 人工复核
│  └─ clip-analysis.toml    打分 → 模型给建议 → 人工复核
└─ README.md            本文件
```

## 快速开始

```powershell
cd control\workflow

python runner.py --list                          # 列出所有链路
python runner.py --lane lanes\clip-refresh.toml --dry-run   # 看步骤，不执行
python runner.py --lane lanes\clip-refresh.toml             # 真跑
python runner.py --lane lanes\clip-refresh.toml --approve <run_id>   # 人工步骤放行
python runner.py --lane lanes\clip-refresh.toml --resume <run_id>    # 断点续跑
```

运行状态：`work\_workflows\<run_id>\state.json`

## 链路定义

```toml
[lane]
name = "clip-refresh"
project = "clip"          # 对应项目隔离
budget_yuan = 2.0         # LLM 步骤累计预算，超了直接中止
description = "..."

[[step]]
id = "rebuild_pool"
kind = "script"
run = "adapters/rebuild_pool.py"    # 相对 control/ 的路径
args = ["--flag"]
timeout_s = 300
retries = 1

[[step]]
id = "advice"
kind = "llm"
requires = ["text.reason"]          # 只要能力，不写死模型
model = "glm-4.6"                   # 可选：显式指定
system = "..."
prompt = "..."
input_file = "../local/.../clip_top.csv"   # 可选：把文件内容注入提示词
input_max_lines = 11                        # 可选：只取前 N 行
timeout_s = 120

[[step]]
id = "review"
kind = "human"
prompt = "看生成结果，确认是否合理"
```

## 三类步骤

| kind | 做什么 | 失败行为 |
|---|---|---|
| `script` | 跑确定性脚本，带超时与重试 | 重试后仍失败 → run 中止 |
| `llm` | 按 `requires` 解析模型并调用，累计预算 | 缺 key / 预算超 / 模型未核实 → **fail-closed** |
| `human` | 暂停，状态置 `awaiting_human` | 等人 `--approve`，不算失败 |

## 模型路由规则（models.toml）

1. `requires` 是**能力**（`text.reason` / `text.fast` / `json.mode` / `embedding`），不是模型名
2. **`verified_at` 为空的模型不会被选用**——型号和价格没核实过，不能拿来算预算
3. 没写 `cost_in_per_1k` / `cost_out_per_1k` 的模型：能调用，但**不计费**（`cost_yuan` 为 null）

> 当前 `deepseek-flash` 已验证可用但未配置价格，所以预算对它不生效。
> 要真正卡预算，先把价格核实并填进 `models.toml`。

## 已验证的能力（2026-10-29）

```
clip-refresh    3 步：脚本 10.0s + 0.2s，人工暂停 → approve 后续跑完成 ✅
clip-analysis   3 步：脚本 0.2s + deepseek-flash 39.3s（127→1114 tokens）
                输出 854 字，基于 CSV 真实数据点名 3 个商品并给依据 ✅
```

## 与 DSH 的分工

| 层 | 负责 | 位置 |
|---|---|---|
| 流程定义 | 谁调用谁、用什么能力、预算多少 | `lanes/*.toml` |
| 执行 | 重试、超时、预算、状态持久化、断点 | `runner.py` |
| 节点能力 | 需要判断的交给模型；确定性的交给脚本 | `models.toml` + `adapters/` |
| 长期运行 | 定时触发 | Windows 计划任务 |

---

## 智谱 GLM 接入（2026-10-29 验证）

**调用方式**：不需要装官方 SDK，直接走 OpenAI 兼容 HTTP：

```
POST https://open.bigmodel.cn/api/paas/v4/chat/completions
Authorization: Bearer $env:ZHIPU_API_KEY
```

**Key 配置**：`ZHIPU_API_KEY`（用户级环境变量，runner 按 `key_env` 读取）

**已验证的模型**（实测调用成功）：

| 模型 | 状态 | 价格（元/百万 tokens） |
|---|---|---|
| `glm-5.3` | ✅ 旗舰，思考模式 | 输入 8 / 输出 28（已填入注册表，可计费） |
| `glm-4.6` | ✅ 可用 | 价格页未列，未填 |
| `glm-4-flash` | ✅ 可用 | 未核实 |
| `glm-4.7-flash` | ⚠️ 免费但常返回 `1305 访问量过大` | 0 |

**实测账目**（clip-analysis 链路，注入 10 行 CSV）：

```
model     = glm-5.3
tokens    = 11079
cost_yuan = 0.2858      ← 按 8/28 元每百万算出来的
chars     = 2063
耗时      = 110.5s
```

## 踩到的两个坑

1. **TOML 的 `.` 是嵌套分隔符**：`[models.glm-5.3]` 会被解析成 `models.glm-5."3"`。
   必须写成 `[models."glm-5.3"]`。
2. **思考模型很慢**：`glm-5.3` 不带 `max_tokens` 时会超过 120 秒超时。
   链路里要显式写 `max_tokens`（示例用 900）和 `timeout_s = 300`。