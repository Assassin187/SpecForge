[PROMPT]
Implement function `coap_udp_server_stop`. Responsibility: 置 running=false，关闭 epoll fd 与 UDP fd，并将它们复位为 -1

[RELY]
- STRUCT `coap_udp_server_t`
  role: UDP/epoll server 不透明句柄
```c
typedef struct coap_udp_server coap_udp_server_t;
```

[GUARANTEE]
```c
void coap_udp_server_stop(coap_udp_server_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- s 是可能为空、可能部分启动的 UDP server。

**Post-Condition**:
- 无返回值；完成后非空 server 的 running 为 false，已打开的 epoll fd 与 UDP fd 均被关闭并复位。

**Invariant**:
- stop 可重复调用；-1 fd 不会被 close。
- 先关闭 epoll fd，再关闭 UDP fd。

**System Algorithm**:
- 若 s 为空直接返回。否则先设置 running=false；若 epoll_fd >= 0 则 close(epoll_fd) 并复位为 -1；若 fd >= 0 则 close(fd) 并复位为 -1。
