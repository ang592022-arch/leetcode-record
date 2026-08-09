# LC203 移除链表元素 / Remove Linked List Elements

<!-- 此文件由 scripts/leetcode_repo.py 根据 metadata/problems.json 生成。 -->

- 难度：Easy
- 语言：cpp
- Topics：Linked List、Recursion
- 主分类：Linked List
- 验证状态：历史导入，Accepted 状态待验证

## 我的代码

[`solution.cpp`](solution.cpp) 是保留的原始解答；自动化不会覆盖它。

## Codex 分析

- 解法思路：增加 dummy 头结点统一处理删除头结点和中间结点，遍历时直接绕过目标值结点。
- 时间复杂度：O(n)
- 空间复杂度：O(1)
- 易错点：dummy 使用 new 创建但未释放；被移除结点也没有在函数内释放，需结合平台的所有权约定理解。
- 分析作者：Codex
