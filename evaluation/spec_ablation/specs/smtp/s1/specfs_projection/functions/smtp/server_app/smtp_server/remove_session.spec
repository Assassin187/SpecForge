[PROMPT]
Implement function `remove_session`. Responsibility: 按 fd 移除并销毁 session 链表节点

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

[GUARANTEE]
```c
static void remove_session(smtp_server_t* server, int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(smtp_server_t*，可为 NULL，BORROWED)；fd(int，不可为 NULL，BORROWED)。

**Post-Condition**:
- 无返回值；副作用为按 fd 移除并销毁 session 链表节点。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 维护 server->sessions：删除匹配节点

**System Algorithm**:
- 按 fd 移除并销毁 session 链表节点。
