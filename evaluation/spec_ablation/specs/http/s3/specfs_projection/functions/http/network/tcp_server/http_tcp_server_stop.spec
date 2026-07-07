[PROMPT]
Implement function `http_tcp_server_stop`. Responsibility: 置 running=false 通知事件循环退出；空指针安全

[RELY]
- STRUCT `struct http_tcp_server`
  role: TCP server 私有状态
```c
struct http_tcp_server;
```

[GUARANTEE]
```c
void http_tcp_server_stop(http_tcp_server_t* server);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server 指针（可为空）。

**Post-Condition**:
- 无返回值；事件循环收到信号后退出。

**Invariant**:
- NULL 输入不产生副作用
- 重复调用安全（幂等）
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- 空安全检查后设置 server->running = false。
