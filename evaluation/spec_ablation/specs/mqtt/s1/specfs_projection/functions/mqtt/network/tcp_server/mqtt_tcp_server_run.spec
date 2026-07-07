[PROMPT]
Implement function `mqtt_tcp_server_run`. Responsibility: 运行 epoll 驱动主循环并分发连接读写关闭事件

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

- FUNC `mqtt_connection_want_write`
  role: 判断连接是否待写
```c
bool mqtt_connection_want_write(const mqtt_connection_t* c);
```

- FUNC `accept_loop`
  role: 处理新连接接入
```c
static void accept_loop(mqtt_tcp_server_t* s);
```

- FUNC `find_conn`
  role: 按 fd 定位连接对象
```c
static mqtt_connection_t* find_conn(mqtt_tcp_server_t* s, int fd);
```

- FUNC `close_connection`
  role: 处理错误/关闭路径
```c
static void close_connection(mqtt_tcp_server_t* s, mqtt_connection_t* c);
```

- FUNC `mqtt_connection_read`
  role: 读取连接输入缓冲
```c
bool mqtt_connection_read(mqtt_connection_t* c, bool* peer_closed);
```

- FUNC `mqtt_connection_flush`
  role: 刷新连接输出队列
```c
bool mqtt_connection_flush(mqtt_connection_t* c);
```

- FUNC `update_interest`
  role: 更新 epoll 关注事件
```c
static void update_interest(mqtt_tcp_server_t* s, mqtt_connection_t* c);
```

[GUARANTEE]
```c
void mqtt_tcp_server_run(mqtt_tcp_server_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 服务器 start 成功后由上层调用进入运行阶段
- Precondition: s 非空且 s->running=true，epoll/listen fd 已初始化
- Input: 输入为服务器对象 s；事件输入由 epoll_wait 返回

**Post-Condition**:
- State Change: 连接链表随接入/关闭动态变化，连接缓冲区与发送队列持续更新
- Response: 无直接返回值；通过回调与网络 I/O 驱动上层协议行为，循环在 stop 后退出

**Invariant**:
- None specified.

**System Algorithm**:
- 循环 epoll_wait：监听 fd 触发 accept_loop；连接 fd 可读时调用 mqtt_connection_read，成功且 input buffer 非空时必须调用 on_data，即使 peer_closed=true；处理 buffered input 后才关闭 peer_closed 连接。任一 on_data callback 可能向其他连接入队，因此每轮事件处理后必须扫描所有仍存活连接，主动 flush 每个待写队列并更新其 epoll interest，而不是只处理当前事件连接。
