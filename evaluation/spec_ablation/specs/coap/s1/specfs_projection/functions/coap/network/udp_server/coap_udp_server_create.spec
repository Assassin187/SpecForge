[PROMPT]
Implement function `coap_udp_server_create`. Responsibility: 分配 UDP server，保存 port/callback/user，并初始化 fd 为 -1

[RELY]
- STRUCT `coap_udp_server_t`
  role: UDP/epoll server 不透明句柄
```c
typedef struct coap_udp_server coap_udp_server_t;
```

- STRUCT `coap_udp_callbacks_t`
  role: UDP runtime callback 集合
```c
typedef struct coap_udp_callbacks {
    coap_udp_on_datagram_fn on_datagram;
} coap_udp_callbacks_t;
```

[GUARANTEE]
```c
coap_udp_server_t* coap_udp_server_create(uint16_t port, coap_udp_callbacks_t cb, void* user);
```

[SPECIFICATION]
**Pre-Condition**:
- port 是监听端口；cb/user 是上层回调集合与用户上下文，按值保存。

**Post-Condition**:
- 成功返回新分配 server 对象；分配失败返回 NULL。调用方负责用 coap_udp_server_destroy 释放成功返回的对象。

**Invariant**:
- running 依赖 calloc 保持 false。
- fd 和 epoll_fd 必须以 -1 表示尚未打开。

**System Algorithm**:
- 用 calloc 分配 coap_udp_server_t 并获得零初始化状态；分配失败返回 NULL。分配成功后保存 port、cb、user，并把 fd 与 epoll_fd 初始化为 -1。
