# 合成 Redis 数据类型

[TYPE-1] String 可存字符串，Hash 可存字段和值的映射，List 适合有序序列。Set 用于不重复成员，Sorted Set 将成员与分数关联并按分数排序。

[TYPE-2] 使用 ZADD 写入有序集合成员及其分数。排行榜示例使用 Sorted Set；去重标签示例使用 Set。AOF 日志并非 Sorted Set 排名分数。
