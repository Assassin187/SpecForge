[PROMPT]
Implement function `http_connection_buffered`. Responsibility: 返回输入缓冲当前可读字节数；空指针返回 0

[RELY]
- STRUCT `struct http_connection`
  role: 连接私有状态结构体
```c
struct http_connection;
```

[GUARANTEE]
```c
size_t http_connection_buffered(const http_connection_t* conn);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：conn(const http_connection_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 通过返回值反映可读字节数。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。
- 维护 conn->in_len：只读返回当前缓冲有效长度

**System Algorithm**:
- 空安全检查后返回 conn->in_len。
