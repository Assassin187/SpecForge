[PROMPT]
Implement function `mqtt_message_router_subscribe`. Responsibility: 建立会话与主题过滤器的订阅关系

[RELY]
- STRUCT `struct mqtt_message_router`
  role: 消息路由器私有状态结构体
```c
struct mqtt_message_router;
```

- FUNC `mqtt_topic_tree_subscribe`
  role: 登记订阅关系
```c
void mqtt_topic_tree_subscribe(mqtt_topic_tree_t* t, int session_id, const char* filter);
```

[GUARANTEE]
```c
void mqtt_message_router_subscribe(mqtt_message_router_t* r, int session_id, const char* filter);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为路由器 r、会话 ID session_id、过滤器 filter

**Post-Condition**:
- 无返回值；订阅结果体现在 topic_tree

**Invariant**:
- 路由层不复制业务状态，仅委托 topic_tree 存储
- 非法 r 不产生副作用

**System Algorithm**:
- r 为空直接返回；否则调用 mqtt_topic_tree_subscribe 写入订阅关系
