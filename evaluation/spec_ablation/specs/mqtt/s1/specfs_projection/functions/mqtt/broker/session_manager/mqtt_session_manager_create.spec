[PROMPT]
Implement function `mqtt_session_manager_create`. Responsibility: 创建会话管理器容器对象

[RELY]
- STRUCT `struct mqtt_session_manager`
  role: 会话管理器私有结构
```c
struct mqtt_session_manager;
```

[GUARANTEE]
```c
mqtt_session_manager_t* mqtt_session_manager_create(void);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: broker 初始化阶段创建全局会话容器时触发
- Precondition: 无输入参数，无额外前置依赖
- Input: 无

**Post-Condition**:
- State Change: 生成新的空容器实例
- Response: 成功返回管理器指针，失败返回 NULL

**Invariant**:
- None specified.

**System Algorithm**:
- 调用 calloc 分配并零初始化管理器结构体，sessions/count/cap 初始为 0
