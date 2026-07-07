[PROMPT]
Implement function `mqtt_session_connection`. Responsibility: 返回会话绑定的底层连接对象

[RELY]
- STRUCT `struct mqtt_session`
  role: 会话私有实现结构
```c
struct mqtt_session;
```

[GUARANTEE]
```c
mqtt_connection_t* mqtt_session_connection(const mqtt_session_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 报文处理或连接控制逻辑需要访问连接句柄时触发
- Precondition: 允许 s 为空
- Input: 输入为会话指针 s

**Post-Condition**:
- State Change: 纯读取操作，不修改任何字段
- Response: 返回连接指针或 NULL

**Invariant**:
- None specified.

**System Algorithm**:
- 若 s 非空则返回 s->conn，否则返回 NULL
