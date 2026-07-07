[PROMPT]
Implement function `http_request_free`. Responsibility: 释放 body 动态内存并复位 body=NULL/body_len=0；空指针安全

[RELY]
- STRUCT `http_request_t`
  role: HTTP 请求结构体
```c
typedef struct http_request {
    http_method_t method;
    char uri[HTTP_MAX_URI];
    http_header_t headers[HTTP_MAX_HEADERS];
    size_t header_count;
    char* body;
    size_t body_len;
} http_request_t;
```

[GUARANTEE]
```c
void http_request_free(http_request_t* req);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 req。

**Post-Condition**:
- 通过副作用释放 body 内存并复位。

**Invariant**:
- 空指针输入时无副作用直接返回
- 释放后 body 字段置为 NULL 防止悬垂指针
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- 空安全检查→free(req->body)→req->body=NULL, req->body_len=0。
