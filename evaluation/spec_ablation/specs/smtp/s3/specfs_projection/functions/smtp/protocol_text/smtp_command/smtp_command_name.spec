[PROMPT]
Implement function `smtp_command_name`. Responsibility: 把命令枚举转换为固定命令名字符串，未知值返回 UNKNOWN

[RELY]
- STRUCT `smtp_command_kind_t`
  role: 该函数读取或维护的结构化状态
```c
typedef enum smtp_command_kind {
    SMTP_CMD_HELO,
    SMTP_CMD_EHLO,
    SMTP_CMD_MAIL,
    SMTP_CMD_RCPT,
    SMTP_CMD_DATA,
    SMTP_CMD_RSET,
    SMTP_CMD_NOOP,
    SMTP_CMD_QUIT,
    SMTP_CMD_AUTH,
    SMTP_CMD_VRFY,
    SMTP_CMD_UNKNOWN,
} smtp_command_kind_t;
```

[GUARANTEE]
```c
const char* smtp_command_name(smtp_command_kind_t kind);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：kind(smtp_command_kind_t，不可为 NULL，BORROWED)。

**Post-Condition**:
- 返回命令枚举对应的静态字符串；未知枚举返回 UNKNOWN。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- 把命令枚举转换为固定命令名字符串，未知值返回 UNKNOWN。
