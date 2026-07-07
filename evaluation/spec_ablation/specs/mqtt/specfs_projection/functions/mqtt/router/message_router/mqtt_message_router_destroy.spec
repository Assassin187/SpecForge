[PROMPT]
Implement function `mqtt_message_router_destroy`. Responsibility: 销毁消息路由器并释放主题树资源

[RELY]
- STRUCT `struct mqtt_message_router`
  role: 消息路由器私有状态结构体
```c
struct mqtt_message_router;
```

- FUNC `mqtt_topic_tree_destroy`
  role: 销毁主题订阅树
```c
void mqtt_topic_tree_destroy(mqtt_topic_tree_t* t);
```

[GUARANTEE]
```c
void mqtt_message_router_destroy(mqtt_message_router_t* r);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为路由器指针 r（可为空）

**Post-Condition**:
- 无返回值

**Invariant**:
- 不销毁外部传入的 session_manager
- 可安全处理 NULL 输入

**System Algorithm**:
- r 为空直接返回；否则销毁 r->topics 并释放 r
