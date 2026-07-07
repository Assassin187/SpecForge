[PROMPT]
Implement function `on_accept_cb`. Responsibility: 新连接接入回调：创建会话并注册到会话管理器

[RELY]
- STRUCT `struct mqtt_broker`
  role: Broker 运行时内部状态结构
```c
struct mqtt_broker;
```

- STRUCT `mqtt_session_t`
  role: 新建会话对象
```c
typedef struct mqtt_session mqtt_session_t;
```

- FUNC `mqtt_session_create`
  role: 为新连接创建会话
```c
mqtt_session_t* mqtt_session_create(mqtt_connection_t* conn);
```

- FUNC `mqtt_connection_close`
  role: 会话创建失败时关闭连接
```c
void mqtt_connection_close(mqtt_connection_t* c);
```

- FUNC `mqtt_session_manager_add`
  role: 注册新会话到管理器
```c
void mqtt_session_manager_add(mqtt_session_manager_t* m, mqtt_session_t* s);
```

- FUNC `mqtt_connection_peer`
  role: 读取对端地址用于日志
```c
const char* mqtt_connection_peer(const mqtt_connection_t* c);
```

- FUNC `mqtt_connection_fd`
  role: 读取连接 fd 用于日志
```c
int mqtt_connection_fd(const mqtt_connection_t* c);
```

[GUARANTEE]
```c
static void on_accept_cb(void* user, mqtt_connection_t* c);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: tcp_server 在 accept 到客户端连接后触发
- Precondition: user 可转换为 mqtt_broker_t，连接 c 有效
- Input: 事件输入为 broker 上下文 user 与新连接 c

**Post-Condition**:
- State Change: 会话管理器新增一个未连接状态的 session
- Response: 无返回值；失败场景通过关闭连接进行快速回收

**Invariant**:
- None specified.

**System Algorithm**:
- 校验 b/c；调用 mqtt_session_create(c) 创建会话，失败则关闭连接；成功后调用 mqtt_session_manager_add 注册会话并输出接入日志
