[PROMPT]
Implement function `smtp_server_run`. Responsibility: 进入底层 TCP server 事件循环

[RELY]
- STRUCT `smtp_server_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_server smtp_server_t;
```

- FUNC `smtp_tcp_server_run`
  role: 被该函数调用以完成子步骤
```c
int smtp_tcp_server_run(smtp_tcp_server_t* server);
```

[GUARANTEE]
```c
int smtp_server_run(smtp_server_t* server);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 应用启动后调用；委托 smtp_tcp_server_run，后续 accept/event/close 由注册回调驱动。
- Precondition: 输入参数：server(smtp_server_t*，可为 NULL，BORROWED)；遵守单线程事件循环调用约束。
- Input: 输入参数：server(smtp_server_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- State Change: 可能更新与应用启动后调用；委托 smtp_tcp_server_run，后续 accept/event/close 由注册回调驱动相关的运行时状态；事件可能产生状态副作用，不承诺幂等。
- Response: 返回 smtp_tcp_server_run 的结果；server 为 NULL 时返回 -1。

**Invariant**:
- None specified.

**System Algorithm**:
- 应用启动后调用；委托 smtp_tcp_server_run，后续 accept/event/close 由注册回调驱动。
