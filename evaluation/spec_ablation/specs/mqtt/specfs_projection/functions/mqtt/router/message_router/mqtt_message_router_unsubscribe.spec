[PROMPT]
Implement function `mqtt_message_router_unsubscribe`. Responsibility: 删除会话在指定过滤器上的订阅关系

[RELY]
- STRUCT `struct mqtt_message_router`
  role: 消息路由器私有状态结构体
```c
struct mqtt_message_router;
```

- FUNC `mqtt_topic_tree_unsubscribe`
  role: 移除订阅关系
```c
void mqtt_topic_tree_unsubscribe(mqtt_topic_tree_t* t, int session_id, const char* filter);
```

[GUARANTEE]
```c
void mqtt_message_router_unsubscribe(mqtt_message_router_t* r, int session_id, const char* filter);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为路由器 r、会话 ID session_id、过滤器 filter

**Post-Condition**:
- 无返回值

**Invariant**:
- 删除后空 filter 条目可由 topic_tree 自动清理
- 非法 r 不产生副作用

**System Algorithm**:
- r 为空直接返回；否则调用 mqtt_topic_tree_unsubscribe 删除映射
