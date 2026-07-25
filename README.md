# MToolTranslator（MTool 汉化工具）

[English](README_EN.md) | 简体中文

[![CI](https://github.com/Richard-WYZ/MToolTranslator/actions/workflows/ci.yml/badge.svg)](https://github.com/Richard-WYZ/MToolTranslator/actions/workflows/ci.yml)

MToolTranslator 是一个与 [MTool](https://mtool.app/) 工作流高度绑定的实验性
日文游戏汉化工具。它读取 MTool 导出的扁平 JSON，在保护键名、控制符、变量和
原始顺序的前提下，把日文值翻译为简体中文，并提供动态术语、断点恢复、质量检查
和人工复核界面。

> [!WARNING]
> 当前版本仅支持 **日文 → 简体中文**。本项目仍处于实验阶段，不保证翻译质量、
> 运行速度、模型可用性或对所有 MTool 文件的兼容性。请先备份游戏和原始翻译文件。

## 当前状态

- 目前只对 **OpenCode Go** 接入路径做过实际验证。
- OpenCode Go 返回的模型目录会随服务变化；界面里出现某个模型，不代表该模型已经
  通过质量、成人内容或完整文件测试。请使用普通/NSFW 测试按钮自行验证。
- Ollama、OpenAI 兼容接口和 Anthropic Messages 传输代码仍属于实验功能，暂未作为
  正式支持的提供方完成系统验证。
- 不含日文的条目会保持不变；英文到中文及其他翻译方向暂不支持。
- 软件不会附带 API 密钥。密钥只能保存在本机 `.env` 或通过设置界面写入。

## 速度与成本

本项目当前优先考虑翻译质量。已记录的一次代表性测试中，580 条样本约用
**169 秒**。2026-07-25 的方案 8 使用 **MiniMax M3 + Qwen 3.7 Plus** 混合路线，
已完成 `ManualTransFile.json` 全部 **61,978 条**；最新一次完整记录的翻译阶段为
**4000.095 秒（约 66.7 分钟）**，约消耗 540 万 Token。该次运行生成了完整输出，
但仍留下 1,201 条复核队列，独立审计另标出了 904 条问题项，因此不能理解为无需
人工检查的成品。

此前还存在两类容易混淆的记录：MiniMax 为主的快速路线约 **28 分钟**跑完全量，
但产生大量对齐和复核问题；更早的旧版质量路线曾在 4 小时内只完成约 20,278 条。
后者不是当前方案 8 的最终成绩。以上时间只计算模型翻译阶段，文件分析、术语处理、
检查点写入、最终校验和人工复核会增加实际总耗时。实际表现也会随文本、模型、并发、
网络和提供方限流显著变化。

简单说：如果你不怕花时间，而且 Token 多得用不完，可以试试；如果你需要稳定、
快速、低成本的生产工具，请暂时不要依赖本项目。

## 工作原理

```text
MTool 导出 ManualTransFile.json
              |
              v
      分类并保留代码/资源标识
              |
              v
  保护控制符、变量、标签和术语
              |
              v
      按文本类型和预算分批翻译
              |
              v
  结构校验、污染检测、失败重试
              |
              v
   断点/复核报告 + MTool 兼容 JSON
```

最终 JSON 保留所有原始键、键顺序和结构，只允许修改值。每个条目必须进入
`translated`、`preserved`、`translated_needs_review` 或 `review_required`
状态之一。

## 项目架构

```text
ui/           浏览器界面、任务进度、设置与人工复核
app/          FastAPI 路由、桌面生命周期和应用服务
translation/  分类、术语、保护、批处理、模型路由、质量与断点
common/       路径和通用基础设施
tests/        必需的单元测试与通用 CSV fixture
tools/        测试、打包、审计和性能工具
```

依赖方向为 `ui -> app -> translation`，`common` 只提供共享基础设施。模型客户端
只负责传输；分类、术语和质量策略不应绑定特定提供方。更详细的说明见
[软件架构](docs/software-architecture.md)。

## 使用方法

### 使用便携版

1. 从 GitHub Releases 下载 `MToolTranslator-vX.Y.Z-windows-x64.zip`。
2. 解压到任意可写目录，不需要安装。
3. 运行 `MToolTranslator.exe`。
4. 在“设置”中填写 OpenCode Go API Key，获取模型并勾选允许用于翻译的模型。
5. 建议先执行单模型普通测试和 NSFW 测试；测试会发送请求并可能消耗 Token。
6. 在 MTool 中导出原始翻译文件 `ManualTransFile.json`。
7. 在翻译页导入该 JSON，选择执行方案后开始翻译。
8. 查看进度和复核队列，完成后导出 JSON，再由 MTool 加载。

程序目录会生成或使用：

- `.env`：本机连接配置和密钥，已被 Git 忽略；
- `.model-status.json`：模型测试状态和上次测试时间，不保存模型回复；
- `.checkpoints/`：可恢复的任务断点。

### 从源码运行

要求 Windows 和 Python 3.10+（当前 CI 使用 Python 3.12）。

```powershell
python -m pip install -r requirements.txt
python main.py
```

浏览器访问 `http://127.0.0.1:8000`。桌面模式：

```powershell
python main.py --desktop
```

运行测试和打包：

```powershell
python -m pip install -r requirements-dev.txt
tools\run_tests.ps1 -q
tools\build.ps1
```

便携输出位于 `build/dist/`。

## 配置与模型

复制 `.env.example` 为 `.env`，或直接通过设置界面修改。已知 API URL 会自动填写，
高级连接参数默认折叠。获取模型目录本身不执行推理；普通和 NSFW 可用性测试会逐个
模型发送极短请求并消耗少量 Token。

模型测试结果会按模型 ID 持久化。模型目录更新时，新模型默认勾选并显示“未测试”；
仍存在的模型保留历史；连接协议、地址或密钥变化后，旧结果会显示为过期参考。

## 自动构建与发布

- `master` 和 `dev` 的提交及 Pull Request 会运行 GitHub Actions 测试。
- 修改 `VERSION` 并合并到 `master` 后，Actions 会运行完整测试、构建 Windows
  便携包、创建 `vX.Y.Z` 标签并发布 Release。
- Release 说明由 GitHub 根据合并的 PR、标签和提交自动生成；可用 PR 标签调整分类。

日常开发在 `dev` 分支进行，确认后通过 Pull Request 合并到 `master`。版本发布时
同时更新 `VERSION`。

## 参考与致谢

本项目在工作流和工程思路上参考或研究了以下项目/服务，但不代表存在官方关系，
也不表示直接复制其代码：

- [MTool](https://mtool.app/)：游戏文本导出、导入和运行时加载工作流；
- [GalTransl](https://github.com/GalTransl/GalTransl)：Galgame 批量翻译、上下文和术语工作流；
- [SakuraLLM](https://github.com/SakuraLLM/SakuraLLM)：日中轻小说/Galgame 翻译模型与提示格式；
- [OpenCode Go](https://opencode.ai/docs/providers#opencode-go)：当前唯一实际验证过的第三方 API 接入；
- [Ollama](https://ollama.com/)：本地模型传输接口的实验性支持。

请分别遵守上述项目、模型和服务的许可证及使用条款。

## AI Vibe Coding 声明

本项目的代码、UI、测试和文档完全通过 AI 编程代理以 vibe coding 方式生成。
仓库维护者负责提出目标、授权测试、选择方案和验收结果，但项目没有经过专业团队的
完整代码审查、安全审计或商业级验证。请谨慎阅读代码，不要因为测试通过就默认它
绝对安全或正确。

## 与 MTool 的关系及发布说明

本项目不是 MTool 官方项目，与 MTool 作者或团队没有隶属、授权或合作关系。

MTool 自身提供多种翻译引擎，其中部分高级引擎或功能需要达到相应的支持等级。
支持 MTool 作者可以提升等级并解锁更多翻译选项，可能获得比本项目更好的翻译质量
或更快的速度；如果有能力，建议优先支持原作者。

如果 MTool 原作者不希望本项目公开发布，我会隐藏本仓库。

请只翻译你拥有或已获授权处理的内容，并遵守游戏、模型、API 服务和所在地的法律
及许可要求。

## 许可证

项目代码以 [MIT License](LICENSE) 发布。第三方依赖、模型和服务适用各自的许可证，
MIT 许可证不覆盖它们。
