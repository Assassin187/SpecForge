[PROMPT]
Implement function `mqtt_topic_tree_create`. Responsibility: 创建空主题树对象

[RELY]
- STRUCT `struct mqtt_topic_tree`
  role: 主题树私有结构体
```c
struct mqtt_topic_tree;
```

[GUARANTEE]
```c
mqtt_topic_tree_t* mqtt_topic_tree_create(void);
```

[SPECIFICATION]
**Pre-Condition**:
- 无输入参数

**Post-Condition**:
- 成功返回主题树指针，失败返回 NULL

**Invariant**:
- 初始 entry_count/entry_cap 为 0
- 初始 entries 指针为空

**System Algorithm**:
- 调用 calloc 分配并零初始化 mqtt_topic_tree_t
