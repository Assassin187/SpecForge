[PROMPT]
Implement function `smtp_server_stop`. Responsibility: 请求底层 TCP server 停止事件循环

[RELY]
- STRUCT `smtp_server_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_server smtp_server_t;
```

- FUNC `smtp_tcp_server_stop`
  role: 被该函数调用以完成子步骤
```c
void smtp_tcp_server_stop(smtp_tcp_server_t* server);
```

[GUARANTEE]
```c
void smtp_server_stop(smtp_server_t* server);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(smtp_server_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 无返回值；对象有效时请求底层事件循环停止，NULL 输入不产生副作用。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。

**System Algorithm**:
- 请求底层 TCP server 停止事件循环。
