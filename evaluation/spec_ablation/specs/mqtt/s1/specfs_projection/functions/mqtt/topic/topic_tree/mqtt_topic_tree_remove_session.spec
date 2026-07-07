[PROMPT]
Implement function `mqtt_topic_tree_remove_session`. Responsibility: 从主题树全部过滤器中移除指定会话

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

- FUNC `entry_remove_sid`
  role: 从条目移除会话 ID
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
void mqtt_topic_tree_remove_session(mqtt_topic_tree_t* t, int session_id);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为主题树 t 与会话 ID session_id

**Post-Condition**:
- 无返回值

**Invariant**:
- 遍历删除过程中保持数组索引有效
- 删除后不会残留空 sid 条目

**System Algorithm**:
- 遍历所有条目删除 session_id；若条目清空则删除该条目并继续处理当前位置
