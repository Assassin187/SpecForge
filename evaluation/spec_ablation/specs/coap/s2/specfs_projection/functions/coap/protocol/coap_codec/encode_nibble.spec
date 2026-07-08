[PROMPT]
Implement function `encode_nibble`. Responsibility: 将 option delta/length 映射为直接值、13 或 14 nibble

[RELY]
None.

[GUARANTEE]
```c
static uint8_t encode_nibble(uint16_t value);
```

[SPECIFICATION]
**Pre-Condition**:
- value 是待编码的 option delta 或 option length。

**Post-Condition**:
- 返回可写入 option header 高/低 nibble 的 uint8_t。

**Invariant**:
- 函数不会返回 reserved nibble 15。

**System Algorithm**:
- 若 value < 13，直接返回 value 的低 8 位；若 13 <= value < 269，返回 nibble 13；若 value >= 269，返回 nibble 14。
