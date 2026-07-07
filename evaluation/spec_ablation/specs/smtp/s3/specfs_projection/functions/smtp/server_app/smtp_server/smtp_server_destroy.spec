[PROMPT]
Implement function `smtp_server_destroy`. Responsibility: 销毁所有 session、底层 TCP server 和 server 对象

[RELY]
- STRUCT `smtp_server_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_server smtp_server_t;
```

- STRUCT `smtp_session_node_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_session_node smtp_session_node_t;
```

- FUNC `smtp_session_destroy`
  role: 被该函数调用以完成子步骤
```c
void smtp_session_destroy(smtp_session_t* session);
```

- FUNC `smtp_tcp_server_destroy`
  role: 被该函数调用以完成子步骤
```c
void smtp_tcp_server_destroy(smtp_tcp_server_t* server);
```

[GUARANTEE]
```c
void smtp_server_destroy(smtp_server_t* server);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(smtp_server_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 无返回值；释放本对象持有的内存、连接、队列或子对象，NULL 输入安全返回。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- 销毁所有 session、底层 TCP server 和 server 对象。
