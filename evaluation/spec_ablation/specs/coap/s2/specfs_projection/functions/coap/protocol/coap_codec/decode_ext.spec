[PROMPT]
Implement function `decode_ext`. Responsibility: 从 datagram 解码 nibble 与 extended bytes，并推进 offset；nibble 15 或截断失败

[RELY]
None.

[GUARANTEE]
```c
static bool decode_ext(const uint8_t* data, size_t len, size_t* off, uint8_t nibble, uint16_t* out_value);
```

[SPECIFICATION]
**Pre-Condition**:
- data/len 表示完整 datagram，off 是相对同一 data 的 absolute offset，nibble 是 header nibble，out_value 接收解码值。

**Post-Condition**:
- 成功返回 true 并写出 decoded value；缺少扩展字节、reserved nibble 或输出参数无效返回 false。

**Invariant**:
- 调用方必须传入完整 datagram 的 data/len，而不是 data+offset 与 len-offset 的切片。
- off 在 nibble < 13 时不变，在 nibble 13/14 时只按实际扩展字节推进。

**System Algorithm**:
- 若 off 或 out_value 为空返回 false。nibble < 13 时把 out_value 设为 nibble，不移动 off。nibble == 13 时要求 *off < len，读取 data[*off]，结果为 13 + byte，并将 *off 加 1。nibble == 14 时要求 *off + 1 < len，读取两个 big-endian bytes，结果为 269 + value，并将 *off 加 2。nibble 15 或扩展字节越界返回 false。
