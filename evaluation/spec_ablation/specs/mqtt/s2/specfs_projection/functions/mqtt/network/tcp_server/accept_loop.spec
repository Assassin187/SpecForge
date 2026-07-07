[PROMPT]
Implement function `accept_loop`. Responsibility: 批量 accept 新连接并完成非阻塞设置、连接对象创建、epoll 注册与 on_accept 回调

[RELY]
- STRUCT `struct mqtt_tcp_server`
  role: TCP 服务器私有状态结构体
```c
struct mqtt_tcp_server;
```

- STRUCT `struct conn_node`
  role: 连接链表节点
```c
struct conn_node;
```

- FUNC `set_nonblocking`
  role: 设置客户端 socket 非阻塞
```c
static bool set_nonblocking(int fd);
```

- FUNC `mqtt_connection_create`
  role: 创建连接对象
```c
mqtt_connection_t* mqtt_connection_create(int fd);
```

- FUNC `sockaddr_to_string`
  role: 格式化对端地址
```c
static char* sockaddr_to_string(const struct sockaddr_in* addr);
```

- FUNC `mqtt_connection_set_peer`
  role: 设置连接 peer 字段
```c
void mqtt_connection_set_peer(mqtt_connection_t* c, const char* peer);
```

- FUNC `close_connection`
  role: epoll 注册失败时走统一关闭流程
```c
static void close_connection(mqtt_tcp_server_t* s, mqtt_connection_t* c);
```

[GUARANTEE]
```c
static void accept_loop(mqtt_tcp_server_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为已启动监听和 epoll 的 TCP server 对象 s。

**Post-Condition**:
- 持续 accept 直到 EAGAIN/EWOULDBLOCK 或不可恢复错误；成功接入的连接会非阻塞化、创建 connection、登记 peer、加入链表和 epoll，并在回调存在时触发 on_accept。

**Invariant**:
- 创建失败的 client_fd 会被关闭或释放
- on_accept 只在连接已加入 server 管理后触发

**System Algorithm**:
- 循环 accept 新连接；对每个连接设置非阻塞、创建连接对象、登记 peer、加入链表与 epoll，并触发 on_accept。
