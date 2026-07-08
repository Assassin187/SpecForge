[PROMPT]
Implement function `add_session`. Responsibility: 将新会话节点头插法加入 server 会话链表

[RELY]
- STRUCT `struct http_server`
  role: add_session 读取或维护的 struct http_server 状态
```c
struct http_server;
```

- STRUCT `http_session_node_t`
  role: add_session 读取或维护的 http_session_node_t 状态
```c
typedef struct http_session_node {
    http_session_t* session;
    http_session_node_t* next;
} http_session_node_t;
```

[GUARANTEE]
```c
static int add_session(http_server_t* server, http_session_t* session);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server 和 session。

**Post-Condition**:
- 返回值表达calloc 分配节点→设置 session→头插法加入 server->sessions 链表→失败返回 -1的结果；成功、失败和特殊分支按当前 C 实现的返回码区分。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- server 和 session 均非空
- session 包装为新节点插入 sessions 链表头部

**System Algorithm**:
- calloc 分配节点→设置 session→头插法加入 server->sessions 链表→失败返回 -1。
