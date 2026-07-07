[PROMPT]
Implement function `smtp_auth_validate`. Responsibility: 验证 user/pass 是否等于 smtpuser/smtppass

[RELY]
None.

[GUARANTEE]
```c
int smtp_auth_validate(const char* user, const char* pass);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：user(const char*，可为 NULL，BORROWED)；pass(const char*，可为 NULL，BORROWED)。

**Post-Condition**:
- 返回 1 表示 user/pass 与内置 smtpuser/smtppass 匹配；NULL 或不匹配返回 0。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- 验证 user/pass 是否等于 smtpuser/smtppass。
