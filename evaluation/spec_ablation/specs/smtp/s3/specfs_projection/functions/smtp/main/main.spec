[PROMPT]
Implement function `main`. Responsibility: SMTP server 可执行程序入口，负责默认配置、参数解析与 server 生命周期编排

[RELY]
- STRUCT `smtp_server_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_server smtp_server_t;
```

- FUNC `smtp_server_create`
  role: 被该函数调用以完成子步骤
```c
smtp_server_t* smtp_server_create(uint16_t port, const char* mail_root);
```

- FUNC `smtp_server_start`
  role: 被该函数调用以完成子步骤
```c
int smtp_server_start(smtp_server_t* server);
```

- FUNC `smtp_server_run`
  role: 被该函数调用以完成子步骤
```c
int smtp_server_run(smtp_server_t* server);
```

- FUNC `smtp_server_destroy`
  role: 被该函数调用以完成子步骤
```c
void smtp_server_destroy(smtp_server_t* server);
```

[GUARANTEE]
```c
int main(int argc, char** argv);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：argc(int，不可为 NULL，BORROWED)；argv(char**，可为 NULL，BORROWED)。

**Post-Condition**:
- 成功完成 server 生命周期后返回 0；创建或启动失败返回非零退出码，并在已创建 server 时负责销毁。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- 默认端口 2525、默认 mail_root 为 /mail-test；可用 argv[1]/argv[2] 覆盖；创建 server，启动监听，运行事件循环，退出后销毁。
