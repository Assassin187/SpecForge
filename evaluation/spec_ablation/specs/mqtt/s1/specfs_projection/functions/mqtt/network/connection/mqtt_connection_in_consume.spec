[PROMPT]
Implement function `mqtt_connection_in_consume`. Responsibility: 消费输入缓冲前缀字节并整理剩余数据

[RELY]
- STRUCT `struct mqtt_connection`
  role: 连接私有状态结构体
```c
struct mqtt_connection;
```

[GUARANTEE]
```c
void mqtt_connection_in_consume(mqtt_connection_t* c, size_t n);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 解码器报告已消费字节后触发
- Precondition: c 有效且 n>0
- Input: 输入为连接 c 和消费长度 n

**Post-Condition**:
- State Change: 输入缓冲有效窗口前移并缩短
- Response: 无返回值

**Invariant**:
- None specified.

**System Algorithm**:
- n>=in_len 时将 in_len 置 0；否则 memmove 剩余字节到缓冲起始并更新 in_len
