[PROMPT]
Implement function `remove_client_node`. Responsibility: 从 TCP server 客户端链表中按 fd 删除并释放节点

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
static void remove_client_node(http_tcp_server_t* server, int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server 和 fd。

**Post-Condition**:
- 无返回值；通过副作用清理节点。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。

**System Algorithm**:
- 通过指针的指针遍历链表，匹配 fd 后解除链接并 free 节点。
