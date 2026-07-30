<div align="center">

# Ycode

**一个面向本地代码仓库的 Coding Agent Runtime**

Ycode 将模型调用、工具执行、上下文管理、任务状态、运行证据和评测流程收敛到一套本地 runtime 中，用来探索长链路代码任务里的可恢复、可审计和可评测问题。

</div>

## 项目定位

Ycode 不是一个简单的聊天壳，而是一个本地代码智能体 harness。它关注的是：模型在多轮任务中如何稳定读代码、调用工具、更新状态、恢复上下文，并把执行过程沉淀成可以复盘的证据。

一次任务运行会被拆成几个核心部分：

| 模块 | 作用 |
| --- | --- |
| Provider | 统一接入 OpenAI-compatible / Anthropic-compatible 模型后端 |
| Context | 组装系统提示、仓库信息、会话历史、工作记忆、长期记忆和当前请求 |
| Tools | 提供读文件、搜索、执行命令、写文件、打补丁、子 Agent、Todo 等工具 |
| Runtime State | 维护 TaskState、Run ID、会话事件、checkpoint 和停止原因 |
| Memory | 保存任务摘要、文件摘要、过程笔记和可召回的长期记忆 |
| Evaluation | 固定任务集、对照实验、运行 trace 和 verifier 结果 |

## 核心能力

- **Agent Runtime**：围绕“模型决策 -> 工具执行 -> 状态更新”构建持续执行循环，支持正常完成、格式重试、预算耗尽、模型异常等运行路径。
- **Tool Calling**：工具调用走统一入口，集中处理参数校验、工作区限制、高风险审批、重复调用拦截和结果落盘。
- **Context Management**：按稳定信息、当前状态、相关记忆、历史消息和当前请求分层组装上下文，避免长任务里 prompt 无限制膨胀。
- **Checkpoint / Resume**：任务中断后可以根据 checkpoint 恢复，同时识别 workspace 是否发生漂移，避免误信旧状态继续执行。
- **Layered Memory**：把短期工作记忆、daily log、durable topics 和文件摘要拆开管理，减少 follow-up 阶段重复读文件。
- **Run Trace & Evaluation**：为每次运行保存状态快照、事件流、trace 和报告，并用固定 benchmark 验证上下文、记忆、恢复和工具安全。

## 安装

要求：Python 3.10+，以及至少一个可用的模型 provider key。

```bash
git clone https://github.com/liyimil/Ycode.git
cd Ycode
pip install -e ".[dev]"
```

当前 Python 包和命令行入口仍保留为 `pico`。这是实现层命名，不影响项目对外名称 `Ycode`。

```bash
pico --help
```

## 配置模型

复制配置模板：

```bash
cp .pico.toml.example .pico.toml
```

最小配置示例：

```toml
provider = "openai"

[providers.openai]
protocol = "openai"
api_key = "sk-..."
base_url = "https://api.openai.com/v1"
model = "gpt-5.4"
```

`.pico.toml` 默认会被 `.gitignore` 忽略，不要把真实 key 提交进 Git。

也可以使用环境变量：

```bash
export PICO_PROVIDER=openai
export PICO_API_KEY=sk-...
export PICO_BASE_URL=https://api.openai.com/v1
export PICO_MODEL=gpt-5.4
```

配置优先级：

```text
CLI 参数 > 环境变量 > 项目 .pico.toml > 全局 ~/.config/pico/config.toml > 默认值
```

更完整的配置说明见 [docs/configuration.md](docs/configuration.md)。

## 使用方式

```bash
pico                              # 启动 Textual TUI
pico --repl                       # 普通终端 REPL
pico "找出测试失败的根因"          # one-shot 任务
pico --resume latest              # 续接最近 session
pico --cwd /path/to/repo          # 指定工作目录
```

常用运行参数：

```bash
pico --approval ask               # shell / 写文件前询问
pico --approval auto              # 普通操作自动通过
pico --approval never             # 非交互模式
pico --sandbox best_effort        # 尽量隔离 shell 命令
pico --no-auto-dream              # 关闭后台 memory 整合
```

进入 TUI 或 REPL 后，可以使用自然语言，也可以使用 slash command：

```text
> /help
> /skills
> /plan 重构 provider 配置加载逻辑
> /review
> /test tests/test_config.py
> /remember 这个仓库的 provider 配置走 OpenAI-compatible endpoint
> /dream
```

## 本地运行文件

| 数据 | 路径 |
| --- | --- |
| 项目配置 | `.pico.toml` |
| 全局配置 | `~/.config/pico/config.toml` |
| 会话历史 | `.pico/sessions/<id>.json` |
| 事件流 | `.pico/sessions/<id>.events.jsonl` |
| 运行证据 | `.pico/runs/<run_id>/` |
| 记忆索引 | `.pico/memory/MEMORY.md` |
| Daily logs | `.pico/memory/logs/YYYY/MM/YYYY-MM-DD.md` |
| Durable topics | `.pico/memory/topics/*.md` |

这些运行文件都属于本地状态，默认不会上传到仓库。

## 项目结构

```text
pico/
├── cli.py                 # CLI 参数、启动模式、REPL 命令
├── config/                # provider profile、TOML、env 解析
├── core/                  # runtime、engine、session、workers、context
├── features/              # memory、skills、sandbox
├── providers/             # OpenAI-compatible / Anthropic-compatible client
├── tools/                 # tool registry 和具体工具
├── tui/                   # Textual TUI
└── evaluation/            # run evidence、metrics、evaluation helpers
```

## 评测

运行单元测试：

```bash
pytest tests/ -q
```

运行上下文与记忆对照实验：

```bash
python scripts/run_context_memory_benchmark.py --mode deterministic
```

如果配置了真实模型，也可以运行 live benchmark：

```bash
python scripts/run_context_memory_benchmark.py \
  --mode live \
  --model mimo-v2.5-pro \
  --base-url https://api.xiaomimimo.com/v1 \
  --api-key-env MIMO_API_KEY
```

实验会对比 `full`、`no_context_reduction`、`no_memory` 三组配置，并输出 token 使用、重复读文件次数、工具步数和 verifier 结果。

## 文档

| 文档 | 内容 |
| --- | --- |
| [配置](docs/configuration.md) | provider profile、`.pico.toml`、环境变量和 sandbox 配置 |
| [记忆](docs/memory.md) | working memory、daily logs、durable topics 和 auto-dream |
| [Skills](docs/skills.md) | `SKILL.md` 目录结构、内置技能和自定义 workflow |
| [Sandbox](docs/sandbox.md) | `run_shell` 隔离模式、backend 选择和文件系统边界 |

## License

MIT
