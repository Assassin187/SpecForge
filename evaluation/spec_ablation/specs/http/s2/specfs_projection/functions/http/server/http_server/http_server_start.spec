[PROMPT]
Implement function `http_server_start`. Responsibility: 委托 http_tcp_server_start 启动底层 TCP server

[RELY]
- FUNC `http_tcp_server_start`
  role: http_server_start 调用 http_tcp_server_start 完成子步骤
```c
int http_tcp_server_start(http_tcp_server_t* server);
```

[GUARANTEE]
```c
int http_server_start(http_server_t* server);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(http_server_t*，不可为 NULL，BORROWED)。

**Post-Condition**:
- 成功返回底层启动结果 0/true；参数无效或底层启动失败返回 -1/false。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- server 非空且 server->tcp 已通过 http_server_create 初始化
- 成功返回 0；socket/bind/epoll 失败时返回 -1 并设置 errno

**System Algorithm**:
- 委托 http_tcp_server_start。
