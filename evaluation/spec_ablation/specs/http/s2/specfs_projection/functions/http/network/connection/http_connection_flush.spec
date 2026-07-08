[PROMPT]
Implement function `http_connection_flush`. Responsibility: 非阻塞 send 刷写输出缓冲；全部发送返回 0，部分发送返回 1 通知上层注册 EPOLLOUT

[RELY]
- STRUCT `struct http_connection`
  role: 连接私有状态结构体
```c
struct http_connection;
```

- FUNC `send`
  role: 向 socket 发送原始字节
  declaration: external dependency; canonical declaration unavailable.

[GUARANTEE]
```c
int http_connection_flush(http_connection_t* conn);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：conn(http_connection_t*，不可为 NULL，BORROWED)。

**Post-Condition**:
- 0=全部发送完成，1=部分发送需重试，-1=错误。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 维护 conn->fd：作为 send 的 socket 参数来源
- 维护 conn->out_buf：从 out_off 偏移开始发送
- 维护 conn->out_off：随 send 成功推进；全部发送后归零
- 维护 conn->out_len：全部发送后归零

**System Algorithm**:
- 循环 send(conn->fd, out_buf+out_off, out_len-out_off, 0)；EINTR 重试；EAGAIN/EWOULDBLOCK 返回 1（部分发送，需注册 EPOLLOUT）；错误返回 -1；全部发送后 out_len=out_off=0 返回 0。
