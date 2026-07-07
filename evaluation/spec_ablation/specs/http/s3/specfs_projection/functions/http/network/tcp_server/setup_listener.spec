[PROMPT]
Implement function `setup_listener`. Responsibility: 创建非阻塞 IPv4 TCP 监听 socket：socket→SO_REUSEADDR→set_nonblocking→bind→listen(128)

[RELY]
- STRUCT `struct http_tcp_server`
  role: TCP server 私有状态
```c
struct http_tcp_server;
```

- FUNC `set_nonblocking`
  role: 设置监听 socket 为非阻塞
```c
static bool set_nonblocking(int fd);
```

[GUARANTEE]
```c
static int setup_listener(http_tcp_server_t* server);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server 对象。

**Post-Condition**:
- 成功设置 server->listen_fd 并返回 0。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- socket(AF_INET, SOCK_STREAM, 0) 创建→setsockopt SO_REUSEADDR→set_nonblocking→bind INADDR_ANY:port→listen(fd, 128)。任一步失败则 close 并返回 -1。
