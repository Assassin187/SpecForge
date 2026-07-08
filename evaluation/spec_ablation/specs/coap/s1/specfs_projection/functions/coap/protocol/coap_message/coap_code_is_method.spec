[PROMPT]
Implement function `coap_code_is_method`. Responsibility: 判断 code 是否是 class 0 且 detail 为 GET/POST/PUT/DELETE

[RELY]
- FUNC `coap_code_class`
  role: 调用 coap_code_class 完成子步骤
```c
uint8_t coap_code_class(uint8_t code);
```

[GUARANTEE]
```c
bool coap_code_is_method(uint8_t code);
```

[SPECIFICATION]
**Pre-Condition**:
- code 是待分类的 CoAP code byte。

**Post-Condition**:
- GET/POST/PUT/DELETE method code 返回 true；其他 code 返回 false。

**Invariant**:
- 判断使用完整 code 的数值范围 1..4，不单独调用 coap_code_detail。

**System Algorithm**:
- 调用 coap_code_class(code)，并同时检查原始 code 值是否位于 1..4。只有 class 为 0 且 code >= 1 且 code <= 4 时视为 method。
