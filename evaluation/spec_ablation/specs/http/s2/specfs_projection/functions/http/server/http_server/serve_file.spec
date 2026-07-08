[PROMPT]
Implement function `serve_file`. Responsibility: 核心文件服务函数：resolve_path→stat→文件则读取并发送→目录则优先 index.html 否则列表→不存在则 404

[RELY]
- STRUCT `struct http_server`
  role: serve_file 读取或维护的 struct http_server 状态
```c
struct http_server;
```

- STRUCT `http_session_t`
  role: serve_file 读取或维护的 http_session_t 状态
```c
typedef struct http_session {
    int fd;
    http_connection_t* conn;
} http_session_t;
```

- STRUCT `http_request_t`
  role: serve_file 读取或维护的 http_request_t 状态
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

- FUNC `http_resolve_path`
  role: serve_file 调用 http_resolve_path 完成子步骤
```c
int http_resolve_path(const char* root_dir, const char* uri_path, char* out_abs, size_t out_len);
```

- FUNC `send_error`
  role: serve_file 调用 send_error 完成子步骤
```c
static int send_error(http_server_t* server, http_session_t* session, int code);
```

- FUNC `http_stat_path`
  role: serve_file 调用 http_stat_path 完成子步骤
```c
int http_stat_path(const char* abs_path);
```

- FUNC `http_read_file`
  role: serve_file 调用 http_read_file 完成子步骤
```c
int http_read_file(const char* abs_path, char** out_data, size_t* out_len);
```

- FUNC `http_mime_by_ext`
  role: serve_file 调用 http_mime_by_ext 完成子步骤
```c
const char* http_mime_by_ext(const char* path);
```

- FUNC `http_dir_listing_html`
  role: serve_file 调用 http_dir_listing_html 完成子步骤
```c
char* http_dir_listing_html(const char* abs_dir, const char* uri_prefix);
```

- FUNC `http_response_send`
  role: serve_file 调用 http_response_send 完成子步骤
```c
int http_response_send(http_connection_t* conn, int status, const char** extra_headers, const void* body, size_t body_len);
```

- FUNC `http_tcp_server_update_interest`
  role: serve_file 调用 http_tcp_server_update_interest 完成子步骤
```c
int http_tcp_server_update_interest(http_tcp_server_t* server, int fd, bool want_write);
```

[GUARANTEE]
```c
static int serve_file(http_server_t* server, http_session_t* session, const http_request_t* req, bool head_only);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server、session、req 和 head_only 标志。

**Post-Condition**:
- 成功返回 0，失败返回 -1；返回 0 表示成功响应或错误响应已排入输出缓冲并已请求写监听，不表示字节已经全部写入 socket。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- server 和 session 非空；resolved_path 已通过 http_resolve_path 规范化
- 成功时响应已排入输出缓冲；失败时已调用 send_error 发送对应错误码
- http_resolve_path 返回 0 表示路径安全，包括 root 内 missing leaf；返回 -1 表示目录遍历、root escape 或 parent/root 不可解析
- http_stat_path 只接收 http_resolve_path 输出的安全绝对路径；返回 0=普通文件, 1=目录, -1=missing/stat failure
- http_read_file 成功时 out_data 为调用方负责 free 的堆内存；http_dir_listing_html 成功时返回调用方负责 free 的堆内存
- http_mime_by_ext 返回静态 MIME 字符串，未匹配时使用 application/octet-stream
- extra_headers 传给 http_response_send 时必须是 NULL 终止的完整 header line 数组，例如 {"Content-Type: text/plain", NULL}
- HEAD 响应不得发送 body，但 Content-Length 必须保留原始资源长度
- root 内 missing leaf 必须返回 404；目录遍历或 root 外路径必须返回 403
- 任何响应排队后都必须注册写监听；如果后续关闭连接，必须先 flush 已排队响应

**System Algorithm**:
- http_resolve_path 安全解析路径→安全失败返回 403→http_stat_path 检查类型→普通文件：http_read_file 读取堆分配 file_data/file_len→http_mime_by_ext 获取静态 MIME 字符串→构造完整 header line "Content-Type: <mime>"→http_response_send→释放 file_data。head_only=true 时必须调用 http_response_send(..., body=NULL, body_len=file_len)，不得把 Content-Length 写成 0。目录：拼接 index.html 并 stat→存在则读取服务→不存在则 http_dir_listing_html 生成堆分配 HTML 列表→发送后释放 listing，目录响应同样使用完整 "Content-Type: text/html" header line。若 http_resolve_path 成功但 http_stat_path 返回 -1，表示 root 内 leaf 不存在或不可 stat，必须返回 404；只有 http_resolve_path 安全失败才返回 403。任何响应成功排队后必须通过 http_tcp_server_update_interest(server->tcp, session->fd, true) 注册写事件；send_error 只负责排队错误响应，关闭连接前仍由调用链先 flush。
