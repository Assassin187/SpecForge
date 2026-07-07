[PROMPT]
Implement function `read_string`. Responsibility: 读取 MQTT 长度前缀字符串并分配以 \0 结尾的新内存

[RELY]
- FUNC `read_u16`
  role: 先读取字符串长度前缀
```c
static bool read_u16(const uint8_t* body, size_t body_len, size_t* pos, uint16_t* out);
```

[GUARANTEE]
```c
static bool read_string(const uint8_t* body, size_t body_len, size_t* pos, char** out);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为 MQTT 长度前缀字符串所在 body/body_len、游标 pos 与输出指针 out。

**Post-Condition**:
- 成功返回 true，分配 NUL 结尾字符串写入 *out 并推进 *pos；长度不足或分配失败返回 false。

**Invariant**:
- 成功返回的字符串由调用方释放
- 失败时不越界读取 body

**System Algorithm**:
- 先读取长度前缀，再复制字符串内容并以 NUL 结尾返回。
