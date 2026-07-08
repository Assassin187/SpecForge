[PROMPT]
Implement function `http_server_run`. Responsibility: 委托 http_tcp_server_run 进入事件循环

[RELY]
- FUNC `http_tcp_server_run`
  role: http_server_run 调用 http_tcp_server_run 完成子步骤
```c
int http_tcp_server_run(http_tcp_server_t* server);
```

[GUARANTEE]
```c
int http_server_run(http_server_t* server);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(http_server_t*，不可为 NULL，BORROWED)。

**Post-Condition**:
- 返回 http_tcp_server_run 的结果；server 为 NULL 时返回 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- server 非空；http_server_start 已成功调用
- 事件循环运行至 http_server_stop 被调用后退出，正常退出返回 0

**System Algorithm**:
- 委托 http_tcp_server_run。
