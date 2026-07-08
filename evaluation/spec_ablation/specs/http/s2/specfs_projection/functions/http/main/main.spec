[PROMPT]
Implement function `main`. Responsibility: HTTP server 进程入口：解析参数→创建→启动→运行→销毁

[RELY]
- FUNC `parse_port`
  role: 解析端口字符串
```c
static uint16_t parse_port(const char* s);
```

- FUNC `http_server_create`
  role: 创建 HTTP server 对象
```c
http_server_t* http_server_create(uint16_t port, const char* root_dir);
```

- FUNC `http_server_start`
  role: 启动 HTTP server 监听
```c
int http_server_start(http_server_t* server);
```

- FUNC `http_server_run`
  role: 进入 server 事件循环
```c
int http_server_run(http_server_t* server);
```

- FUNC `http_server_destroy`
  role: 释放 server 资源
```c
void http_server_destroy(http_server_t* server);
```

[GUARANTEE]
```c
int main(int argc, char** argv);
```

[SPECIFICATION]
**Pre-Condition**:
- 命令行参数。

**Post-Condition**:
- 成功返回 0。

**Invariant**:
- 创建或启动失败必须返回非零退出码
- server 创建成功后任何后续失败路径都必须销毁 server
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。

**System Algorithm**:
- parse_port 解析 argv[1]（默认 8080），argv[2] 作为 root 目录（默认 '.'）→http_server_create→http_server_start→printf 监听信息→http_server_run→http_server_destroy。
