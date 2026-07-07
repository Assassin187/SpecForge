[PROMPT]
Implement function `set_nonblocking`. Responsibility: 设置 fd 为非阻塞模式的通用辅助函数

[RELY]
None.

[GUARANTEE]
```c
static bool set_nonblocking(int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为已打开的文件描述符 fd。

**Post-Condition**:
- 成功返回 true 且 fd 保留原 flags 并增加 O_NONBLOCK；F_GETFL 或 F_SETFL 失败返回 false。

**Invariant**:
- 不关闭 fd
- 除 O_NONBLOCK 外不有意清除原有文件状态标志

**System Algorithm**:
- 读取 fd 当前 flags，若成功则追加 O_NONBLOCK 并写回。
