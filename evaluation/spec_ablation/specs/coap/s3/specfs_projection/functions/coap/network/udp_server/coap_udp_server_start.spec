[PROMPT]
Implement function `coap_udp_server_start`. Responsibility: 创建非阻塞 IPv4 UDP socket，bind 端口，注册 EPOLLIN 并置 running=true

[RELY]
- STRUCT `coap_udp_server_t`
  role: UDP/epoll server 不透明句柄
```c
typedef struct coap_udp_server coap_udp_server_t;
```

- FUNC `set_nonblocking`
  role: 调用 set_nonblocking 完成子步骤
```c
static bool set_nonblocking(int fd);
```

[GUARANTEE]
```c
bool coap_udp_server_start(coap_udp_server_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- s 必须是已创建但尚未成功启动的 UDP server。

**Post-Condition**:
- 任一失败路径返回 false；全部成功返回 true。源码失败路径不会主动关闭此前已经打开的 fd。

**Invariant**:
- 监听只绑定 IPv4 INADDR_ANY。
- epoll interest 只注册 EPOLLIN。
- running 仅在 socket、nonblocking、bind、epoll_create1、epoll_ctl 全部成功后设为 true。

**System Algorithm**:
- 若 s 为空返回 false。创建 AF_INET/SOCK_DGRAM socket；失败时打印 socket 错误并返回 false。对 socket 设置 SO_REUSEADDR，忽略 setsockopt 结果。调用 set_nonblocking；失败时打印错误并返回 false。构造绑定到 INADDR_ANY:s->port 的 sockaddr_in；bind 失败时打印错误并返回 false。创建 epoll fd；失败时打印错误并返回 false。注册 server fd 的 EPOLLIN；失败时打印错误并返回 false。所有步骤成功后设置 running=true。
