[PROMPT]
Implement function `setup_listener`. Responsibility: 创建、设置非阻塞、bind 并 listen TCP socket

[RELY]
- STRUCT `smtp_tcp_server_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_tcp_server smtp_tcp_server_t;
```

- FUNC `socket`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

- FUNC `setsockopt`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

- FUNC `set_nonblocking`
  role: 被该函数调用以完成子步骤
```c
static int set_nonblocking(int fd);
```

- FUNC `bind`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

- FUNC `listen`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

[GUARANTEE]
```c
static int setup_listener(smtp_tcp_server_t* server);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(smtp_tcp_server_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 返回值表达创建、设置非阻塞、bind 并 listen TCP socket的结果；成功、失败和特殊分支按当前 C 实现的返回码区分。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 维护 server->listen_fd：写入监听 fd

**System Algorithm**:
- 创建、设置非阻塞、bind 并 listen TCP socket。
