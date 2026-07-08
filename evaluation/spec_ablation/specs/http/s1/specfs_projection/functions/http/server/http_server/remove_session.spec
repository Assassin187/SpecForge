[PROMPT]
Implement function `remove_session`. Responsibility: 从 server 会话链表中按 fd 删除并销毁会话

[RELY]
- STRUCT `struct http_server`
  role: remove_session 读取或维护的 struct http_server 状态
```c
struct http_server;
```

- STRUCT `http_session_node_t`
  role: remove_session 读取或维护的 http_session_node_t 状态
```c
typedef struct http_session_node {
    http_session_t* session;
    http_session_node_t* next;
} http_session_node_t;
```

- FUNC `http_session_destroy`
  role: remove_session 调用 http_session_destroy 完成子步骤
```c
void http_session_destroy(http_session_t* session);
```

[GUARANTEE]
```c
static void remove_session(http_server_t* server, int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server 和 fd。

**Post-Condition**:
- 无返回值；副作用为指针的指针遍历链表→匹配后解除链接→http_session_destroy 销毁会话→free 节点。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- server 非空
- 匹配 fd 的节点从链表移除并释放内存；fd 未匹配时无副作用

**System Algorithm**:
- 指针的指针遍历链表→匹配后解除链接→http_session_destroy 销毁会话→free 节点。
