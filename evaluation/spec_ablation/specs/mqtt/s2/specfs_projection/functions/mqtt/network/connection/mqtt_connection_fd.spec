[PROMPT]
Implement function `mqtt_connection_fd`. Responsibility: 读取连接当前文件描述符

[RELY]
- STRUCT `struct mqtt_connection`
  role: 连接私有状态结构体
```c
struct mqtt_connection;
```

[GUARANTEE]
```c
int mqtt_connection_fd(const mqtt_connection_t* c);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 上层需要按 fd 注册 epoll、定位连接或生成 session_id 时触发
- Precondition: 允许 c 为空
- Input: 输入为连接指针 c

**Post-Condition**:
- State Change: 只读，无状态修改
- Response: 返回 fd 或 -1

**Invariant**:
- None specified.

**System Algorithm**:
- c 非空返回 c->fd，否则返回 -1
