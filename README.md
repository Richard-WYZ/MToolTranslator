# MToolTranslator（MTool 汉化工具）

[English](README_EN.md) | 简体中文

[![CI](https://github.com/Richard-WYZ/MToolTranslator/actions/workflows/ci.yml/badge.svg)](https://github.com/Richard-WYZ/MToolTranslator/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

MToolTranslator 是一个与 [MTool](https://mtool.app/) 工作流高度绑定的实验性日文游戏汉化工具。它读取 MTool 导出的扁平 JSON，在保护键名、控制符、变量和原始顺序的前提下，把日文值翻译为简体中文，并提供动态术语、断点恢复、质量检查和人工复核界面。

> [!WARNING]
> 当前版本仅支持 **日文 → 简体中文**。本项目仍处于实验阶段，不保证翻译质量、运行速度、模型可用性或对所有 MTool 文件的兼容性。请先备份游戏和原始翻译文件。

## 与 MTool 的关系及发布说明

本项目不是 MTool 官方项目，与 MTool 作者或团队没有隶属、授权或合作关系。

MTool 自身提供多种翻译引擎，其中部分高级引擎或功能需要达到相应的支持等级。支持 MTool 作者可以提升等级并解锁更多翻译选项，可能获得比本项目更好的翻译质量或更快的速度；如果有能力，建议优先支持原作者。

如果 MTool 原作者不希望本项目公开发布，我会隐藏本仓库。

请只翻译你拥有或已获授权处理的内容，并遵守游戏、模型、API 服务和所在地的法律及许可要求。

## AI Vibe Coding 声明

本项目的代码、UI、测试和文档完全通过 AI 编程代理以 vibe coding 方式生成。仓库维护者负责提出目标、授权测试、选择方案和验收结果，但项目没有经过专业团队的完整代码审查、安全审计或商业级验证。请谨慎阅读代码，不要因为测试通过就默认它绝对安全或正确。

参与开发前请阅读[贡献指南](CONTRIBUTING.md)。日常开发在 `dev` 分支进行，`master` 仅用于稳定版本和发布。

## 使用方法

### 使用便携版

1. 从 GitHub Releases 下载 `MToolTranslator-vX.Y.Z-windows-x64.zip`。
2. 解压到任意可写目录，不需要安装。
3. 运行 `MToolTranslator.exe`。
4. 首次使用时，在“设置”中新增提供商配置，选择 OpenDesign Go 或其他预设，填写 API Key，点击“连接并获取模型”；选择默认模型后点击“完成并使用”。
5. 建议先执行单模型普通测试和 NSFW 测试；测试会发送请求并可能消耗 Token。
6. 在 MTool 中导出原始翻译文件 `ManualTransFile.json`。
7. 在翻译页导入该 JSON，点击“开始翻译”。文件检查和准备会自动执行，无需手动预检；高级翻译方式和参数可按需展开。
8. 在“原文与译文”中全文搜索日文或中文、查看对照和修改译文。修改会自动保存草稿，“确认并下一条”才表示人工确认；也可以主动运行 AI 检查。
9. 翻译完成且输出结构完整后即可导出当前结果，不必等待复核队列清空；后续仍可继续复核并再次导出，再由 MTool 加载。

程序目录会生成或使用：

- `.env`：本机连接配置和密钥，已被 Git 忽略；
- `.model-status.json`：模型测试状态和上次测试时间，不保存模型回复；
- `.checkpoints/`：可恢复的任务断点。

### 复核与导出

- “原文与译文”支持当前文件的全量原文、译文或双侧搜索，搜索与状态筛选可组合；匹配结果高亮，并可在结果、相邻条目和术语出现位置间定位。
- “项目历史”统一显示同一项目的进度、继续翻译和查看译文入口，支持搜索和状态筛选。切换项目保留工作副本与检查点。
- 默认导出使用另存为，不再询问是否清理项目；覆盖原文件仍可从“更多导出方式”主动选择，并保留首次备份。
- 复核页面将条目分为“必须复核”“建议复核”“系统保留”和“已确认译文”，数字、符号等无需翻译内容不会挤占待复核列表。
- AI 复核可以分别选择主复核模型、验证模型和敏感内容模型；只有通过结构与质量校验的结果才会自动应用。
- AI 复核会先对旧检查点执行可回滚的确定性校正：代码调用、资源标识和字符范围不会送入模型，已经被误译的值会恢复为权威原文；多行文本整段修复失败时会按原始行结构隔离重试。
- 模型拒绝检测只硬拦截明确的 AI、政策或无法协助等元话术；“不适合”“拒绝请求”等正常剧情表达不会因关键词本身被判为拒绝，语义不明确的元回答只进入建议复核。
- AI 复核、单条保存和批量确认执行期间会锁定相关操作并显示等待动画，避免重复提交。
- 翻译或 AI 复核正在写入时不能导出；任务结束后，即使仍有待复核条目，也可以导出结构完整的当前快照。
- 导出不会删除工作副本，因此可以继续复核并反复导出。关闭存在未导出结果的桌面程序时会先显示原生确认框，断点和临时结果会保留。

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

普通构建会写入独立的 `build/dist/MToolTranslator-dev-<时间戳>-windows-x64/`
目录。正式发布须在与 `origin/master` 一致的干净 `master` 上执行：

```powershell
tools\build.ps1 -Version 0.2.6
```

正式输出位于 `build/dist/MToolTranslator-v0.2.6-windows-x64/`，初始仅包含
`MToolTranslator.exe`；已有输出目录及其中的运行时文件不会被覆盖或自动清理。

## 当前状态

- **OpenDesign Go** 已验证模型目录发现和少量普通日文批量翻译；尚未完成成人内容或全文件质量、速度验证。OpenCode Go 保留兼容支持及此前的验证记录。
- 提供商返回的模型目录会随账号和服务变化；界面里出现某个模型，不代表该模型已经通过质量、成人内容或完整文件测试。请使用普通/NSFW 测试按钮自行验证。
- Ollama、OpenAI 兼容接口和 Anthropic Messages 传输代码仍属于实验功能，暂未作为正式支持的提供方完成系统验证。
- 不含日文的条目会保持不变；英文到中文及其他翻译方向暂不支持。
- 软件不会附带 API 密钥。密钥只能保存在本机 `.env` 或通过设置界面写入。

## 速度与成本

本项目当前优先考虑翻译质量。最近验证的 **14,655 条**外部样本使用 **MiniMax M3 + Qwen 3.7 Plus**，翻译阶段耗时 **253.396 秒（约 4.22 分钟）**，总耗时约 4.48 分钟，消耗 **334,922 Token**。但人工检查仍发现短文本批次存在语义错位风险，因此达到速度目标不代表已经达到生产质量。

较大的 **61,978 条**基准文件最近一次完整翻译阶段耗时 **3476.365 秒（约 57.94 分钟）**，消耗 **6,137,210 Token**，仍超过 30 分钟硬性目标。当前版本加入了批次语义防护、代码保护、上下文污染隔离和 AI 复核工作流，但尚未完成一次新的全量实跑来证明所有修复的端到端效果。

简单说：如果你不怕花时间，而且 Token 多得用不完，可以试试；如果你需要稳定、快速、低成本的生产工具，请暂时不要依赖本项目。

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

最终 JSON 保留所有原始键、键顺序和结构，只允许修改值。每个条目必须进入 `translated`、`preserved`、`translated_needs_review` 或 `review_required` 状态之一。

## 项目架构

```text
ui/           浏览器界面、任务进度、设置与人工复核
app/          FastAPI 路由、桌面生命周期和应用服务
translation/  分类、术语、保护、批处理、模型路由、质量与断点
common/       路径和通用基础设施
tests/        必需的单元测试与通用 CSV fixture
tools/        测试、打包、审计和性能工具
```

依赖方向为 `ui -> app -> translation`，`common` 只提供共享基础设施。模型客户端只负责传输；分类、术语和质量策略不应绑定特定提供方。更详细的说明见[软件架构](docs/software-architecture.md)。

## 配置与模型

复制 `.env.example` 为 `.env`，或直接通过设置界面修改。先选择或新增提供商配置，再选择模型。预设会填写 API URL 和默认协议，高级连接参数仍可调整。OpenDesign Go 使用 Chat Completions，不会沿用 OpenCode Go 的会话头或专用模型路由。获取模型目录本身不执行推理；普通和 NSFW 可用性测试会逐个模型发送极短请求并消耗少量 Token。

每套提供商配置独立保存名称、地址、密钥、模型目录、启用状态、协议和默认模型。选择已保存配置即可切换；存在未保存修改或运行中的任务时不能切换。新配置不会继承其他提供商的密钥或模型目录。配置和密钥均保存在本机 `.env`，设置接口只返回密钥是否已配置。旧版单提供商配置仍可读取。

模型测试结果按连接上下文和模型 ID 持久化。模型目录更新时，新模型默认勾选并显示“未测试”；仍存在的模型保留历史；连接协议、地址或密钥变化后，旧结果会显示为过期参考。切换回原连接时可继续查看其历史测试记录。

## 参考与致谢

本项目在工作流和工程思路上参考或研究了以下项目/服务，但不代表存在官方关系，也不表示直接复制其代码：

- [MTool](https://mtool.app/)：游戏文本导出、导入和运行时加载工作流；
- [GalTransl](https://github.com/GalTransl/GalTransl)：Galgame 批量翻译、上下文和术语工作流；
- [SakuraLLM](https://github.com/SakuraLLM/SakuraLLM)：日中轻小说/Galgame 翻译模型与提示格式；
- [OpenDesign Cloud API](https://open-design.ai/help/cloud-api/)：OpenDesign Go 的连接方式和协议说明；
- [CCSwitch](https://github.com/farion1231/cc-switch) 和 [Magpie](https://github.com/yetone/magpie)：提供商预设、连接与模型分层选择的设计参考；
- [OpenCode Go](https://opencode.ai/docs/providers#opencode-go)：保留兼容的第三方 API 接入；
- [Ollama](https://ollama.com/)：本地模型传输接口的实验性支持。

请分别遵守上述项目、模型和服务的许可证及使用条款。
