[PROMPT]
Implement function `remove_client_node`. Responsibility: 从 clients 链表移除 fd 节点并释放节点内存

[RELY]
- STRUCT `smtp_tcp_server_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_tcp_server smtp_tcp_server_t;
```

- STRUCT `smtp_client_node_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_client_node smtp_client_node_t;
```

[GUARANTEE]
```c
static void remove_client_node(smtp_tcp_server_t* server, int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(smtp_tcp_server_t*，可为 NULL，BORROWED)；fd(int，不可为 NULL，BORROWED)。

**Post-Condition**:
- 无返回值；副作用为从 clients 链表移除 fd 节点并释放节点内存。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。
- 维护 server->clients：删除匹配节点

**System Algorithm**:
- 从 clients 链表移除 fd 节点并释放节点内存。
