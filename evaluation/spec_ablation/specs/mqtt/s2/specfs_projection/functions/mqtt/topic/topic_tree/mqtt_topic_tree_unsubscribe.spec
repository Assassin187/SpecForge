[PROMPT]
Implement function `mqtt_topic_tree_unsubscribe`. Responsibility: 移除过滤器条目中的会话订阅关系

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

- FUNC `find_entry`
  role: 定位过滤器条目
```c
static ssize_t find_entry(const mqtt_topic_tree_t* t, const char* filter);
```

- FUNC `entry_remove_sid`
  role: 删除会话 ID
```c
static void entry_remove_sid(filter_entry_t* e, int sid);
```

- FUNC `delete_entry`
  role: 空条目时删除过滤器
```c
static void delete_entry(mqtt_topic_tree_t* t, size_t idx);
```

[GUARANTEE]
```c
void mqtt_topic_tree_unsubscribe(mqtt_topic_tree_t* t, int session_id, const char* filter);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为主题树 t、会话 ID session_id、过滤器 filter

**Post-Condition**:
- 无返回值

**Invariant**:
- 未命中过滤器时不修改状态
- 删除条目采用末尾覆盖保持数组紧凑

**System Algorithm**:
- 查找 filter 条目，命中后删除 session_id；若条目 sid_count 变为 0，则删除整个条目
