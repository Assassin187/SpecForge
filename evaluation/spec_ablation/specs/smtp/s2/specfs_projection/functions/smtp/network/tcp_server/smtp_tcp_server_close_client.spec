[PROMPT]
Implement function `smtp_tcp_server_close_client`. Responsibility: 关闭并移除客户端 fd，同步触发一次 on_close；callback 可能销毁上层 session

[RELY]
- STRUCT `smtp_tcp_server_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_tcp_server smtp_tcp_server_t;
```

- FUNC `find_client`
  role: 被该函数调用以完成子步骤
```c
static smtp_client_node_t* find_client(smtp_tcp_server_t* server, int fd);
```

- FUNC `epoll_ctl`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

- FUNC `close`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

- FUNC `remove_client_node`
  role: 被该函数调用以完成子步骤
```c
static void remove_client_node(smtp_tcp_server_t* server, int fd);
```

[GUARANTEE]
```c
int smtp_tcp_server_close_client(smtp_tcp_server_t* server, int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(smtp_tcp_server_t*，可为 NULL，BORROWED)；fd(int，不可为 NULL，BORROWED)。

**Post-Condition**:
- 成功完成关闭、移除和一次同步 on_close callback 后返回 0；server 无效、fd 不存在或关闭流程无法开始时返回 -1。重复关闭同一 fd 不得再次触发 on_close。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- on_close 在本函数返回前同步执行，callback 返回前上层与 fd 关联的 session 可能已经失效
- 调用方不得假设 close_client 返回后仍可访问任何由 on_close 管理的上层对象

**System Algorithm**:
- server 无效或 fd 不在 client 链表时返回 -1，且不得调用 on_close。找到 fd 后依次从 epoll 删除、关闭 fd、移除 client 节点，并在函数返回前同步调用一次 on_close(user, fd)；on_close 可能立即移除并销毁上层 session。
