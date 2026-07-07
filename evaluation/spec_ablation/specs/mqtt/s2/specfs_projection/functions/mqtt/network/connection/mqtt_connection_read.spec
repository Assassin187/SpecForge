[PROMPT]
Implement function `mqtt_connection_read`. Responsibility: 非阻塞读取 socket 数据并追加到输入缓冲

[RELY]
- STRUCT `struct mqtt_connection`
  role: 连接私有状态结构体
```c
struct mqtt_connection;
```

- FUNC `ensure_in_cap`
  role: 扩容输入缓冲以追加新字节
```c
static bool ensure_in_cap(mqtt_connection_t* c, size_t need);
```

[GUARANTEE]
```c
bool mqtt_connection_read(mqtt_connection_t* c, bool* peer_closed);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: epoll 产生 EPOLLIN 时由事件循环触发
- Precondition: c 有效且未关闭；peer_closed 可为空
- Input: 输入为连接 c 与 peer_closed 输出标志

**Post-Condition**:
- State Change: 输入缓冲 in_data/in_len 可能增长；peer_closed 可能被置位；peer_closed=true 与 in_len>0 可以同时成立
- Response: 返回 true 表示读取阶段可继续处理，调用者必须先处理所有 buffered input，再根据 peer_closed 关闭连接；false 表示致命错误需关闭连接

**Invariant**:
- None specified.

**System Algorithm**:
- 循环读取：每轮读取前先确保输入缓冲有正数可写空间，然后调用真实缓冲区 recv(c->fd, c->in_data + c->in_len, writable, MSG_DONTWAIT)；读到数据则追加到 in_len；EAGAIN/EWOULDBLOCK 返回 true；EINTR 重试；只有真实读取调用返回 n=0 时才标记 peer_closed=true 后返回 true，不丢弃本次及此前已追加的数据；其他错误返回 false
