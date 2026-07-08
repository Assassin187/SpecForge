[PROMPT]
Implement function `decode_b64_to_text`. Responsibility: 把 base64 文本解码为 NUL 结尾字符串，拒绝空结果或溢出

[RELY]
- FUNC `smtp_auth_decode_base64`
  role: 被该函数调用以完成子步骤
```c
int smtp_auth_decode_base64(const char* input, unsigned char* out, size_t out_cap, size_t* out_len);
```

[GUARANTEE]
```c
static int decode_b64_to_text(const char* b64, char* out, size_t out_len);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：b64(const char*，可为 NULL，BORROWED)；out(char*，可为 NULL，BORROWED)；out_len(size_t，不可为 NULL，BORROWED)。

**Post-Condition**:
- 成功返回 0，out 包含 decoded_len 字节文本加 NUL；输入无效、Base64 解码失败、空结果或 out_len 不足时返回 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 必须使用 smtp_auth_decode_base64 返回的 decoded_len 作为唯一长度来源
- decoded_len 必须大于 0 且小于 out_len，才能复制并追加 NUL
- 不得使用 strlen() 读取未 NUL 终止的 decoded buffer

**System Algorithm**:
- b64、out 为 NULL 或 out_len 为 0 时返回 -1。必须调用 smtp_auth_decode_base64(b64, temp, temp_cap, &decoded_len)；解码失败返回 -1。解码成功但 decoded_len == 0 或 decoded_len >= out_len 时返回 -1。成功时只复制准确 decoded_len 字节到 out，并追加单个 NUL 终止符。禁止对 decoded buffer 使用 strlen() 判断长度，字符串长度只能来自 smtp_auth_decode_base64 写入的 decoded_len。
