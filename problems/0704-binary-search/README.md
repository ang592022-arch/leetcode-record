# LC704 二分查找 / Binary Search

<!-- 此文件由 scripts/leetcode_repo.py 根据 metadata/problems.json 生成。 -->

- 难度：Easy
- Topics：Array、Binary Search
- 主分类：Binary Search
- 验证状态：历史导入，Accepted 状态待验证

## 我的代码

[`solution.cpp`](solution.cpp) 是保留的原始解答；自动化不会覆盖它。

## Codex 分析

- 解法思路：在有序数组的闭区间上二分，命中返回下标，区间为空时返回 -1。
- 时间复杂度：O(log n)
- 空间复杂度：O(1)
- 易错点：right 是有符号 int，而 nums.size() 是无符号类型；题目非空约束避免了空数组下的转换问题。
- 分析作者：Codex
