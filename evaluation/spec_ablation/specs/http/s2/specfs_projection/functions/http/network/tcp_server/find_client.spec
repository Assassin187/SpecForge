[PROMPT]
Implement function `find_client`. Responsibility: 在 TCP server 的客户端单向链表中按 fd 查找节点

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
static http_client_node_t* find_client(http_tcp_server_t* server, int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server 和 fd。

**Post-Condition**:
- 找到返回节点指针，未找到返回 NULL。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。

**System Algorithm**:
- 遍历 server->clients 链表，比较每个节点的 fd 字段，匹配则返回节点指针。
