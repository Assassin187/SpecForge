[PROMPT]
Implement function `http_tcp_server_close_client`. Responsibility: 从 epoll 删除 fd、close socket、从链表删除节点并触发 on_close 回调

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

- FUNC `find_client`
  role: 按 fd 定位客户端节点
```c
static http_client_node_t* find_client(http_tcp_server_t* server, int fd);
```

- FUNC `remove_client_node`
  role: 从链表移除并释放节点
```c
static void remove_client_node(http_tcp_server_t* server, int fd);
```

[GUARANTEE]
```c
int http_tcp_server_close_client(http_tcp_server_t* server, int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server 和 fd。

**Post-Condition**:
- 成功返回 0，未找到返回 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。

**System Algorithm**:
- find_client 查找→epoll_ctl EPOLL_CTL_DEL→close(fd)→remove_client_node→on_close 回调。
