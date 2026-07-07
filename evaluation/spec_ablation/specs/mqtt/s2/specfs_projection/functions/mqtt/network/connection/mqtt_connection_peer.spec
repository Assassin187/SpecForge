[PROMPT]
Implement function `mqtt_connection_peer`. Responsibility: 读取连接对端地址字符串

[RELY]
- STRUCT `struct mqtt_connection`
  role: 连接私有状态结构体
```c
struct mqtt_connection;
```

[GUARANTEE]
```c
const char* mqtt_connection_peer(const mqtt_connection_t* c);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 日志、审计或调试输出需要对端标识时触发
- Precondition: 允许 c 为空或 peer 未设置
- Input: 输入为连接指针 c

**Post-Condition**:
- State Change: 只读，无状态修改
- Response: 返回非 NULL 字符串指针

**Invariant**:
- None specified.

**System Algorithm**:
- 有 peer 返回其字符串，否则返回空字符串
