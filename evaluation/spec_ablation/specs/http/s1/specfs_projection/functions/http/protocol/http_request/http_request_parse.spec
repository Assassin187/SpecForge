[PROMPT]
Implement function `http_request_parse`. Responsibility: 从连接输入缓冲解析完整 HTTP/1.1 请求：请求行→头部→正文；返回 0/1/-1

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

- FUNC `http_connection_pop_line`
  role: 从连接缓冲读取一行
```c
char* http_connection_pop_line(http_connection_t* conn);
```

- FUNC `http_connection_buffered`
  role: 检查连接缓冲可用字节数
```c
size_t http_connection_buffered(const http_connection_t* conn);
```

- FUNC `http_connection_pop_bytes`
  role: 从连接缓冲读取指定长度字节
```c
char* http_connection_pop_bytes(http_connection_t* conn, size_t len);
```

- FUNC `parse_method`
  role: 将方法字符串映射为枚举值
```c
static http_method_t parse_method(const char* s);
```

- FUNC `http_request_header`
  role: 按名称查找已解析头部值
```c
const char* http_request_header(const http_request_t* req, const char* name);
```

[GUARANTEE]
```c
int http_request_parse(http_request_t* req, http_connection_t* conn);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 req 和 conn。

**Post-Condition**:
- 成功返回 0；数据不完整返回 1；malformed request line、非法/缺失 header 格式、Content-Length 非法、body 超限等解析错误返回 -1；unsupported method 不返回 -1，而是返回 0 且 req->method=HTTP_UNKNOWN；返回 0 时 req 已完整填充，调用方处理完毕后应调用 http_request_free 释放 body。

**Invariant**:
- 请求行必须包含 method URI version 三部分
- 合法 HTTP method token 但不属于 GET/HEAD/POST 时必须保留为 HTTP_UNKNOWN 并返回 0，由 dispatch 映射为 501
- 只有 malformed request line、非法 version、坏 header、非法 Content-Length 或 body 超限才属于解析错误 -1
- 头部数量不超过 HTTP_MAX_HEADERS
- 正文长度不超过 HTTP_MAX_BODY
- 解析失败时结构体处于未定义状态，调用方应先调用 http_request_free
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。

**System Algorithm**:
- 第一阶段：pop_line 获取请求行，sscanf 解析 method/uri/version，parse_method 映射方法枚举；若 request line 语法完整且 HTTP version 合法，即使 parse_method 返回 HTTP_UNKNOWN，也必须填充 req->method=HTTP_UNKNOWN 并继续解析，不能把 unsupported method 当作 malformed request。第二阶段：循环 pop_line 解析头部直到空行，strchr 查找冒号分隔符，strncpy 保存 name/value。第三阶段：http_request_header 查找 Content-Length，strtoul 转换数值，校验不超过 HTTP_MAX_BODY，pop_bytes 读取正文。
