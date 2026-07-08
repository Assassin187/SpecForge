[PROMPT]
Implement function `smtp_connection_queue`. Responsibility: 把待发送字节复制到输出缓冲队尾

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

[GUARANTEE]
```c
int smtp_connection_queue(smtp_connection_t* conn, const void* data, size_t len);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：conn(smtp_connection_t*，可为 NULL，BORROWED)；data(const void*，可为 NULL，BORROWED)；len(size_t，不可为 NULL，BORROWED)。

**Post-Condition**:
- 成功把数据追加到输出缓冲后返回 0；参数无效或扩容失败返回 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 维护 conn->out_buf：追加待发送字节
- 维护 conn->out_len：增加输出长度

**System Algorithm**:
- 把待发送字节复制到输出缓冲队尾。
