[PROMPT]
Implement function `http_connection_create`. Responsibility: 为已建立的 socket fd 分配并初始化连接对象，设置 fd、清零缓冲字段

[RELY]
None.

[GUARANTEE]
```c
http_connection_t* http_connection_create(int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：fd(int，不可为 NULL，BORROWED)。

**Post-Condition**:
- 成功返回连接对象指针，失败返回 NULL；返回动态分配的对象，调用方必须通过 http_connection_destroy 释放。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 维护 conn->fd：写入 socket 文件描述符
- 维护 conn->in_buf：初始化为 NULL
- 维护 conn->in_len：初始化为 0
- 维护 conn->in_cap：初始化为 0
- 维护 conn->out_buf：初始化为 NULL
- 维护 conn->out_len：初始化为 0
- 维护 conn->out_off：初始化为 0
- 维护 conn->out_cap：初始化为 0

**System Algorithm**:
- calloc 分配 http_connection 结构体，设置 fd 字段，初始化 in_buf/out_buf 等缓冲字段为 NULL/0。
