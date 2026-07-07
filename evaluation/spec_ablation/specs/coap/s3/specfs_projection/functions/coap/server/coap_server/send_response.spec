[PROMPT]
Implement function `send_response`. Responsibility: 编码 response 并通过 UDP sendto 发往原 peer，随后释放编码缓冲

[RELY]
- STRUCT `coap_server_t`
  role: CoAP server 进程级对象的不透明句柄
```c
typedef struct coap_server coap_server_t;
```

- STRUCT `coap_endpoint_t`
  role: UDP peer endpoint，保存 sockaddr_storage 与实际地址长度
```c
typedef struct coap_endpoint {
    struct sockaddr_storage addr;
    socklen_t addr_len;
} coap_endpoint_t;
```

- STRUCT `coap_message_t`
  role: 统一 CoAP message 对象，保存 header、token、options 与 payload
```c
typedef struct coap_message {
    uint8_t version;
    coap_type_t type;
    uint8_t code;
    uint16_t message_id;
    uint8_t token_len;
    uint8_t token[COAP_MAX_TOKEN_LEN];
    coap_option_t* options;
    size_t option_count;
    uint8_t* payload;
    size_t payload_len;
} coap_message_t;
```

- STRUCT `coap_bytes_t`
  role: 编码结果动态字节缓冲，由 coap_bytes_free 释放
```c
typedef struct coap_bytes {
    uint8_t* data;
    size_t len;
} coap_bytes_t;
```

- FUNC `coap_encode_message`
  role: 调用 coap_encode_message 完成子步骤
```c
bool coap_encode_message(const coap_message_t* msg, coap_bytes_t* out);
```

- FUNC `coap_udp_server_sendto`
  role: 调用 coap_udp_server_sendto 完成子步骤
```c
bool coap_udp_server_sendto(coap_udp_server_t* s, const coap_endpoint_t* peer, const uint8_t* data, size_t len);
```

- FUNC `coap_bytes_free`
  role: 调用 coap_bytes_free 完成子步骤
```c
void coap_bytes_free(coap_bytes_t* bytes);
```

[GUARANTEE]
```c
static void send_response(coap_server_t* s, const coap_endpoint_t* peer, const coap_message_t* resp);
```

[SPECIFICATION]
**Pre-Condition**:
- s 是 server；peer 是目标 endpoint；resp 是待发送 response message。

**Post-Condition**:
- 无返回值；编码成功时尝试发送并总是释放 encoded bytes。编码失败时不发送。

**Invariant**:
- UDP sendto 的失败不会反馈给调用方。
- encoded bytes 的所有权只在本函数内部存在。

**System Algorithm**:
- 创建局部 coap_bytes_t bytes={0}。调用 coap_encode_message(resp, &bytes)；编码失败直接返回。编码成功后调用 coap_udp_server_sendto(s->udp, peer, bytes.data, bytes.len)，忽略发送结果。最后调用 coap_bytes_free(&bytes) 释放编码缓冲。
