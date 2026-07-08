[PROMPT]
Implement function `http_connection_fd`. Responsibility: 返回连接的 socket fd；空指针或无效连接返回 -1

[RELY]
- STRUCT `struct http_connection`
  role: 连接私有状态结构体
```c
struct http_connection;
```

[GUARANTEE]
```c
int http_connection_fd(const http_connection_t* conn);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：conn(const http_connection_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 通过返回值反映 fd 或 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 维护 conn->fd：只读访问 socket 文件描述符

**System Algorithm**:
- 空安全检查：conn==NULL 返回 -1；否则返回 conn->fd。
