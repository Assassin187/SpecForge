[PROMPT]
Implement function `sockaddr_to_string`. Responsibility: 将 IPv4 sockaddr 转换为 ip:port 字符串

[RELY]
- STRUCT `struct sockaddr_in`
  role: 对端地址结构体
  declaration: external dependency; canonical declaration unavailable.

[GUARANTEE]
```c
static char* sockaddr_to_string(const struct sockaddr_in* addr);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为有效 IPv4 sockaddr_in 指针 addr。

**Post-Condition**:
- 成功返回堆分配的 ip:port 字符串，调用方负责 free；strdup 失败时返回 NULL。

**Invariant**:
- 不修改 addr
- 返回字符串表示网络字节序端口转换后的主机序端口

**System Algorithm**:
- 将 sockaddr_in 中地址和端口格式化为 ip:port 字符串并返回 strdup 副本。
