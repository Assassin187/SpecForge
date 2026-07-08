[PROMPT]
Implement function `accept_loop`. Responsibility: 批量 accept 新连接并完成非阻塞设置、epoll 注册与 on_accept 回调

[RELY]
- STRUCT `struct http_tcp_server`
  role: TCP server 私有状态
```c
struct http_tcp_server;
```

- FUNC `set_nonblocking`
  role: 设置客户端 socket 为非阻塞
```c
static bool set_nonblocking(int fd);
```

- FUNC `add_client`
  role: 注册客户端到 epoll 与链表
```c
static int add_client(http_tcp_server_t* server, int fd);
```

[GUARANTEE]
```c
static void accept_loop(http_tcp_server_t* server);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server。

**Post-Condition**:
- 无返回值；通过副作用修改连接链表。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。

**System Algorithm**:
- 循环 accept 直到 EAGAIN/EWOULDBLOCK；每个新连接调用 set_nonblocking→add_client→on_accept 回调。任一失败则 close(fd)。
