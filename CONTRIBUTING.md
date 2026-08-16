# 贡献指南 / Contributing

## 分支工作流

- `master` 是稳定和发布分支，不用于日常开发。
- `dev` 是日常开发与集成分支。代码、测试和文档变更都应从最新的 `dev` 开始。
- 修改文件前先运行 `git branch --show-current`，确认当前不在 `master`。
- 禁止直接向 `master` 提交或推送。
- 变更完成后，将提交推送到 `dev`，并确保 CI 通过。
- 只有发布或仓库维护者明确授权时，才能通过 Pull Request 将 `dev` 合并到 `master`。
- 合并完成后，将 `master` 的合并结果快进同步回 `dev`，避免两个长期分支产生不必要的分叉。

如果工作区存在未提交改动，不要擅自切换分支。请先确认改动归属，并与仓库维护者确认处理方式。

## 验证要求

提交变更前运行：

```powershell
tools\run_tests.ps1 -q
git diff --check
```

翻译策略变更还必须增加相应的聚焦测试和回归测试。

## Branch workflow (English)

- `master` is the stable release branch and must not be used for routine development.
- `dev` is the development and integration branch. Start code, test, and documentation changes from the latest `dev`.
- Run `git branch --show-current` before editing and verify that the current branch is not `master`.
- Do not commit or push directly to `master`.
- Push completed changes to `dev` and ensure CI passes.
- Merge `dev` into `master` through a pull request only for a release or with explicit maintainer authorization.
- After merging, fast-forward `dev` to the resulting `master` commit to keep the long-lived branches aligned.

Do not switch branches without confirmation when the working tree contains uncommitted changes. Identify who owns the changes and agree on how to handle them first.
