# LC27 移除元素 / Remove Element

<!-- 此文件由 scripts/leetcode_repo.py 根据 metadata/problems.json 生成。 -->

- 难度：Easy
- Topics：Array、Two Pointers
- 主分类：Array
- 验证状态：历史导入，Accepted 状态待验证

## 我的代码

[`solution.cpp`](solution.cpp) 是保留的原始解答；自动化不会覆盖它。

## Codex 分析

- 解法思路：fast 扫描输入，遇到不等于 val 的元素就写到 slow 位置并推进 slow。
- 时间复杂度：O(n)
- 空间复杂度：O(1)
- 易错点：返回长度之后的数组尾部内容没有语义，不应继续依赖。
- 分析作者：Codex
