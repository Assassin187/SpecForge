[PROMPT]
Implement function `set_nonblocking`. Responsibility: 为 socket fd 追加 O_NONBLOCK flag

[RELY]
None.

[GUARANTEE]
```c
static bool set_nonblocking(int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- fd 是调用方提供的 open file descriptor。

**Post-Condition**:
- F_GETFL 失败或 F_SETFL 返回非 0 时返回 false；F_SETFL 返回 0 时返回 true。

**Invariant**:
- 函数不关闭 fd，也不修改除 O_NONBLOCK 以外的既有 flags。

**System Algorithm**:
- 先调用 fcntl(fd, F_GETFL, 0) 读取现有 flags；读取失败时立即返回 false。读取成功后调用 fcntl(fd, F_SETFL, flags | O_NONBLOCK)，只追加 O_NONBLOCK，不清除其他 flag。
