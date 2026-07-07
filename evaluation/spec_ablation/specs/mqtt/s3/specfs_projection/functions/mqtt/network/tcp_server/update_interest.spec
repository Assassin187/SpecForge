[PROMPT]
Implement function `update_interest`. Responsibility: 根据连接是否有待发数据更新 epoll 关注事件（EPOLLIN/EPOLLOUT）

[RELY]
- STRUCT `struct mqtt_tcp_server`
  role: TCP 服务器私有状态结构体
```c
struct mqtt_tcp_server;
```

- FUNC `mqtt_connection_closed`
  role: 跳过已关闭连接
```c
bool mqtt_connection_closed(const mqtt_connection_t* c);
```

- FUNC `mqtt_connection_fd`
  role: 设置 epoll data.fd
```c
int mqtt_connection_fd(const mqtt_connection_t* c);
```

- FUNC `mqtt_connection_want_write`
  role: 决定是否订阅 EPOLLOUT
```c
bool mqtt_connection_want_write(const mqtt_connection_t* c);
```

[GUARANTEE]
```c
static void update_interest(mqtt_tcp_server_t* s, mqtt_connection_t* c);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为 TCP server 对象 s 与连接 c。

**Post-Condition**:
- s/c 为空或连接已关闭时直接返回；否则用 EPOLL_CTL_MOD 将事件更新为 EPOLLIN 或 EPOLLIN|EPOLLOUT。

**Invariant**:
- 连接对象所有权不变
- 是否监听 EPOLLOUT 仅由 mqtt_connection_want_write 决定

**System Algorithm**:
- 按连接是否待写组装 EPOLLIN/EPOLLOUT 事件并执行 epoll_ctl MOD。
