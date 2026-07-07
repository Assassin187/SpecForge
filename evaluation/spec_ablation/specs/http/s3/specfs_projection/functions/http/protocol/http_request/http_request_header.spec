[PROMPT]
Implement function `http_request_header`. Responsibility: 按名称（大小写不敏感）在已解析头部中查找值；未找到返回 NULL

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

- STRUCT `http_header_t`
  role: HTTP 头部键值对结构体
```c
typedef struct http_header {
    char name[HTTP_MAX_HEADER_NAME];
    char value[HTTP_MAX_HEADER_VALUE];
} http_header_t;
```

[GUARANTEE]
```c
const char* http_request_header(const http_request_t* req, const char* name);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 req 和头部名称。

**Post-Condition**:
- 找到返回值字符串指针，未找到返回 NULL。

**Invariant**:
- 查找为大小写不敏感匹配
- 空指针输入安全返回 NULL
- 返回指针指向 req 内部内存，生命周期与 req 相同
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- 空安全检查→遍历 0..header_count-1，strcasecmp 比较 headers[i].name==name→匹配返回 headers[i].value。
