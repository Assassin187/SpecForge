[PROMPT]
Implement function `coap_code_detail`. Responsibility: 提取 CoAP code 的 5-bit detail

[RELY]
None.

[GUARANTEE]
```c
uint8_t coap_code_detail(uint8_t code);
```

[SPECIFICATION]
**Pre-Condition**:
- code 是完整 CoAP code byte。

**Post-Condition**:
- 返回 code detail，范围 0..31。

**Invariant**:
- 不修改输入 code。

**System Algorithm**:
- 将 code 与 0x1fu 相与，提取低 5-bit detail。
