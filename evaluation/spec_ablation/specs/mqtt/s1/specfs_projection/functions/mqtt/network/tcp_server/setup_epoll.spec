[PROMPT]
Implement function `setup_epoll`. Responsibility: 创建 epoll 实例并注册监听 fd 的 EPOLLIN 事件

[RELY]
- STRUCT `struct mqtt_tcp_server`
  role: TCP 服务器私有状态结构体
```c
struct mqtt_tcp_server;
```

[GUARANTEE]
```c
static bool setup_epoll(mqtt_tcp_server_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为 TCP server 对象 s，且 s->listen_fd 已是有效监听 fd。

**Post-Condition**:
- 成功返回 true，创建 epoll_fd 并注册 listen_fd 的 EPOLLIN；epoll_create1 或 epoll_ctl 失败返回 false。

**Invariant**:
- 成功路径 listen fd 由 epoll 监听
- 失败路径不设置 running=true

**System Algorithm**:
- 创建 epoll 实例并将 listen_fd 以 EPOLLIN 注册进去。
