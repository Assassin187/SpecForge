[PROMPT]
Implement function `setup_response_envelope`. Responsibility: 初始化 response，CON 映射为 ACK、其他有效请求映射为 NON，并复制 Message ID 与 Token

[RELY]
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

- FUNC `coap_message_set_token`
  role: 调用 coap_message_set_token 完成子步骤
```c
bool coap_message_set_token(coap_message_t* msg, const uint8_t* token, size_t token_len);
```

[GUARANTEE]
```c
static void setup_response_envelope(const coap_message_t* req, coap_message_t* resp);
```

[SPECIFICATION]
**Pre-Condition**:
- req 是已解码请求 message；resp 是待初始化响应 message。

**Post-Condition**:
- 无返回值；resp 获得响应 envelope：version、type、message_id 和 token。

**Invariant**:
- CON 请求映射为 ACK，其他请求类型映射为 NON。
- token 复制失败不会被本函数上报。

**System Algorithm**:
- 调用 coap_message_init(resp)，再设置 resp->version=1。若 req->type == COAP_TYPE_CON，则 resp->type = COAP_TYPE_ACK；否则 resp->type = COAP_TYPE_NON。复制 req->message_id 到 resp。调用 coap_message_set_token(resp, req->token, req->token_len)，但忽略返回值。
