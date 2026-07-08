[PROMPT]
Implement function `free_request_view`. Responsibility: 释放 request view 的 path/query 并将指针复位

[RELY]
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

[GUARANTEE]
```c
static void free_request_view(coap_request_t* req);
```

[SPECIFICATION]
**Pre-Condition**:
- req 是可能为空的 request view。

**Post-Condition**:
- 无返回值；非空 view 的动态 path/query 被释放并复位。

**Invariant**:
- 不释放 req->message，因为它是借用指针。

**System Algorithm**:
- 若 req 为空直接返回。否则释放 req->path 和 req->query，并将二者置 NULL。
