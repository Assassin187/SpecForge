[PROMPT]
Implement function `smtp_tcp_server_destroy`. Responsibility: 关闭所有客户端、监听 fd、epoll fd 并释放 server

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

- FUNC `close`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

[GUARANTEE]
```c
void smtp_tcp_server_destroy(smtp_tcp_server_t* server);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(smtp_tcp_server_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 无返回值；释放本对象持有的内存、连接、队列或子对象，NULL 输入安全返回。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- 关闭所有客户端、监听 fd、epoll fd 并释放 server。
