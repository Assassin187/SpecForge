[PROMPT]
Implement function `coap_code_class`. Responsibility: 提取 CoAP code 的 3-bit class

[RELY]
None.

[GUARANTEE]
```c
uint8_t coap_code_class(uint8_t code);
```

[SPECIFICATION]
**Pre-Condition**:
- code 是完整 CoAP code byte。

**Post-Condition**:
- 返回 code class，范围 0..7。

**Invariant**:
- 不修改输入 code。

**System Algorithm**:
- 右移 5 位后与 0x7u 相与，提取高 3-bit class。
