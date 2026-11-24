# Gemini 主控的多模型协作框架

在 Antigravity 里和 **Gemini Neon** 对话，让它按需要把独立设计交给 **Gemini Carbon**、把实现交给你在 **Codex** 中登录的 GPT、把关键改动交给 Antigravity 自带的 **Claude** 复核。你在同一个主会话里提需求、追问和看验收结果。

Neon 负责理解任务。本地 Python 分流脚本根据任务类型、难度和可用额度池选出一个执行者，日常请求优先用 **Medium**。例如你请它核对一个误差计算脚本，它可以直接读代码。只有在需要独立验算时才派一位审查者，并把对方的答案与实际运行结果核对。

## 先试一下，不消耗模型额度

克隆仓库或解压源码，在包含 `router.py` 的目录中运行以下命令。只需 **Python 3.10+** 和标准库。Windows 可用 `py -3`，macOS 或 Linux 可用 `python3` 代替 `python`。

```sh
python router.py implement
python router.py review --avoid-pool claude
python examples/workspace/check_fixture.py
```

按仓库默认配置，前两条命令分别预览 GPT 的常规实现路线和 Claude 额度紧张时的只读审查路线。输出中的 `status` 为 `preview`，`effort` 为 `medium`。最后一条用两个固定样本计算定位误差，预期输出 `RMSE = 3.536`。这些命令均可在未登录模型账号时运行。

## 模型怎样分工

| 用户要完成的事 | 常规路线 | 只在确有难点时升级 |
| --- | --- | --- |
| 日常问答、解释日志、小改动 `chat` | Neon Medium 直接处理 | Neon xhigh |
| 需要第二份独立方案 `design` | Carbon Medium，仅接收原始问题、约束和证据 | Carbon High |
| 接口和验收标准明确的实现 `implement` | Codex · GPT-6.2 Sol Medium | GPT-6.2 Astra，本机核实的最高档 |
| 独立检查代码、实验口径或关键论断 `review` | Antigravity · Claude Sonnet 5.5，优先 Medium | Claude Opus 5.5，该账号提供的最高档 |

这是仓库的起始配置。用 `--hard-reason "具体难点"` 或 `--failed-check "失败的验收项和实际现象"` 说明单个子任务为什么需要升级。按项目规则，每个用户任务最多委派两次专家调用，其中最高档至多一次。该预算由 Neon 在会话中执行。一次委派所含的多轮推理及工具调用都可能计入实际用量。[主会话规则](./GEMINI.md) · [默认模型配置](./router.example.json)

