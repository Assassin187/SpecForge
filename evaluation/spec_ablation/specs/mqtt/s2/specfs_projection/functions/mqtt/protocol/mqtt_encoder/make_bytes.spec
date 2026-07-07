[PROMPT]
Implement function `make_bytes`. Responsibility: 分配指定长度的 mqtt_bytes_t 缓冲包装对象

[RELY]
- STRUCT `mqtt_bytes_t`
  role: 编码缓冲封装结构体
```c
typedef struct mqtt_bytes {
    uint8_t* data;
    size_t len;
} mqtt_bytes_t;
```

[GUARANTEE]
```c
static mqtt_bytes_t make_bytes(size_t len);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为需要分配的编码缓冲长度 len。

**Post-Condition**:
- 返回 mqtt_bytes_t；分配成功时 data 非空且 len 为请求长度，分配失败时 data 为 NULL 且 len 为 0。

**Invariant**:
- 返回的 data 由调用方通过 mqtt_bytes_free 释放
- 不初始化 data 内容

**System Algorithm**:
- 按请求长度分配 mqtt_bytes_t.data 并设置 len。
