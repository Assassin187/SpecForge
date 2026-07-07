[PROMPT]
Implement function `delete_entry`. Responsibility: 删除指定条目并释放其资源（末尾覆盖收缩）

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
static void delete_entry(mqtt_topic_tree_t* t, size_t idx);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为主题树 t 与待删除条目索引 idx。

**Post-Condition**:
- t 为空或 idx 越界时直接返回；否则释放目标 filter/sids，用末条目覆盖 idx 并减少 entry_count。

**Invariant**:
- 删除操作不保持 entries 顺序
- 只释放被删除条目的动态资源

**System Algorithm**:
- 释放目标条目的 filter 和 sids，再用末条目覆盖并缩减 entry_count。
