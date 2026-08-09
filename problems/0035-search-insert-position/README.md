# LC35 搜索插入位置 / Search Insert Position

<!-- 此文件由 scripts/leetcode_repo.py 根据 metadata/problems.json 生成。 -->

- 难度：Easy
- 语言：cpp
- Topics：Array、Binary Search
- 主分类：Binary Search
- 验证状态：已验证 Accepted

## 我的代码

[`solution.cpp`](solution.cpp) 是保留的原始解答；自动化不会覆盖它。

## Codex 分析

- 解法思路：在闭区间 [left, right] 上二分；未命中时 left 正好落在插入位置。
- 时间复杂度：O(log n)
- 空间复杂度：O(1)
- 易错点：闭区间模板要求循环条件为 left <= right，并在两侧都跳过 mid。
- 分析作者：Codex
