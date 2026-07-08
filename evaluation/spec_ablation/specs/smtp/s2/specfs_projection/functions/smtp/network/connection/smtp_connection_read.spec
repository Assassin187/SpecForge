[PROMPT]
Implement function `smtp_connection_read`. Responsibility: 从非阻塞 socket 尽可能读取字节追加到输入缓冲

[RELY]
- STRUCT `smtp_connection_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_connection smtp_connection_t;
```

- FUNC `ensure_cap`
  role: 被该函数调用以完成子步骤
```c
static int ensure_cap(uint8_t** buf, size_t* cap, size_t needed);
```

- FUNC `recv`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

[GUARANTEE]
```c
int smtp_connection_read(smtp_connection_t* conn);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：conn(smtp_connection_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 返回本轮读取字节数；对端关闭且无已读数据返回 0，无新数据返回 -2，错误返回 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 维护 conn->in_buf：追加收到的字节
- 维护 conn->in_len：增加输入长度

**System Algorithm**:
- 循环 recv；EINTR 重试，EAGAIN/EWOULDBLOCK 在无新数据时返回 -2，对端关闭返回 0，错误返回 -1。
