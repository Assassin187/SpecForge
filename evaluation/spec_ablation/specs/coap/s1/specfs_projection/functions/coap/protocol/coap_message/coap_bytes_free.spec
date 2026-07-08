[PROMPT]
Implement function `coap_bytes_free`. Responsibility: 释放编码结果 data，并将 data/len 复位

[RELY]
- STRUCT `coap_bytes_t`
  role: 编码结果动态字节缓冲，由 coap_bytes_free 释放
```c
typedef struct coap_bytes {
    uint8_t* data;
    size_t len;
} coap_bytes_t;
```

[GUARANTEE]
```c
void coap_bytes_free(coap_bytes_t* bytes);
```

[SPECIFICATION]
**Pre-Condition**:
- bytes 是可能为空的 encoded bytes 对象。

**Post-Condition**:
- 无返回值；非空 bytes 被复位为空缓冲状态。

**Invariant**:
- 只释放 data，不释放 coap_bytes_t 对象本身。

**System Algorithm**:
- 若 bytes 为空直接返回。否则 free(bytes->data)，再将 data 置 NULL、len 置 0。
