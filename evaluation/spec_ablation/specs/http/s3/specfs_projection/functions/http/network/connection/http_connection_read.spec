[PROMPT]
Implement function `http_connection_read`. Responsibility: 非阻塞循环 recv 读取 socket 数据并追加到输入缓冲；正确处理 EAGAIN/EINTR/对端关闭

[RELY]
- STRUCT `struct http_connection`
  role: 连接私有状态结构体
```c
struct http_connection;
```

- FUNC `ensure_cap`
  role: 确保输入缓冲容量充足
```c
static int ensure_cap(uint8_t** buf, size_t* cap, size_t needed);
```

- FUNC `recv`
  role: 从 socket 读取原始字节
  declaration: external dependency; canonical declaration unavailable.

[GUARANTEE]
```c
int http_connection_read(http_connection_t* conn);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：conn(http_connection_t*，不可为 NULL，BORROWED)。

**Post-Condition**:
- 返回总读取字节数；无新数据且 EAGAIN 返回 -2；错误返回 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。
- 维护 conn->fd：作为 recv 的 socket 参数来源
- 维护 conn->in_buf：写入接收数据的目标缓冲
- 维护 conn->in_len：随每次成功 recv 递增
- 维护 conn->in_cap：容量不足时由 ensure_cap 更新

**System Algorithm**:
- 使用 4096 字节栈缓冲区循环 recv；EINTR 时重试；EAGAIN/EWOULDBLOCK 时若无已读数据返回 -2；对端关闭返回已读字节数；错误返回 -1。每次成功读取后调用 ensure_cap 扩容并 memcpy 追加到输入缓冲。
