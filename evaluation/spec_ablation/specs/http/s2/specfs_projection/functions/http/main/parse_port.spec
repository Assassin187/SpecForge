[PROMPT]
Implement function `parse_port`. Responsibility: strtol 解析端口字符串为 uint16_t；无效输入返回 0

[RELY]
None.

[GUARANTEE]
```c
static uint16_t parse_port(const char* s);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入端口字符串。

**Post-Condition**:
- 返回解析后的端口号；无效输入返回 0。

**Invariant**:
- 合法端口范围为 1..65535
- 无效输入统一返回 0 表示使用默认端口
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。

**System Algorithm**:
- strtol(s, NULL, 10) 解析；无效或超出范围返回 0。
