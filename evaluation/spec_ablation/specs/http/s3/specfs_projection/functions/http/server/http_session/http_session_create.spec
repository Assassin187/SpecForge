[PROMPT]
Implement function `http_session_create`. Responsibility: 为已 accept 的 fd 分配会话对象并创建底层连接对象；任一失败清理并返回 NULL

[RELY]
- STRUCT `http_session_t`
  role: 会话私有实现结构
```c
typedef struct http_session {
    int fd;
    http_connection_t* conn;
} http_session_t;
```

- FUNC `http_connection_create`
  role: 创建底层连接对象
```c
http_connection_t* http_connection_create(int fd);
```

[GUARANTEE]
```c
http_session_t* http_session_create(int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入已 accept 的 fd。

**Post-Condition**:
- 成功返回 session 指针；失败返回 NULL；成功返回动态分配的会话对象，调用方必须通过 http_session_destroy 释放。

**Invariant**:
- session->fd 必须设置为入参 fd
- session->conn 必须指向新创建的连接对象
- 任一分配失败时必须清理已分配资源
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- fd 为已 accept 的有效 socket 文件描述符；并发访问需由上层同步控制
- 成功返回有效的 http_session_t*，其 fd 字段设置为入参 fd，conn 字段指向新分配的连接对象

**System Algorithm**:
- calloc 分配 session→http_connection_create 创建连接对象→设置 session->fd 和 session->conn。任一失败则 free(session) 返回 NULL。
