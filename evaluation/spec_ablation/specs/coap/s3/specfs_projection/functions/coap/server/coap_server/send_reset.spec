[PROMPT]
Implement function `send_reset`. Responsibility: 构造空 RST message 并发送到 peer

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

- FUNC `coap_message_init`
  role: 调用 coap_message_init 完成子步骤
```c
void coap_message_init(coap_message_t* msg);
```

- FUNC `send_response`
  role: 调用 send_response 完成子步骤
```c
static void send_response(coap_server_t* s, const coap_endpoint_t* peer, const coap_message_t* resp);
```

- FUNC `coap_message_free`
  role: 调用 coap_message_free 完成子步骤
```c
void coap_message_free(coap_message_t* msg);
```

[GUARANTEE]
```c
static void send_reset(coap_server_t* s, const coap_endpoint_t* peer, uint16_t message_id);
```

[SPECIFICATION]
**Pre-Condition**:
- s/peer 指定发送目标；message_id 是要放入 RST 的 Message ID。

**Post-Condition**:
- 无返回值；成功编码时发送一个空 RST message，发送失败不向上传递。

**Invariant**:
- RST 不设置 token 或 payload。

**System Algorithm**:
- 创建局部 coap_message_t rst，调用 coap_message_init(&rst)，设置 rst.type = COAP_TYPE_RST、rst.code = 0、rst.message_id = message_id。调用 send_response(s, peer, &rst)，随后 coap_message_free(&rst)。
