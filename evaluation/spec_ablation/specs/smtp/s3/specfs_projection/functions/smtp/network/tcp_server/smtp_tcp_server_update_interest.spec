[PROMPT]
Implement function `smtp_tcp_server_update_interest`. Responsibility: 按 want_write 修改客户端 epoll interest 中的 EPOLLOUT

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

- FUNC `find_client`
  role: 被该函数调用以完成子步骤
```c
static smtp_client_node_t* find_client(smtp_tcp_server_t* server, int fd);
```

- FUNC `epoll_ctl`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

[GUARANTEE]
```c
int smtp_tcp_server_update_interest(smtp_tcp_server_t* server, int fd, bool want_write);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(smtp_tcp_server_t*，可为 NULL，BORROWED)；fd(int，不可为 NULL，BORROWED)；want_write(bool，不可为 NULL，BORROWED)。

**Post-Condition**:
- 返回值表达按 want_write 修改客户端 epoll interest 中的 EPOLLOUT的结果；成功、失败和特殊分支按当前 C 实现的返回码区分。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。
- 维护 node->want_write：记录当前写兴趣

**System Algorithm**:
- 按 want_write 修改客户端 epoll interest 中的 EPOLLOUT。
