# LC977 有序数组的平方 / Squares of a Sorted Array

<!-- 此文件由 scripts/leetcode_repo.py 根据 metadata/problems.json 生成。 -->

- 难度：Easy
- 语言：cpp
- Topics：Array、Two Pointers、Sorting
- 主分类：Two Pointers
- 验证状态：历史导入，Accepted 状态待验证

## 我的代码

[`solution.cpp`](solution.cpp) 是保留的原始解答；自动化不会覆盖它。

## Codex 分析

- 解法思路：比较左右端平方值，把较大的一个从结果数组末尾向前填充。
- 时间复杂度：O(n)
- 空间复杂度：O(n)
- 易错点：平方运算使用 int；当前题目约束下安全，迁移到更大数值范围时需注意溢出。
- 分析作者：Codex
