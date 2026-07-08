[PROMPT]
Implement function `on_datagram`. Responsibility: 处理一个 UDP datagram：解码、malformed CON RST、non-method 静默忽略、资源 dispatch、回包与清理

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

- STRUCT `coap_request_t`
  role: 从 decoded message 投影出的资源请求视图
```c
typedef struct coap_request {
    const coap_message_t* message;
    coap_method_t method;
    char* path;
    char* query;
    uint16_t content_format;
    bool has_content_format;
    uint16_t accept;
    bool has_accept;
} coap_request_t;
```

- FUNC `coap_decode_message`
  role: 调用 coap_decode_message 完成子步骤
```c
coap_decode_status_t coap_decode_message(const uint8_t* data, size_t len, coap_message_t* out);
```

- FUNC `send_reset`
  role: 调用 send_reset 完成子步骤
```c
static void send_reset(coap_server_t* s, const coap_endpoint_t* peer, uint16_t message_id);
```

- FUNC `coap_endpoint_to_string`
  role: 调用 coap_endpoint_to_string 完成子步骤
```c
const char* coap_endpoint_to_string(const coap_endpoint_t* peer, char* buf, size_t buf_sz);
```

- FUNC `coap_code_class`
  role: 调用 coap_code_class 完成子步骤
```c
uint8_t coap_code_class(uint8_t code);
```

- FUNC `coap_code_detail`
  role: 调用 coap_code_detail 完成子步骤
```c
uint8_t coap_code_detail(uint8_t code);
```

- FUNC `coap_code_is_method`
  role: 调用 coap_code_is_method 完成子步骤
```c
bool coap_code_is_method(uint8_t code);
```

- FUNC `setup_response_envelope`
  role: 调用 setup_response_envelope 完成子步骤
```c
static void setup_response_envelope(const coap_message_t* req, coap_message_t* resp);
```

- FUNC `build_request_view`
  role: 调用 build_request_view 完成子步骤
```c
static bool build_request_view(const coap_message_t* req, coap_request_t* out);
```

- FUNC `coap_resource_router_dispatch`
  role: 调用 coap_resource_router_dispatch 完成子步骤
```c
bool coap_resource_router_dispatch(coap_resource_router_t* router, const coap_request_t* req, coap_message_t* resp);
```

- FUNC `send_response`
  role: 调用 send_response 完成子步骤
```c
static void send_response(coap_server_t* s, const coap_endpoint_t* peer, const coap_message_t* resp);
```

- FUNC `free_request_view`
  role: 调用 free_request_view 完成子步骤
```c
static void free_request_view(coap_request_t* req);
```

- FUNC `coap_message_free`
  role: 调用 coap_message_free 完成子步骤
```c
void coap_message_free(coap_message_t* msg);
```

- FUNC `coap_make_code`
  role: 调用 coap_make_code 完成子步骤
```c
uint8_t coap_make_code(uint8_t code_class, uint8_t detail);
```

[GUARANTEE]
```c
static void on_datagram(void* user, const coap_endpoint_t* peer, const uint8_t* data, size_t len);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: UDP runtime 收到一个 datagram 后通过 coap_udp_callbacks_t.on_datagram 调用。
- Precondition: user 必须是 coap_server_t*；peer/data 非空且 len > 0。源码若 s、peer、data 为空或 len 为 0，立即返回。
- Input: 输入是单个 UDP datagram bytes、peer endpoint 和 server/router/KV 当前状态。

**Post-Condition**:
- State Change: 可能发送 RST、5.00、4.04 或 handler 生成的响应；成功解析的 req/resp/view 在所有完成路径上被释放。
- Response: 无返回值；非法 datagram 不产生普通 response，CON malformed datagram 可能产生 RST。

**Invariant**:
- None specified.

**System Algorithm**:
- 调用 coap_decode_message(data, len, &req)。若 decode 失败且 len >= 4，则从 raw bytes 提取 message_id 与 type；只有 raw type 为 COAP_TYPE_CON 时调用 send_reset(s, peer, message_id)，然后返回。decode 成功后打印 peer/type/code/mid。若 req.code 不是 method，释放 req 并返回。否则 setup_response_envelope(&req, &resp)。build_request_view 失败时设置 resp.code=5.00，发送响应，释放 resp 和 req 后返回。dispatch 返回 false 时设置 resp.code=4.04。最后发送 resp，释放 resp，free_request_view(&view)，释放 req。
