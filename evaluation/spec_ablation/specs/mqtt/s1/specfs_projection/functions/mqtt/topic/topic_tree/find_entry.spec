[PROMPT]
Implement function `find_entry`. Responsibility: 按过滤器字符串查找条目索引

[RELY]
- STRUCT `struct mqtt_topic_tree`
  role: 主题树私有结构体
```c
struct mqtt_topic_tree;
```

- STRUCT `struct filter_entry`
  role: 过滤器条目结构体
```c
struct filter_entry;
```

[GUARANTEE]
```c
static ssize_t find_entry(const mqtt_topic_tree_t* t, const char* filter);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为主题树 t 与订阅过滤器字符串 filter。

**Post-Condition**:
- t 或 filter 为空时返回 -1；命中时返回条目索引，未命中返回 -1。

**Invariant**:
- 不修改主题树
- 只比较非空 filter 字段

**System Algorithm**:
- 遍历条目数组按 filter 字符串精确匹配并返回索引。
