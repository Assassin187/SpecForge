[PROMPT]
Implement function `on_close_cb`. Responsibility: TCP close 回调：从 server session 链表移除 fd 对应 session

[RELY]
- STRUCT `smtp_server_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_server smtp_server_t;
```

- FUNC `remove_session`
  role: 被该函数调用以完成子步骤
```c
static void remove_session(smtp_server_t* server, int fd);
```

[GUARANTEE]
```c
static void on_close_cb(void* user, int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: tcp_server 关闭客户端后调用，释放对应 SMTP session。
- Precondition: 输入参数：user(void*，可为 NULL，BORROWED)；fd(int，不可为 NULL，BORROWED)；遵守单线程事件循环调用约束。
- Input: 输入参数：user(void*，可为 NULL，BORROWED)；fd(int，不可为 NULL，BORROWED)。

**Post-Condition**:
- State Change: 从连接/session 链表中移除对应节点并释放其关联资源；事件可能产生状态副作用，不承诺幂等。
- Response: 无直接返回值；关闭 fd 后移除并释放对应 session/connection 状态。

**Invariant**:
- None specified.

**System Algorithm**:
- tcp_server 关闭客户端后调用，释放对应 SMTP session。
