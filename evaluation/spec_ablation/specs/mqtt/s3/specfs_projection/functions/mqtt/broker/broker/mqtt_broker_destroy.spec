[PROMPT]
Implement function `mqtt_broker_destroy`. Responsibility: 销毁 Broker 运行时对象并按依赖顺序回收网络、路由与会话资源

[RELY]
- STRUCT `struct mqtt_broker`
  role: Broker 运行时内部状态结构
```c
struct mqtt_broker;
```

- FUNC `mqtt_broker_stop`
  role: 先停止服务器运行循环
```c
void mqtt_broker_stop(mqtt_broker_t* b);
```

- FUNC `mqtt_tcp_server_destroy`
  role: 销毁网络服务器对象
```c
void mqtt_tcp_server_destroy(mqtt_tcp_server_t* s);
```

- FUNC `mqtt_message_router_destroy`
  role: 销毁消息路由器
```c
void mqtt_message_router_destroy(mqtt_message_router_t* r);
```

- FUNC `mqtt_session_manager_destroy`
  role: 销毁会话管理器
```c
void mqtt_session_manager_destroy(mqtt_session_manager_t* m);
```

[GUARANTEE]
```c
void mqtt_broker_destroy(mqtt_broker_t* b);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为 broker 指针 b（可为 NULL）

**Post-Condition**:
- 无返回值；调用后 b 指向对象的资源被全部回收

**Invariant**:
- 销毁顺序与创建依赖相反，避免悬挂引用
- NULL 输入不产生副作用

**System Algorithm**:
- 若 b 为空直接返回；否则先调用 mqtt_broker_stop，再销毁 tcp_server、router、session_manager，最后释放 b 本体
