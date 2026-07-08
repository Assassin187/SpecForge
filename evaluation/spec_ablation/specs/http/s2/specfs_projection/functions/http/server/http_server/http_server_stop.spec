[PROMPT]
Implement function `http_server_stop`. Responsibility: 委托 http_tcp_server_stop 停止事件循环

[RELY]
- FUNC `http_tcp_server_stop`
  role: http_server_stop 调用 http_tcp_server_stop 完成子步骤
```c
void http_tcp_server_stop(http_tcp_server_t* server);
```

[GUARANTEE]
```c
void http_server_stop(http_server_t* server);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(http_server_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 无返回值；对象有效时请求底层事件循环停止，NULL 输入不产生副作用。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- server 非空且 server->tcp 已初始化
- TCP server 运行标志置为 false，事件线程将退出循环

**System Algorithm**:
- 空安全检查→委托 http_tcp_server_stop。
