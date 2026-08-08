# LC209 长度最小的子数组 / Minimum Size Subarray Sum

<!-- 此文件由 scripts/leetcode_repo.py 根据 metadata/problems.json 生成。 -->

- 难度：Medium
- Topics：Array、Binary Search、Sliding Window、Prefix Sum
- 主分类：Sliding Window
- 验证状态：历史导入，Accepted 状态待验证

## 我的代码

[`solution.cpp`](solution.cpp) 是保留的原始解答；自动化不会覆盖它。

## Codex 分析

- 解法思路：right 扩张窗口累加；当窗口和达到 target 时持续移动 left，记录最短长度。
- 时间复杂度：O(n)
- 空间复杂度：O(1)
- 易错点：窗口单调收缩依赖 nums 全为正数；若允许负数，这个策略不成立。
- 分析作者：Codex
