[PROMPT]
Implement function `smtp_command_parse`. Responsibility: 解析单行 SMTP 命令，跳过前导空白、verb 大写化、复制并右裁剪参数

[RELY]
- STRUCT `smtp_command_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_command {
    smtp_command_kind_t kind;
    char arg[1024];
    bool has_arg;
} smtp_command_t;
```

- FUNC `parse_kind`
  role: 被该函数调用以完成子步骤
```c
static smtp_command_kind_t parse_kind(const char* cmd);
```

[GUARANTEE]
```c
int smtp_command_parse(const char* line, smtp_command_t* out);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：line(const char*，可为 NULL，BORROWED)；out(smtp_command_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 成功返回 0 并填充 out->kind、out->arg 和 out->has_arg；line/out 无效或空命令返回 -1。AUTH LOGIN 解析后 out->arg 必须为 `LOGIN` 且 has_arg=true；AUTH LOGIN <base64> 解析后 out->arg 必须为 `LOGIN <base64>`；AUTH PLAIN <base64> 解析后 out->arg 必须为 `PLAIN <base64>`。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 维护 smtp_command_t.kind：写入命令类型
- 维护 smtp_command_t.arg：写入参数副本
- 维护 smtp_command_t.has_arg：写入参数存在标志
- 遵循 SMTP_COMMAND.verb 映射规则：第一个空白分隔 token 大写后决定 kind
- 遵循 SMTP_COMMAND.argument 映射规则：verb 后剩余非空文本右裁剪后完整复制到 arg，不拆 AUTH mechanism 或 initial response
- 遵循 SMTP_COMMAND.has_argument 映射规则：argument 非空时为 true

**System Algorithm**:
- 解析单行 SMTP 命令，跳过前导空白，只把第一个空白分隔 token 作为 command verb 并大写化后映射 kind；verb 后剩余文本仅右裁剪后完整复制到 out->arg，不再拆分子字段。对 AUTH 命令，smtp_command_parse 不拆 mechanism，也不解析 initial response；AUTH mechanism 与 optional initial response 只能由 handle_auth 从 out->arg 中继续拆分。
