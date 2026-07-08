[PROMPT]
Implement function `smtp_server_start`. Responsibility: 启动底层 TCP server 监听

[RELY]
- STRUCT `smtp_server_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_server smtp_server_t;
```

- FUNC `smtp_tcp_server_start`
  role: 被该函数调用以完成子步骤
```c
int smtp_tcp_server_start(smtp_tcp_server_t* server);
```

[GUARANTEE]
```c
int smtp_server_start(smtp_server_t* server);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(smtp_server_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 成功返回底层启动结果 0/true；参数无效或底层启动失败返回 -1/false。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。

**System Algorithm**:
- 启动底层 TCP server 监听。
