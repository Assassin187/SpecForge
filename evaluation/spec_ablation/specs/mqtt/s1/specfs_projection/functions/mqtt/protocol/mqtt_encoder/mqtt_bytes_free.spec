[PROMPT]
Implement function `mqtt_bytes_free`. Responsibility: 释放 mqtt_bytes_t 缓冲并复位字段

[RELY]
- STRUCT `mqtt_bytes_t`
  role: 编码字节缓冲结构体
```c
typedef struct mqtt_bytes {
    uint8_t* data;
    size_t len;
} mqtt_bytes_t;
```

[GUARANTEE]
```c
void mqtt_bytes_free(mqtt_bytes_t* b);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为 mqtt_bytes_t 指针 b（可为空）

**Post-Condition**:
- 无返回值；调用后 b 不再持有有效缓冲

**Invariant**:
- 可安全重复调用
- 释放后结构处于可复用空状态

**System Algorithm**:
- b 为空直接返回；否则 free(b->data) 并将 data 置 NULL、len 置 0
