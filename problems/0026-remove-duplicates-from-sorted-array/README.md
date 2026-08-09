# LC26 删除有序数组中的重复项 / Remove Duplicates from Sorted Array

<!-- 此文件由 scripts/leetcode_repo.py 根据 metadata/problems.json 生成。 -->

- 难度：Easy
- 语言：cpp
- Topics：Array、Two Pointers
- 主分类：Array
- 验证状态：历史导入，Accepted 状态待验证

## 我的代码

[`solution.cpp`](solution.cpp) 是保留的原始解答；自动化不会覆盖它。

## Codex 分析

- 解法思路：用 slow 指向当前去重结果的末尾，顺序扫描并把新值原地写回。
- 时间复杂度：O(n)
- 空间复杂度：O(1)
- 易错点：实现从下标 1 开始，依赖题目给出的非空数组约束。
- 分析作者：Codex
