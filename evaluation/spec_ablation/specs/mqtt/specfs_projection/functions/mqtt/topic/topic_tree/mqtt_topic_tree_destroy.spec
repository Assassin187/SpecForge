[PROMPT]
Implement function `mqtt_topic_tree_destroy`. Responsibility: 销毁主题树及全部订阅条目资源

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
void mqtt_topic_tree_destroy(mqtt_topic_tree_t* t);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为主题树指针 t（可为空）

**Post-Condition**:
- 无返回值

**Invariant**:
- 每个条目资源最多释放一次
- NULL 输入不产生副作用

**System Algorithm**:
- t 为空直接返回；遍历释放每个条目的 filter 与 sids；释放 entries 数组和 t 本体
