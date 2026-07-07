[PROMPT]
Implement function `find_session`. Responsibility: 在 server 会话链表中按 fd 查找会话

[RELY]
- STRUCT `struct http_server`
  role: HTTP server 私有状态
```c
struct http_server;
```

- STRUCT `http_session_node_t`
  role: 会话链表节点
```c
typedef struct http_session_node {
    http_session_t* session;
    http_session_node_t* next;
} http_session_node_t;
```

[GUARANTEE]
```c
static http_session_t* find_session(http_server_t* server, int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server 和 fd。

**Post-Condition**:
- 成功返回对象指针；分配、查找或初始化失败时返回 NULL。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- server 非空；fd 为有效文件描述符
- 返回匹配 fd 的会话指针，未找到时返回 NULL

**System Algorithm**:
- 遍历 server->sessions 链表，比较 session->fd 匹配则返回 session 指针。
