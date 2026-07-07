[PROMPT]
Implement function `mqtt_message_router_remove_session`. Responsibility: 删除会话在所有过滤器上的订阅痕迹

[RELY]
- STRUCT `struct mqtt_message_router`
  role: 消息路由器私有状态结构体
```c
struct mqtt_message_router;
```

- FUNC `mqtt_topic_tree_remove_session`
  role: 清理会话全部订阅
```c
void mqtt_topic_tree_remove_session(mqtt_topic_tree_t* t, int session_id);
```

[GUARANTEE]
```c
void mqtt_message_router_remove_session(mqtt_message_router_t* r, int session_id);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为路由器 r 与会话 ID session_id

**Post-Condition**:
- 无返回值

**Invariant**:
- 会话断开后可通过本函数防止脏订阅残留
- 非法 r 不产生副作用

**System Algorithm**:
- r 为空直接返回；否则调用 mqtt_topic_tree_remove_session 全量清理
