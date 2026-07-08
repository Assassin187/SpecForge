[PROMPT]
Implement function `main`. Responsibility: CoAP server 可执行程序入口，编排端口解析与 server 生命周期

[RELY]
- STRUCT `coap_server_t`
  role: CoAP server 进程级对象的不透明句柄
```c
typedef struct coap_server coap_server_t;
```

- FUNC `coap_server_create`
  role: 调用 coap_server_create 完成子步骤
```c
coap_server_t* coap_server_create(uint16_t port);
```

- FUNC `coap_server_start`
  role: 调用 coap_server_start 完成子步骤
```c
bool coap_server_start(coap_server_t* s);
```

- FUNC `coap_server_run`
  role: 调用 coap_server_run 完成子步骤
```c
void coap_server_run(coap_server_t* s);
```

- FUNC `coap_server_destroy`
  role: 调用 coap_server_destroy 完成子步骤
```c
void coap_server_destroy(coap_server_t* s);
```

[GUARANTEE]
```c
int main(int argc, char** argv);
```

[SPECIFICATION]
**Pre-Condition**:
- argc/argv 由 C runtime 提供；默认监听端口先设为 5683；仅当 argc >= 2 时用 atoi(argv[1]) 解析候选端口。

**Post-Condition**:
- 创建失败或启动失败返回 1；server 正常 run 返回并完成 destroy 后返回 0。

**Invariant**:
- 无效、缺失或越界端口参数不会阻止启动，而是回退到 5683。
- start 失败路径必须释放已经创建的 server。
- 成功路径必须在 coap_server_run 返回后销毁 server。

**System Algorithm**:
- 若解析出的 p 位于 1..65535，则将 port 设为 (uint16_t)p，否则保留默认 5683。随后调用 coap_server_create(port)；创建失败时向 stderr 写入失败信息并返回 1。创建成功后调用 coap_server_start(server)；启动失败时写入 stderr、销毁 server 并返回 1。启动成功后调用 coap_server_run(server)，run 返回后调用 coap_server_destroy(server)。
