[PROMPT]
Implement function `write_all`. Responsibility: 处理 EINTR 并完整写出 len 字节

[RELY]
- FUNC `write`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

[GUARANTEE]
```c
static int write_all(int fd, const void* data, size_t len);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：fd(int，不可为 NULL，BORROWED)；data(const void*，可为 NULL，BORROWED)；len(size_t，不可为 NULL，BORROWED)。

**Post-Condition**:
- 成功完整写出 len 字节后返回 0；write 出错且非 EINTR 时返回 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。

**System Algorithm**:
- 处理 EINTR 并完整写出 len 字节。
