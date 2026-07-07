[PROMPT]
Implement function `read_u16`. Responsibility: 从报文体按网络字节序读取 uint16 并推进游标

[RELY]
None.

[GUARANTEE]
```c
static bool read_u16(const uint8_t* body, size_t body_len, size_t* pos, uint16_t* out);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为报文体 body/body_len、当前位置指针 pos 与输出指针 out。

**Post-Condition**:
- 剩余至少 2 字节时返回 true，按网络字节序写入 *out 并推进 *pos；否则返回 false 且不越界读取。

**Invariant**:
- pos 只在成功读取后推进
- 按大端顺序解析 uint16_t

**System Algorithm**:
- 从当前位置读取 2 字节大端整数并推进游标。
