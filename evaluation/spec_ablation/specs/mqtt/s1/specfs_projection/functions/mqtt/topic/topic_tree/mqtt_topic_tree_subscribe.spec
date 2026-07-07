[PROMPT]
Implement function `mqtt_topic_tree_subscribe`. Responsibility: 向过滤器条目添加会话订阅关系（去重）

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

- FUNC `ensure_entry_cap`
  role: 扩容过滤器条目数组
```c
static void ensure_entry_cap(mqtt_topic_tree_t* t, size_t need);
```

- FUNC `entry_has_sid`
  role: 检查会话是否已订阅
```c
static bool entry_has_sid(const filter_entry_t* e, int sid);
```

- FUNC `ensure_sid_cap`
  role: 扩容会话 ID 数组
```c
static void ensure_sid_cap(filter_entry_t* e, size_t need);
```

[GUARANTEE]
```c
void mqtt_topic_tree_subscribe(mqtt_topic_tree_t* t, int session_id, const char* filter);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为主题树 t、会话 ID session_id、过滤器 filter

**Post-Condition**:
- 无返回值；成功时订阅关系写入树结构

**Invariant**:
- 同一 filter 下 session_id 不重复存储
- 创建新条目失败时回滚 entry_count

**System Algorithm**:
- 参数非法直接返回；先查找 filter 条目，不存在则扩容并新建；若 session_id 尚未存在则扩容 sid 数组并追加
