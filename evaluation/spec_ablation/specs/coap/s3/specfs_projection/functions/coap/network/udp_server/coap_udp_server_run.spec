[PROMPT]
Implement function `coap_udp_server_run`. Responsibility: 运行非阻塞 UDP epoll 事件循环，每个成功 recvfrom 的 datagram 立即回调上层

[RELY]
- STRUCT `coap_udp_server_t`
  role: UDP/epoll server 不透明句柄
```c
typedef struct coap_udp_server coap_udp_server_t;
```

- STRUCT `coap_endpoint_t`
  role: UDP peer endpoint，保存 sockaddr_storage 与实际地址长度
```c
typedef struct coap_endpoint {
    struct sockaddr_storage addr;
    socklen_t addr_len;
} coap_endpoint_t;
```

[GUARANTEE]
```c
void coap_udp_server_run(coap_udp_server_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 应用在 coap_udp_server_start 成功后调用，进入 UDP datagram event loop。
- Precondition: s 非空且 s->running 为 true；否则函数立即返回。
- Input: 事件输入来自 epoll_wait 返回的 EPOLLIN 事件和 recvfrom 读取的单个 UDP datagram。

**Post-Condition**:
- State Change: 函数不直接修改协议状态；收到 datagram 时通过 callback 把 peer 与 bytes 上交。stop 设置 running=false 后外层循环结束。
- Response: 无返回值；loop 结束于 running=false 或非 EINTR epoll 错误。

**Invariant**:
- None specified.

**System Algorithm**:
- 打印监听端口后，以 1000ms timeout 循环 epoll_wait。epoll_wait 被 EINTR 中断时继续外层循环；其他 epoll 错误打印并退出 loop。忽略非 EPOLLIN 或 fd 不等于 server fd 的事件。对有效事件进入 recvfrom 内层循环：每次清零 peer、设置 peer.addr_len，r > 0 时若 cb.on_datagram 存在则调用 cb.on_datagram(user, &peer, buf, r) 并继续读取；r == 0、EAGAIN 或 EWOULDBLOCK 退出内层循环；EINTR 重试 recvfrom；其他 recvfrom 错误打印并退出内层循环。
