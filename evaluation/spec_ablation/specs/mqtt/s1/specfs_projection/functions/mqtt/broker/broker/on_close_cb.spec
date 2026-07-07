[PROMPT]
Implement function `on_close_cb`. Responsibility: 连接关闭回调：移除路由订阅关系并从会话管理器删除会话

[RELY]
- STRUCT `struct mqtt_broker`
  role: Broker 运行时内部状态结构
```c
struct mqtt_broker;
```

- FUNC `mqtt_connection_fd`
  role: 读取关闭连接的会话 ID
```c
int mqtt_connection_fd(const mqtt_connection_t* c);
```

- FUNC `mqtt_message_router_remove_session`
  role: 清理该会话所有订阅
```c
void mqtt_message_router_remove_session(mqtt_message_router_t* r, int session_id);
```

- FUNC `mqtt_session_manager_remove`
  role: 从会话管理器删除会话
```c
void mqtt_session_manager_remove(mqtt_session_manager_t* m, int session_id);
```

[GUARANTEE]
```c
static void on_close_cb(void* user, mqtt_connection_t* c);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: tcp_server 检测到连接关闭或错误并执行 close_connection 时触发
- Precondition: user 对应 broker，上报连接 c 有效
- Input: 事件输入为 broker 上下文与关闭连接 c

**Post-Condition**:
- State Change: 订阅树与会话管理器中该 sid 的记录被移除
- Response: 无返回值；连接相关业务状态被回收

**Invariant**:
- None specified.

**System Algorithm**:
- 校验 b/c；取 sid=mqtt_connection_fd(c)；调用 mqtt_message_router_remove_session 清理订阅，再调用 mqtt_session_manager_remove 删除会话并打印关闭日志
