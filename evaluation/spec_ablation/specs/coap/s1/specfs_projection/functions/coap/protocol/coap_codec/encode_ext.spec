[PROMPT]
Implement function `encode_ext`. Responsibility: 按 nibble 13/14 编码 extended value；nibble 15 返回 false

[RELY]
None.

[GUARANTEE]
```c
static bool encode_ext(uint16_t value, uint8_t nibble, uint8_t* out, size_t* out_len);
```

[SPECIFICATION]
**Pre-Condition**:
- value 是原始 delta/length，nibble 是 encode_nibble 的结果，out 指向调用方提供的 2-byte scratch buffer，out_len 用于返回写入长度。

**Post-Condition**:
- nibble 13/14 成功写入扩展字节并返回 true；nibble < 13 返回 true 且 out_len 为 0；nibble 15 或 out_len 为空返回 false。

**Invariant**:
- 源码假定当 nibble 为 13 或 14 时 out 非空且 value 与 nibble 匹配。

**System Algorithm**:
- 若 out_len 为空返回 false。否则先将 *out_len 置 0。nibble < 13 时无需写扩展字节并返回 true；nibble == 13 时写 out[0] = value - 13，*out_len = 1；nibble == 14 时计算 value - 269 并按 big-endian 写入 out[0..1]，*out_len = 2；其他 nibble 返回 false。
