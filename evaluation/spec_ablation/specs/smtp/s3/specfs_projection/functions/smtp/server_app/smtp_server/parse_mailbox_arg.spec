[PROMPT]
Implement function `parse_mailbox_arg`. Responsibility: 解析 MAIL FROM:/RCPT TO: 参数，支持尖括号地址和非空白地址

[RELY]
None.

[GUARANTEE]
```c
static int parse_mailbox_arg(const char* arg, const char* key, char* out, size_t out_len);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：arg(const char*，可为 NULL，BORROWED)；key(const char*，可为 NULL，BORROWED)；out(char*，可为 NULL，BORROWED)；out_len(size_t，不可为 NULL，BORROWED)。

**Post-Condition**:
- 返回值表达解析 MAIL FROM:/RCPT TO: 参数，支持尖括号地址和非空白地址的结果；成功、失败和特殊分支按当前 C 实现的返回码区分。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- 解析 MAIL FROM:/RCPT TO: 参数，支持尖括号地址和非空白地址。
