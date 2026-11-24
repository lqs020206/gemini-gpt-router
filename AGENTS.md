# 远程部署任务

当用户让你配置本框架时，从本目录执行以下流程。项目的日常对话规则见同目录的 GEMINI.md。如果你收到的是已经分派的实现或审查任务，直接按任务单工作。

## 配置流程

1. **定位安装目录。** 找到同时包含本文件、router.py 和 setup_router.py 的解压目录。检查当前操作系统以及可运行的 Python，要求 3.10+。Windows 可试 `py -3 --version`，macOS/Linux 可试 `python3 --version`。后续统一用这个解释器。若机器没有合适版本，优先复用机器已有的软件管理方式安装 Python 3；没有安装权限时报告具体缺项。Python 依赖全部来自标准库。
2. **核对传输和基线。** 若包内有 SHA256SUMS，运行 `python package_project.py verify`。运行 `python -B -m unittest discover -s tests -v`，记下通过数。运行 `python setup_router.py doctor`，检查本机可执行文件、Codex 的 ChatGPT 登录状态和两边的模型目录。
3. **补齐官方 CLI。** 按缺项执行 `python install_tools.py agy`、`python install_tools.py codex` 或 `python install_tools.py all`。如果用户提供的是已有安装，优先使用并核对实际路径。可在模板副本 router.local.json 配置 agy_path 或 codex_path。Windows 使用原生 .exe。安装后若当前 shell 的 PATH 尚未刷新，使用安装器报告的位置或重新打开终端。
4. **登录现场账户。** 若 Google 尚未登录，启动官方 agy 让用户在浏览器完成个人账户授权，确认订阅可用，Use AI Credits 关闭。若 Codex 尚未用 ChatGPT 登录，启动 `codex login` 引导用户完成官方登录，再查 `codex login status`。账户授权交给用户本人；继续完成另一通道和所有本地配置。账户仍在等待时，准确报告哪一步等待。这里用的是远程机器的官方登录，配置文件不需要密钥。
5. **绑定型号。** 运行 `python setup_router.py init`。它在本目录生成 router.local.json。查看八个档位的结果。Google 侧用 `agy models` 核实精确型号，Codex 侧用官方模型选择器核实型号、日常 medium 和困难档最高值。若模板型号在这台机器不可用，在本地 JSON 绑定能承担同类角色的实际模型，并向用户说明更换；不确定替代品时，保留待核项目。再次运行 init。该操作保留已填的有效绑定。调整单一机器时仅修改 router.local.json。
6. **启用工作区规则。** 若用户提供业务项目，运行 `python setup_router.py link --workspace "实际项目目录"`。已有同名规则内容不同时先读现有文件，保留用户规则。若没有业务项目，可将本工具目录作为 Antigravity 工作区，直接加载 GEMINI.md。使用原生终端时，可从 router.local.json 取 profiles.neon.standard.slug，运行 `agy --model <该精确ID>` 开始对话；桌面端则选定实际绑定的 Gemini 并明确设 Medium。
7. **验收并报告。** 运行下面的命令，再运行 `python setup_router.py doctor --strict`。返回框架目录、CLI 路径、八档绑定及依据、测试通过数、示例结果、已接入的项目、仍需用户处理的登录或额度问题。如果没有发起真实请求，将真实端到端推理标为“未测试”。在正式任务中按用户要求实际派工；单纯部署仅做预览和离线验收。

```text
python router.py chat
python router.py implement
python router.py review --avoid-pool claude
python router.py design --hard-reason "测试困难设计路由"
python router.py review --worker claude --workspace examples/workspace --brief examples/review.md --run --output setup-review-handoff.json
python examples/workspace/check_fixture.py
```

最后一项应输出 `RMSE = 3.536`。核对交接单包含 `status=handoff_ready`、真实目标工作区以及完整中文任务正文。重复验收时给输出文件换一个新名字。若本机明确禁用 Claude，保留该路由被拒绝的记录，加 `--config router.example.json` 仅演练模板交接单，并将账号可用性标为未通过。

## 配置报告约定

| 项目 | 如何判断 |
| --- | --- |
| 软件已就绪 | 程序存在，支持所需 CLI 参数 |
| 模型目录通过 | 可从账户目录或本地 Codex 模型缓存核对该型号与档位；注明来源 |
| 全部首选档预检通过 | `doctor --strict` 退出 0，表示八个档位可绑定。缺失、禁用或待初始化时退出 2；错误退出 1 |
| 原生桌面可用 | 实际查看模型选择器；只有桌面可用时，明确注明需要手工交接 |
| 端到端已验证 | 只有收到真实模型最终答复并通过任务验收，才标为通过 |

如果用户另外要求进行真实冒烟测试，优先在 examples/workspace 做一次只读 medium 请求，再将结果与本地样例输出核对。Antigravity 原生调用由用户直接使用官方客户端或在其原生环境发起，其他配置 agent 可以生成交接单和检查安装状态。无需接触旧机器的登录文件或环境密钥。