`--avoid-pool gemini`、`--avoid-pool claude`、`--avoid-pool codex` 可以重复指定。前者同时避开 Neon 和 Carbon 专家路线。`claude` 指 Google 端的相应额度组，`codex` 指你在 OpenAI 端使用的额度。池的紧张程度由使用者按官方用量面板判断，脚本根据传入的排除项改选或报告没有可用路线。[Antigravity 用量面板](https://antigravity.google/docs/cli/commands/usage)

## 接入真实账号与项目

1. **检查本机环境。** 运行 `python setup_router.py doctor`。使用 GPT 需要 Codex CLI 和 ChatGPT 登录。通过脚本使用 Carbon 或 Claude 需要官方 `agy`、有资格的个人 Google 账号，以及账号模型列表里对应的型号。`doctor` 使用 CLI 帮助、登录状态和模型目录做检查，目录查询可能联网，不发起模型推理。
2. **按需安装和登录。** 缺少哪一个 CLI，就运行 `python install_tools.py agy` 或 `python install_tools.py codex`。安装器会下载并执行厂商安装脚本。Google 端启动 `agy`，在官方界面登录，并在 `/config` 中确认 **Use AI Credits** 已关闭（仅使用订阅额度时）。GPT 端运行 `codex login`，选择 ChatGPT 账号，用 `codex login status` 确认。账户授权在实际运行框架的机器上完成。[Google CLI 入门](https://antigravity.google/docs/getting-started?tab=cli) · [OpenAI 登录说明](https://developers.openai.com/codex/auth/)
3. **绑定模型。** 运行 `python setup_router.py init`，再运行 `python setup_router.py doctor --strict`。前者从实际目录中匹配型号，将路径、模型 ID 与档位写入本机 `router.local.json`。后者用退出码 `0` 表示八个首选配置档均通过预检，用 `2` 表示存在缺项、禁用项或待写入的匹配项。账号缺少某个型号时，可在本机配置中改绑实际型号，或添加 `"disabled_workers": ["claude"]` 等禁用项，然后重新初始化。其余可用路线仍可单独使用。
4. **开始对话。** 用 Antigravity 打开此仓库目录，在模型选择器中显式选择 **Neon · Medium**。若要用于已有项目，在工具目录运行 `python setup_router.py link --workspace "你的项目目录"`，然后在 Antigravity 中打开该项目，开始新对话。这个命令会将一条引用本工具的项目规则写入 `.agents/rules/gemini-gpt-router.md`。若工具目录迁移，及时核对该规则中的路径。仅使用终端时，可以从已绑定的配置中读取 Neon 的精确模型 ID，在工作区运行 `agy --model <模型ID>`。

### 让远程 agent 配置

将代码放到远程机器的固定目录，把下面这段交给有终端权限的配置 agent。如果有现成项目，可在末尾补上项目路径。

> 请进入这份源码的根目录，读取 AGENTS.md，按其中的流程检查环境、安装缺少的官方 CLI、初始化本机模型配置，并运行离线测试和示例。若我指定了业务项目，将规则接入该项目。需要本人登录时，说明在哪台机器、哪个官方界面操作。最后汇报可用路线、缺失项和验收结果。本次部署只做目录检查和离线验收。

配置 agent 的执行步骤和验收口径见 [AGENTS.md](./AGENTS.md)。接入其他项目时，生成的工作区规则含当前电脑的工具安装路径；在公开提交业务项目之前，可将该本机规则加入业务仓库的 `.git/info/exclude`。

## 一次独立审查如何往返

你可以直接对 Neon 说：“请独立检查这个脚本有没有把平均距离当成 RMSE，给出两个样本的平方误差，并用运行结果验收。”仓库提供了可复现的[任务单](./examples/review.md)和[目标脚本](./examples/workspace/check_fixture.py)。

**使用 Antigravity 的 Claude。** 先生成交接单。这一步只写本地 JSON，预期状态为 `handoff_ready`。重复运行时请用新的输出文件名。

```sh
python router.py review --worker claude --workspace examples/workspace --brief examples/review.md --run --output review-handoff.json
```

可以在 Antigravity 桌面端另开 Claude 会话交付任务单，再将其答复带回 Neon。已配置官方 `agy` 时，用户也可以直接执行下列一次性命令，或由 Antigravity 原生会话发起执行。该命令会使用 Google 账号额度。消费账号的直接脚本使用范围见 [Google 官方 FAQ](https://antigravity.google/docs/faq/)。

```sh
python native_handoff.py --handoff review-handoff.json
```

**使用自己的 GPT。** 若选择 GPT 审查，同一份任务单可以通过下一条命令直接派给已登录的 Codex。该命令会使用 OpenAI 账号额度。`review` 和 `design` 走只读沙箱，`implement` 可写目标工作区。Google 原生审查使用其 CLI 的 plan 模式及客户端权限设置。

```sh
python router.py review --worker gpt --workspace examples/workspace --brief examples/review.md --run
```

`worker_replied` 表示已收到实际回复。对这个例子，Neon 可以用“两个平方误差为 25 和 0，RMSE 四舍五入为 3.536”对照验收。对真实工程任务，验收依据换成自己的 diff、测试结果或数据证据。[自定义任务单模板](./examples/brief-template.md)

## 验证范围和常见问题

- **怎样验证框架？** 在仓库根目录运行 `python -B -m unittest discover -s tests -v`。测试用模拟进程覆盖路由、升档、配置隔离、中文路径和回执校验。真实账号上的端到端验证需要收到目标模型回复，并通过对应任务的验收。[离线测试](./tests/test_project.py)
- **为什么查到了模型却未能调用？** `full_preflight` 仅表示目录预检通过。Google 端查看 `agy models` 及当前套餐，Codex 端核对模型选择器、登录方式和当前用量。客户端升级后重新运行 `init`，尤其要核对原生模型 ID 及最高档名称。
- **只有桌面端怎么办？** 有资格的模型仍可在 Antigravity 桌面端手工交接。用这个仓库自动派给 GPT 需要本机 Codex CLI。普通 Gemini 网页聊天无法直接执行本地脚本。
- **如何继续了解选型？** 原文出处、设计取舍和一个合成数据复核示例见[方案与使用](./方案与使用.md)。
