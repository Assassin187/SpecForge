[PROMPT]
Implement function `smtp_connection_flush`. Responsibility: 尝试把输出缓冲剩余字节发送到 socket

[RELY]
- STRUCT `smtp_connection_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_connection smtp_connection_t;
```

- FUNC `send`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

[GUARANTEE]
```c
int smtp_connection_flush(smtp_connection_t* conn);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：conn(smtp_connection_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 返回 0 表示当前队列已刷完，返回 1 表示仍需等待可写，返回 -1 表示发送错误。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。
- 维护 conn->out_buf：发送源缓冲
- 维护 conn->out_off：随 send 成功推进
- 维护 conn->out_len：全部发送后清零

**System Algorithm**:
- 循环 send；EINTR 重试，EAGAIN/EWOULDBLOCK 返回 1 表示仍需 EPOLLOUT，全部发送后 out_len/out_off 归零。
