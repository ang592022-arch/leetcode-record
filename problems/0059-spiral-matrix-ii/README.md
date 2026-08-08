# LC59 螺旋矩阵 II / Spiral Matrix II

<!-- 此文件由 scripts/leetcode_repo.py 根据 metadata/problems.json 生成。 -->

- 难度：Medium
- Topics：Array、Matrix、Simulation
- 主分类：Simulation
- 验证状态：历史导入，Accepted 状态待验证

## 我的代码

[`solution.cpp`](solution.cpp) 是保留的原始解答；自动化不会覆盖它。

## Codex 分析

- 解法思路：维护 left/right/top/bottom 四条边，按层顺时针填入递增数字。
- 时间复杂度：O(n^2)
- 空间复杂度：O(1) extra（输出矩阵为 O(n^2)）
- 易错点：每完成一条边就要收缩对应边界；条件或收缩顺序错误时容易重复填充中心行、中心列，建议用 n=1、n=2、n=3 回归。
- 分析作者：Codex
