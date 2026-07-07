[PROMPT]
Implement function `mqtt_session_create`. Responsibility: 创建会话对象并初始化连接相关默认状态

[RELY]
- STRUCT `struct mqtt_session`
  role: 会话私有实现结构
```c
struct mqtt_session;
```

- FUNC `mqtt_connection_t`
  role: 依赖外部连接对象类型
  declaration: external dependency; canonical declaration unavailable.

[GUARANTEE]
```c
mqtt_session_t* mqtt_session_create(mqtt_connection_t* conn);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: on_accept 回调接收新连接后由 broker 调用
- Precondition: conn 为有效连接指针；调用方负责连接生命周期
- Input: 输入为底层连接 conn

**Post-Condition**:
- State Change: 产生新的 session 对象，初始为未认证状态
- Response: 成功返回新建 session 指针，失败返回 NULL

**Invariant**:
- None specified.

**System Algorithm**:
- 若 conn 为空返回 NULL；否则分配 session 并设置 conn、connected=false、client_id=NULL、clean_session=true、keep_alive=0
