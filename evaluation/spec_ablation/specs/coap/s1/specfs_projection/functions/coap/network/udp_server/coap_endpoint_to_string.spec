[PROMPT]
Implement function `coap_endpoint_to_string`. Responsibility: 将 IPv4/IPv6 peer endpoint 格式化为 host:port；非法参数返回空字符串

[RELY]
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
const char* coap_endpoint_to_string(const coap_endpoint_t* peer, char* buf, size_t buf_sz);
```

[SPECIFICATION]
**Pre-Condition**:
- peer 指向待格式化 endpoint；buf/buf_sz 提供调用方拥有的输出缓冲区。

**Post-Condition**:
- 非法参数返回 ""；未知 family 或成功格式化时返回 buf。

**Invariant**:
- 输出写入调用方提供的 buf；函数不分配内存。
- host 缓冲区大小为 INET6_ADDRSTRLEN。

**System Algorithm**:
- 若 peer、buf 为空或 buf_sz 为 0，直接返回空字符串常量。AF_INET 时用 inet_ntop(AF_INET) 和 ntohs(sin_port) 得到 host/port；AF_INET6 时用 inet_ntop(AF_INET6) 和 ntohs(sin6_port)。未知 address family 时把 "<unknown>" 写入 buf 并返回 buf。IPv4/IPv6 正常路径把 "host:port" 写入 buf。
