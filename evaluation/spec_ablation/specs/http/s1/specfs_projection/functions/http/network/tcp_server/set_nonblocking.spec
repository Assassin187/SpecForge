[PROMPT]
Implement function `set_nonblocking`. Responsibility: 通过 fcntl F_GETFL/F_SETFL 为 fd 设置 O_NONBLOCK 标志

[RELY]
None.

[GUARANTEE]
```c
static bool set_nonblocking(int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 fd 参数。

**Post-Condition**:
- 成功返回 true，失败返回 false。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。

**System Algorithm**:
- fcntl(fd, F_GETFL) 获取当前标志，若成功则追加 O_NONBLOCK 并通过 fcntl(fd, F_SETFL) 写回。
