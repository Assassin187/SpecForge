[PROMPT]
Implement function `mqtt_connection_in_len`. Responsibility: 返回输入缓冲当前有效长度

[RELY]
- STRUCT `struct mqtt_connection`
  role: 连接私有状态结构体
```c
struct mqtt_connection;
```

[GUARANTEE]
```c
size_t mqtt_connection_in_len(const mqtt_connection_t* c);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 上层判断是否存在可解码字节时触发
- Precondition: 允许 c 为空
- Input: 输入为连接 c

**Post-Condition**:
- State Change: 只读，无状态修改
- Response: 返回可读字节数

**Invariant**:
- None specified.

**System Algorithm**:
- c 非空返回 in_len，否则返回 0
