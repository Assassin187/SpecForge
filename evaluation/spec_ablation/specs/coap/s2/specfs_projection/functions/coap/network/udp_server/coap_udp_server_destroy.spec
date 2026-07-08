[PROMPT]
Implement function `coap_udp_server_destroy`. Responsibility: 停止并释放 UDP server；空指针安全返回

[RELY]
- STRUCT `coap_udp_server_t`
  role: UDP/epoll server 不透明句柄
```c
typedef struct coap_udp_server coap_udp_server_t;
```

- FUNC `coap_udp_server_stop`
  role: 调用 coap_udp_server_stop 完成子步骤
```c
void coap_udp_server_stop(coap_udp_server_t* s);
```

[GUARANTEE]
```c
void coap_udp_server_destroy(coap_udp_server_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- s 是可能为空的 UDP server 对象。

**Post-Condition**:
- 无返回值；空指针路径不产生副作用，非空路径释放 server 对象。

**Invariant**:
- 释放对象前必须先 stop，以保证 fd/epoll_fd 被关闭或复位。

**System Algorithm**:
- 若 s 为空直接返回。否则先调用 coap_udp_server_stop(s) 关闭 runtime 资源，再释放 s 本身。
