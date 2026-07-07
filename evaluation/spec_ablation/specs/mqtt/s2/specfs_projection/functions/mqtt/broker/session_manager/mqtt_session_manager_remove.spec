[PROMPT]
Implement function `mqtt_session_manager_remove`. Responsibility: 按 session_id 从管理器删除会话并保持数组紧凑

[RELY]
- STRUCT `struct mqtt_session_manager`
  role: 会话管理器私有结构
```c
struct mqtt_session_manager;
```

- FUNC `mqtt_session_id`
  role: 定位要删除的会话
```c
int mqtt_session_id(const mqtt_session_t* s);
```

- FUNC `mqtt_session_destroy`
  role: 销毁被移除会话
```c
void mqtt_session_destroy(mqtt_session_t* s);
```

[GUARANTEE]
```c
void mqtt_session_manager_remove(mqtt_session_manager_t* m, int session_id);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 连接关闭或主动踢会话时触发
- Precondition: m 可为空；session_id 为目标会话 fd
- Input: 输入为管理器 m 与待删除 session_id

**Post-Condition**:
- State Change: 命中时会话数量减少且数组顺序可能变化
- Response: 无返回值；删除结果体现在容器内部

**Invariant**:
- None specified.

**System Algorithm**:
- 线性扫描找到匹配会话后先销毁，再用末元素覆盖当前位置并 count--；未找到则无操作
