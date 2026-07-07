[PROMPT]
Implement function `http_tcp_server_run`. Responsibility: 运行 epoll 事件循环：epoll_wait 1s 超时，分派 accept/read/write/close 事件

[RELY]
- STRUCT `struct http_tcp_server`
  role: TCP server 私有状态
```c
struct http_tcp_server;
```

- STRUCT `http_client_node_t`
  role: 客户端链表节点
```c
typedef struct http_client_node http_client_node_t;
```

- FUNC `accept_loop`
  role: 处理新连接接入
```c
static void accept_loop(http_tcp_server_t* server);
```

- FUNC `find_client`
  role: 按 fd 定位客户端节点
```c
static http_client_node_t* find_client(http_tcp_server_t* server, int fd);
```

- FUNC `http_tcp_server_close_client`
  role: 处理错误/关闭路径
```c
int http_tcp_server_close_client(http_tcp_server_t* server, int fd);
```

[GUARANTEE]
```c
int http_tcp_server_run(http_tcp_server_t* server);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server。

**Post-Condition**:
- 正常退出返回 0，异常返回错误码。

**Invariant**:
- running 标志控制循环生命周期
- 事件循环退出后不残留待处理事件
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- 设置 running=true；while(running) epoll_wait 64 events 1s 超时→listen_fd 调用 accept_loop→client EPOLLERR|HUP|RDHUP 调用 close_client→EPOLLIN/EPOLLOUT 构建 app_ev 并回调 on_event。
