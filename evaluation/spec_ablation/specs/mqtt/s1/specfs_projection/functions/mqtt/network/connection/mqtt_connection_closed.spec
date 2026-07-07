[PROMPT]
Implement function `mqtt_connection_closed`. Responsibility: 判断连接是否已关闭

[RELY]
- STRUCT `struct mqtt_connection`
  role: 连接私有状态结构体
```c
struct mqtt_connection;
```

[GUARANTEE]
```c
bool mqtt_connection_closed(const mqtt_connection_t* c);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 事件循环在读写前进行连接有效性检查时触发
- Precondition: 允许 c 为空
- Input: 输入为连接指针 c

**Post-Condition**:
- State Change: 只读，无状态修改
- Response: 返回连接关闭状态

**Invariant**:
- None specified.

**System Algorithm**:
- c 非空返回 closed 标志；空指针返回 true
