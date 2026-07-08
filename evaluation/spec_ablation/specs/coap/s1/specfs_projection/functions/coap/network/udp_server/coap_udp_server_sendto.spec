[PROMPT]
Implement function `coap_udp_server_sendto`. Responsibility: 通过 server socket 向 peer 发送完整 datagram；EINTR 重试，EAGAIN/部分发送返回 false

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
bool coap_udp_server_sendto(coap_udp_server_t* s, const coap_endpoint_t* peer, const uint8_t* data, size_t len);
```

[SPECIFICATION]
**Pre-Condition**:
- s/peer/data 指向已启动 server、目标 endpoint 与待发送 datagram；len 是 datagram 长度。

**Post-Condition**:
- 完整发送 len 字节返回 true；部分发送、不可写、参数无效或其他错误返回 false。

**Invariant**:
- 函数不缓存未发送数据。
- EINTR 是唯一会触发重试的错误。

**System Algorithm**:
- 若 s、peer、data 为空，len 为 0，或 s->fd < 0，直接返回 false。否则循环调用 sendto(s->fd, data, len, 0, peer->addr, peer->addr_len)。sendto 成功时只接受完整 datagram 发送；errno 为 EINTR 时重试；errno 为 EAGAIN 或 EWOULDBLOCK 时返回 false；其他错误返回 false。
