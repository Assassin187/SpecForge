[PROMPT]
Implement function `ensure_entry_cap`. Responsibility: 扩容主题树条目数组以容纳更多 filter

[RELY]
- STRUCT `struct mqtt_topic_tree`
  role: 主题树私有结构体
```c
struct mqtt_topic_tree;
```

[GUARANTEE]
```c
static void ensure_entry_cap(mqtt_topic_tree_t* t, size_t need);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为主题树 t 与目标条目容量 need。

**Post-Condition**:
- t 为空或容量已足够时直接返回；扩容成功时 entries 和 entry_cap 更新到覆盖 need；realloc 失败时保持原数组不变。

**Invariant**:
- 只增长 entry_cap
- 不改变 entry_count

**System Algorithm**:
- 若主题树条目容量不足则按倍增策略 realloc entries。
