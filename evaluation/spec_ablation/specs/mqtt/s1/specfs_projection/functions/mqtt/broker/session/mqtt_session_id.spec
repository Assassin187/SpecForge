[PROMPT]
Implement function `mqtt_session_id`. Responsibility: 返回会话对应的连接 fd 作为 session_id

[RELY]
- STRUCT `struct mqtt_session`
  role: 会话私有实现结构
```c
struct mqtt_session;
```

- FUNC `mqtt_connection_fd`
  role: 读取连接 fd 作为 session_id
```c
int mqtt_connection_fd(const mqtt_connection_t* c);
```

[GUARANTEE]
```c
int mqtt_session_id(const mqtt_session_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: broker 或会话管理器需要按 ID 定位会话时触发
- Precondition: 允许 s 或 s->conn 为空
- Input: 输入为会话指针 s

**Post-Condition**:
- State Change: 纯读取操作，不修改会话内部状态
- Response: 返回 >=0 的 fd 表示有效会话 ID，返回 -1 表示不可用

**Invariant**:
- None specified.

**System Algorithm**:
- 若 s 与 conn 均有效则调用 mqtt_connection_fd(s->conn)，否则返回 -1
