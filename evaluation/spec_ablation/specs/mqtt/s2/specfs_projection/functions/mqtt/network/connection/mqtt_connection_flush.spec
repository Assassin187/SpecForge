[PROMPT]
Implement function `mqtt_connection_flush`. Responsibility: 尝试发送输出队列中的待发字节

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
bool mqtt_connection_flush(mqtt_connection_t* c);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: EPOLLOUT 或事件循环主动推进写队列时触发
- Precondition: c 有效且未关闭
- Input: 输入为连接 c

**Post-Condition**:
- State Change: 输出队列可能缩短直至清空
- Response: 返回 true 表示无需强制关闭，false 表示写失败需关闭连接

**Invariant**:
- None specified.

**System Algorithm**:
- 循环 send 当前队头块：发送成功推进 off，块发送完则出队释放；EAGAIN/EWOULDBLOCK 返回 true；EINTR 重试；其他错误返回 false
