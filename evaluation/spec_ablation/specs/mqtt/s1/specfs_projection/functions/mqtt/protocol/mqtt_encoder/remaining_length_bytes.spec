[PROMPT]
Implement function `remaining_length_bytes`. Responsibility: 计算 Remaining Length 采用 MQTT 可变长编码所需字节数

[RELY]
None.

[GUARANTEE]
```c
static size_t remaining_length_bytes(size_t len);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为待编码的 Remaining Length 数值 len。

**Post-Condition**:
- 返回 MQTT Remaining Length 可变长编码所需字节数，最少为 1。

**Invariant**:
- 函数不修改外部状态
- 按 128 进制分组计算编码长度

**System Algorithm**:
- 按 MQTT Remaining Length 编码规则计算所需字节数。
