[PROMPT]
Implement function `find_client`. Responsibility: 按 fd 在线性 client 链表中查找节点

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
static smtp_client_node_t* find_client(smtp_tcp_server_t* server, int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(smtp_tcp_server_t*，可为 NULL，BORROWED)；fd(int，不可为 NULL，BORROWED)。

**Post-Condition**:
- 成功返回对象指针；分配、查找或初始化失败时返回 NULL。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 维护 server->clients：遍历入口

**System Algorithm**:
- 按 fd 在线性 client 链表中查找节点。
