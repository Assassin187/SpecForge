[PROMPT]
Implement function `coap_make_code`. Responsibility: 将 3-bit class 与 5-bit detail 合成为 CoAP code

[RELY]
None.

[GUARANTEE]
```c
uint8_t coap_make_code(uint8_t code_class, uint8_t detail);
```

[SPECIFICATION]
**Pre-Condition**:
- code_class 与 detail 是待组合的 CoAP code 部分。

**Post-Condition**:
- 返回 3-bit class 与 5-bit detail 合成后的 code byte。

**Invariant**:
- class 只保留低 3 bits；detail 只保留低 5 bits。

**System Algorithm**:
- 计算 ((code_class & 0x7u) << 5) | (detail & 0x1fu)，并转换为 uint8_t。
