[PROMPT]
Implement function `close_connection`. Responsibility: 统一连接关闭流程：epoll 删除、触发 on_close 回调、再移除连接

[RELY]
- STRUCT `struct mqtt_tcp_server`
  role: TCP 服务器私有状态结构体
```c
struct mqtt_tcp_server;
```

- FUNC `mqtt_connection_fd`
  role: 读取连接 fd
```c
int mqtt_connection_fd(const mqtt_connection_t* c);
```

- FUNC `remove_conn`
  role: 从链表移除并销毁连接
```c
static void remove_conn(mqtt_tcp_server_t* s, int fd);
```

[GUARANTEE]
```c
static void close_connection(mqtt_tcp_server_t* s, mqtt_connection_t* c);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为 TCP server 对象 s 与待关闭连接 c。

**Post-Condition**:
- s 或 c 为空时直接返回；否则删除 epoll interest，先触发 on_close 回调，再从链表移除并销毁连接。

**Invariant**:
- on_close 在连接销毁前触发
- 连接最终通过 remove_conn 统一释放

**System Algorithm**:
- 先从 epoll 删除 fd，再触发 on_close 回调，最后调用 remove_conn 清理对象。
