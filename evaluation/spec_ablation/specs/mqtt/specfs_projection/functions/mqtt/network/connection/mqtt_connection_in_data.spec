[PROMPT]
Implement function `mqtt_connection_in_data`. Responsibility: 返回输入缓冲起始地址供协议解码

[RELY]
- STRUCT `struct mqtt_connection`
  role: 连接私有状态结构体
```c
struct mqtt_connection;
```

[GUARANTEE]
```c
uint8_t* mqtt_connection_in_data(mqtt_connection_t* c);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 上层解码器读取网络输入缓冲时触发
- Precondition: 允许 c 为空
- Input: 输入为连接 c

**Post-Condition**:
- State Change: 只读，无状态修改
- Response: 返回缓冲区指针或 NULL

**Invariant**:
- None specified.

**System Algorithm**:
- c 非空返回 in_data 指针，否则返回 NULL
