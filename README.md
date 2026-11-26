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

1. 从 [GitHub Releases](https://github.com/Richard-WYZ/MToolTranslator/releases) 下载 Windows x64 便携包，解压到可写目录，运行 `MToolTranslator.exe`。
2. 在“设置”中填写模型服务的连接地址和 API 密钥，确认保存密钥，点击“连接并获取模型”，选择默认模型。
3. 在 MTool 中导出 `ManualTransFile.json`，将文件导入翻译页，点击“开始翻译”。
4. 在“原文与译文”中对照日文检查译文。导出后，将生成的 JSON 加载回 MTool。

### 复核与导出

- 可以按原文、译文或复核状态查找条目。编辑会自动保存为草稿，点击“人工确认并下一条”即可确认当前译文。
- 在“项目历史”中可以继续上次的翻译或复核。任务断点保存在程序目录的 `.checkpoints/` 中。
- 翻译或 AI 复核结束后，输出结构完整即可导出。默认导出为新文件，之后仍可继续复核并再次导出。

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

最终 JSON 保留所有原始键、键顺序和结构，只更新需要翻译的值，不含日文的条目保留原值。每个条目必须进入 `translated`、`preserved`、`translated_needs_review` 或 `review_required` 状态之一。

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

可以在“设置”中为每个模型服务分别保存连接地址、密钥和默认模型，之后按需切换。使用已有配置时，便携版将 `.env` 放在 EXE 旁，源码版放在项目根目录。也可以从 `.env.example` 复制一份后填写。配置好的 `.env` 含有密钥，请仅在本机保存。

## 参考与致谢

- 游戏文本的导出、导入和运行时加载沿用 [MTool](https://mtool.app/) 的工作流。
- 批量翻译、上下文和术语处理参考了 [GalTransl](https://github.com/GalTransl/GalTransl)。

请分别遵守上述项目的许可证及使用条款。
