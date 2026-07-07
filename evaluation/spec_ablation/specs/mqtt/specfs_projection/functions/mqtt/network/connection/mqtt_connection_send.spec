[PROMPT]
Implement function `mqtt_connection_send`. Responsibility: 将待发送数据复制入输出队列

[RELY]
- STRUCT `struct mqtt_connection`
  role: 连接私有状态结构体
```c
struct mqtt_connection;
```

- STRUCT `struct out_chunk`
  role: 输出队列块结构体
```c
struct out_chunk;
```

[GUARANTEE]
```c
void mqtt_connection_send(mqtt_connection_t* c, const uint8_t* data, size_t len);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 上层协议处理需要向客户端回包时触发
- Precondition: c 有效且未关闭；data 非空且 len>0
- Input: 输入为连接 c、数据指针 data 与长度 len

**Post-Condition**:
- State Change: 输出队列新增一个待发送块
- Response: 无返回值；失败时静默丢弃本次入队请求

**Invariant**:
- None specified.

**System Algorithm**:
- 分配 out_chunk 与数据副本并复制 payload，随后挂到 out_tail（空队列时同时更新 out_head）
