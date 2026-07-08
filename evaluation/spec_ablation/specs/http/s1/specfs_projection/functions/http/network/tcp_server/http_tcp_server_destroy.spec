[PROMPT]
Implement function `http_tcp_server_destroy`. Responsibility: 关闭所有客户端连接、关闭 listen_fd 与 epoll_fd、释放 server；空指针安全

[RELY]
- STRUCT `struct http_tcp_server`
  role: TCP server 私有状态
```c
struct http_tcp_server;
```

- STRUCT `http_client_node_t`
  role: 客户端链表节点
```c
typedef struct http_client_node http_client_node_t;
```

[GUARANTEE]
```c
void http_tcp_server_destroy(http_tcp_server_t* server);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server 指针（可为空）。

**Post-Condition**:
- 无返回值，调用后对象失效。

**Invariant**:
- 销毁后不残留任何 fd 或内存资源
- NULL 输入不产生副作用
- 重复调用安全（幂等）
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。

**System Algorithm**:
- 空安全检查→遍历关闭所有客户端(fd close+free)→关闭 listen_fd→关闭 epoll_fd→free(server)。
