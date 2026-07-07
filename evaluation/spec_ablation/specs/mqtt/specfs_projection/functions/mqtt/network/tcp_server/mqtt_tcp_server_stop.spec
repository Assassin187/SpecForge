[PROMPT]
Implement function `mqtt_tcp_server_stop`. Responsibility: 停止事件循环并关闭所有连接及服务器 fd

[RELY]
- STRUCT `struct mqtt_tcp_server`
  role: TCP 服务器私有状态结构体
```c
struct mqtt_tcp_server;
```

- FUNC `close_connection`
  role: 逐个关闭并移除连接
```c
static void close_connection(mqtt_tcp_server_t* s, mqtt_connection_t* c);
```

[GUARANTEE]
```c
void mqtt_tcp_server_stop(mqtt_tcp_server_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为服务器指针 s（可为空）

**Post-Condition**:
- 无返回值；服务器退出运行态

**Invariant**:
- stop 后不应残留活动连接节点
- fd 关闭后统一重置为 -1，便于幂等调用

**System Algorithm**:
- 置 running=false；循环关闭并移除所有连接；关闭 epoll_fd 与 listen_fd 并重置为 -1
