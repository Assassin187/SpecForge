[PROMPT]
Implement function `http_request_init`. Responsibility: memset 清零整个 http_request_t 结构体；空指针安全返回

[RELY]
None.

[GUARANTEE]
```c
void http_request_init(http_request_t* req);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 req 指针。

**Post-Condition**:
- 通过副作用清零结构体。

**Invariant**:
- 空指针输入时无副作用直接返回
- 清零后结构体所有字段为 0/NULL
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- 空安全检查→memset(req, 0, sizeof(*req))。
