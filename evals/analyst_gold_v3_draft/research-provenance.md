# 方法来源与采用范围

2026-11-11通过GitHub只读查阅anthropics/knowledge-work-plugins官方仓库；没有安装、运行外部代码，也没有整段复制其skill。将它用于改进老师分析方法，不声称这些来源能保证结果自动正确。

固定参考版本：877a617b435ee2edfb002d9a3703c0a1a03aa1ba。

| 来源 | 读取内容标识（blob SHA） | 采用做法 |
|---|---|---|
| [分析方法](https://github.com/anthropics/knowledge-work-plugins/blob/877a617b435ee2edfb002d9a3703c0a1a03aa1ba/data/skills/analyze/SKILL.md) | 31ce9c58dd4de3038643750373de5b71a2494fa2 | 按快答/完整分析/正式报告选择深度，先问题与口径再计算、解释、建议 |
| [绘图方法](https://github.com/anthropics/knowledge-work-plugins/blob/877a617b435ee2edfb002d9a3703c0a1a03aa1ba/data/skills/data-visualization/SKILL.md) | 409cce60f59b463a8386c83cde03dc7e70be93f3 | 按趋势/比较/结构/分布选图，避免混合单位共轴，保留底层结果 |
| [分析核验](https://github.com/anthropics/knowledge-work-plugins/blob/877a617b435ee2edfb002d9a3703c0a1a03aa1ba/data/skills/validate-data/SKILL.md) | 30a1bac41bf9679dc7d96b7ec59a116f03ec0575 | 检查分子分母、关联膨胀、样本选择、观察窗、结论边界 |

文件读取来自默认分支时的上述blob，固定版本用于来源追踪。本项目采用的方法没有依赖外部skill执行；数值均由本地CSV/Decimal、DuckDB、SciPy和scikit-learn实际计算。

数据：[Olist巴西电商公开数据](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)，CC BY-NC-SA 4.0。原始CSV不提交，抽样规则、行数和文件哈希见source-manifest.json。此成果用于本地学习与评测，商业使用前须重新核验源许可。
