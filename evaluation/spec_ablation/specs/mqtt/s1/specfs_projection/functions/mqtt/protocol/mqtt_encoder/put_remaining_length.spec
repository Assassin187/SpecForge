[PROMPT]
Implement function `put_remaining_length`. Responsibility: 将 Remaining Length 按 MQTT 规则写入输出缓冲

[RELY]
None.

[GUARANTEE]
```c
static void put_remaining_length(uint8_t* out, size_t* pos, size_t len);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为可写输出缓冲 out、游标 pos 与 Remaining Length 数值 len。

**Post-Condition**:
- 按 MQTT 7bit+续位格式写入一个或多个字节，并将 *pos 推进到写入末尾。

**Invariant**:
- 调用方保证 out 剩余空间足够
- 除最后一字节外均设置 continuation bit

**System Algorithm**:
- 将长度按 7bit+续位格式写入输出缓冲。
