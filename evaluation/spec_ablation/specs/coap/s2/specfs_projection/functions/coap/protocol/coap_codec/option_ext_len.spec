[PROMPT]
Implement function `option_ext_len`. Responsibility: 计算 option delta/length 使用的 extended byte 数

[RELY]
None.

[GUARANTEE]
```c
static size_t option_ext_len(uint16_t value);
```

[SPECIFICATION]
**Pre-Condition**:
- value 是 option delta 或 option length 的 unsigned integer 值。

**Post-Condition**:
- 返回 0、1 或 2。

**Invariant**:
- 该 helper 只计算长度，不写入缓冲区。

**System Algorithm**:
- 按 CoAP option nibble 扩展规则计算额外字节数：value < 13 不需要扩展字节；13 <= value < 269 需要 1 byte；value >= 269 需要 2 bytes。
