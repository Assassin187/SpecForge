[PROMPT]
Implement function `mqtt_broker_create`. Responsibility: 创建 Broker 实例并完成会话管理器、消息路由器与 TCP 服务端的初始化链路

[RELY]
- STRUCT `struct mqtt_broker`
  role: Broker 运行时内部状态结构
```c
struct mqtt_broker;
```

- FUNC `mqtt_session_manager_create`
  role: 创建会话管理器
```c
mqtt_session_manager_t* mqtt_session_manager_create(void);
```

- FUNC `mqtt_message_router_create`
  role: 创建消息路由器
```c
mqtt_message_router_t* mqtt_message_router_create(mqtt_session_manager_t* sessions);
```

- FUNC `mqtt_tcp_server_create`
  role: 创建 TCP 服务器并绑定回调
```c
mqtt_tcp_server_t* mqtt_tcp_server_create(uint16_t port, mqtt_tcp_callbacks_t cb, void* user);
```

- FUNC `mqtt_message_router_destroy`
  role: 初始化失败时回滚销毁路由器
```c
void mqtt_message_router_destroy(mqtt_message_router_t* r);
```

- FUNC `mqtt_session_manager_destroy`
  role: 初始化失败时回滚销毁会话管理器
```c
void mqtt_session_manager_destroy(mqtt_session_manager_t* m);
```

[GUARANTEE]
```c
mqtt_broker_t* mqtt_broker_create(uint16_t port);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为监听端口 port

**Post-Condition**:
- 成功返回可用 broker 句柄，失败返回 NULL

**Invariant**:
- b->sessions 与 b->router 仅在 broker 存活期内有效
- 失败路径必须无资源泄漏且不暴露半初始化对象

**System Algorithm**:
- 先分配 mqtt_broker_t；随后依次创建 session_manager、router、TCP 回调表与 tcp_server；任一步失败都按逆序释放已创建资源并返回 NULL
