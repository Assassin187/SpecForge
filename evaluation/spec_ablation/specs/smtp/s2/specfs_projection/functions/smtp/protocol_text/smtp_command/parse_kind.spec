[PROMPT]
Implement function `parse_kind`. Responsibility: 将大写命令 verb 映射为 smtp_command_kind_t，未命中返回 SMTP_CMD_UNKNOWN

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
static smtp_command_kind_t parse_kind(const char* cmd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：cmd(const char*，可为 NULL，BORROWED)。

**Post-Condition**:
- 返回匹配的 smtp_command_kind_t；未知命令返回 SMTP_CMD_UNKNOWN。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 遵循 SMTP_COMMAND.verb 映射规则：HELO/EHLO/MAIL/RCPT/DATA/RSET/NOOP/QUIT/AUTH/VRFY 映射到对应 enum

**System Algorithm**:
- 将大写命令 verb 映射为 smtp_command_kind_t，未命中返回 SMTP_CMD_UNKNOWN。
