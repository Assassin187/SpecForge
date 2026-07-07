[PROMPT]
Implement function `smtp_auth_parse_plain_blob`. Responsibility: 解析 AUTH PLAIN blob：NUL、user、NUL、pass

[RELY]
None.

[GUARANTEE]
```c
int smtp_auth_parse_plain_blob(const unsigned char* blob, size_t blob_len, char* out_user, size_t out_user_len, char* out_pass, size_t out_pass_len);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：blob(const unsigned char*，可为 NULL，BORROWED)；blob_len(size_t，不可为 NULL，BORROWED)；out_user(char*，可为 NULL，BORROWED)；out_user_len(size_t，不可为 NULL，BORROWED)；out_pass(char*，可为 NULL，BORROWED)；out_pass_len(size_t，不可为 NULL，BORROWED)。

**Post-Condition**:
- 成功返回 0 并写入 NUL 结尾 user/pass；blob 缺少分隔 NUL、字段为空或输出容量不足时返回 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。
- 遵循 AUTH_PLAIN.authzid_nul_authcid_nul_password 映射规则：blob 必须包含两个 NUL，user/pass 均非空并适配输出缓冲

**System Algorithm**:
- 解析 AUTH PLAIN blob：NUL、user、NUL、pass。
