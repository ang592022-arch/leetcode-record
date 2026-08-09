# LeetCode 仓库长期规则

本仓库保存用户本人提交的 LeetCode 代码。所有自动化、Codex 操作和人工维护都必须先遵守本文件。

## 最高优先级：保护原始代码

- `problems/<四位题号>-<slug>/solution.<ext>` 是首次导入的原始 Accepted 代码。禁止格式化、重写、重命名变量、补注释或用所谓“更优写法”覆盖它。
- 导入前后必须核对 SHA-256；仅整理路径时使用保持字节不变的移动操作。
- 已存在的 `solution.<ext>` 与新提交内容不同时，不得覆盖。把新版本保存到同题目录的 `submissions/`，使用不会冲突的时间戳文件名，并在 metadata 中记录两者。
- AI 建议的代码只能放在 `optimized.<ext>`，文件开头必须明确写明“Codex 优化版本”；它永远不能计作用户原始答案。
- 没有 Accepted 事件、提交记录或用户明确确认时，不得把任意代码标记为已验证 Accepted。历史导入应使用 `unverified_historical_import`。
- 编程语言是原始 submission 的一部分。禁止把 Python 改写、改名或伪装成 C++，也禁止反向转换；metadata 语言、文件扩展名与原始来源必须一致。
- GitHub Action 只能原样移动或复制经过验证的 submission 字节，不得翻译语言、格式化代码或仅因扩展名/metadata 冲突而猜测语言。

## 单一目录与分类

- 每道题只有一个真实目录：`problems/<四位题号>-<slug>/`。不要按多个 Topics 复制代码。
- Topics、主分类、相似题和未来 Obsidian 双链只记录在 `metadata/problems.json` 与生成的 README 中。
- 主分类只用于根 README 分组；修改分类不得移动或复制题目代码。
- 不创建空目录、空笔记、占位题解或重复文件。

## 新题处理流程

1. 只接收带有 Accepted 来源信息的同步记录；记录题号、slug、题名、语言和来源。
2. 查重：按题号、slug、目标路径和代码 SHA-256 检查。
3. 首次导入时原样写入 `solution.<ext>`；已有不同内容时按上面的 `submissions/` 规则保存。
4. 更新 `metadata/problems.json`，未知值保持未知，不编造复杂度、Topics 或 Accepted 状态。
5. 用仓库脚本重新生成题目 README 与根 README。不要手工维护生成区域。
6. 运行测试和敏感信息扫描，并检查最终 diff。对非原始代码运行 `git diff --check`；原始 `solution.*`/`submissions/*` 的既有空白必须原样保留，不得为了通过格式检查修改。
7. 使用规定格式提交，并只做正常的快进 push。

## README 与学习记录

- `metadata/problems.json` 是索引和统计的唯一数据源；根 README 的统计与表格由脚本生成。
- 每题 README 必须把“我的代码”和“Codex 分析”分开。用户代码只链接原始文件；AI 解释、风险和改进建议必须明确归入 Codex 分析。
- 只有实际存在内容时才创建 `notes.md`、`optimized.<ext>` 或相似题列表；不要生成模板式空章节。
- Easy 题说明保持简洁。任何复杂度、易错点和改进建议都应基于实际代码，而不是通用题解套话。
- metadata 应保留稳定的题号、slug、Topics、路径和哈希，以便未来生成 `[[LC20 有效的括号]]`、`[[栈]]` 等 Obsidian 双链。
- Solved、难度和语言统计只计算具有允许来源证据的 verified Accepted；历史未验证代码可以展示，但不得计入 Solved。
- 自动化脚本与测试 fixture 必须保存在 `scripts/`、`tests/` 或临时仓库，禁止写入真实 `problems/`、metadata 或 Solved 统计。

## Obsidian 增量同步

- 用户说“同步一下今天的题。”时，先运行 `sync-obsidian.cmd prepare`。脚本只用 `pull --ff-only` 更新本仓库，并依据 `.leetcode-sync/state.json` 找出新增或实际变化的题目。
- 只读取待同步题目的 metadata、真实代码，以及 Vault 中直接相关的算法笔记、知识点和索引；禁止扫描日记或无关知识区。
- Codex 完成语义笔记后运行 `sync-obsidian.cmd finalize` 和 `sync-obsidian.cmd validate`，再检查 diff 并只在 Obsidian 创建本地 commit。Vault 不得添加 remote 或 push。
- `scripts/obsidian_sync.py` 只负责检测、checkpoint 和基础校验，不得生成臆测性的题解，也不得改写任何 `solution.*`。

## Git 提交规范

- 新增一道题：`solve: LC20 Valid Parentheses`
- 一次同步多道题：`solve: sync <N> LeetCode solutions`
- 更新学习笔记：`docs: update LC20 notes`
- 仓库结构或自动化：`chore: <清晰说明>`
- 禁止使用 `update`、`test`、`new`、`123`、`修改` 等无法解释历史的消息。
- 一个同步提交只包含本次同步相关内容；不要夹带无关文件。

## Push 与历史安全

- 禁止 `git push --force`、`--force-with-lease`、`git reset --hard`、rebase/过滤后重写已发布历史、删除远端仓库或批量删除已有提交，除非用户明确要求并确认准确目标。
- pull 只能使用 `--ff-only`。若远端分叉，停止并诊断，不得通过覆盖历史来“修好” push。
- 正常 push 前必须确认分支、remote、待推送 commits 和工作区状态。pre-push hook 拒绝非快进更新，不得绕过 hook。
- 原 OneDrive 仓库和既有提交是迁移恢复点；不要修改或删除原副本。

## 敏感信息与自动化安全

- 禁止提交 API key、token、密码、Cookie、LeetCode Session、`csrftoken`、`cf_clearance`、浏览器配置、`.env` 或私人文件。
- 每次 commit 前运行 `scripts/security_scan.py`；命中高置信度秘密时停止，不得自行降级或忽略。
- 浏览器扩展若需要 GitHub 权限，只允许写入指定仓库；开启避免覆盖同题文件的时间戳/后缀设置。
- 不从浏览器 Cookie/Session 存储中导出认证信息。需要身份验证时使用扩展自身 OAuth 或用户主动提供的安全登录流程。

## 验证与汇报

- 修改自动化后运行全部单元测试和临时 bare remote 端到端测试；测试数据不得进入真实仓库历史。
- 同步完成只需汇报：新增题数、更新题数、commit hash、push 是否成功，以及任何需要用户处理的阻塞。
