[PROMPT]
Implement function `mqtt_message_router_create`. Responsibility: 创建消息路由器并绑定会话管理器与主题树

[RELY]
- STRUCT `struct mqtt_message_router`
  role: 消息路由器私有状态结构体
```c
struct mqtt_message_router;
```

- FUNC `mqtt_topic_tree_create`
  role: 创建主题订阅树
```c
mqtt_topic_tree_t* mqtt_topic_tree_create(void);
```

[GUARANTEE]
```c
mqtt_message_router_t* mqtt_message_router_create(mqtt_session_manager_t* sessions);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为会话管理器指针 sessions

**Post-Condition**:
- 成功返回路由器句柄，失败返回 NULL

**Invariant**:
- sessions 为非拥有引用
- 路由器销毁时必须释放 topics

**System Algorithm**:
- sessions 为空返回 NULL；分配路由器对象并保存 sessions；创建 topic_tree，失败则回滚释放并返回 NULL
