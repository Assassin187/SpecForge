[PROMPT]
Implement function `smtp_session_reset_transaction`. Responsibility: 清空 MAIL/RCPT/DATA transaction 状态，不影响 greeting/authenticated

[RELY]
- STRUCT `smtp_session_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_session {
    int control_fd;
    smtp_connection_t* conn;
    bool greeted;
    bool authenticated;
    char helo_name[256];
    char auth_user[256];
    smtp_auth_state_t auth_state;
    char auth_login_user[256];
    char mail_from[512];
    bool has_mail_from;
    char rcpt_to[SMTP_MAX_RECIPIENTS][512];
    size_t rcpt_count;
    bool in_data_mode;
    char message_buf[SMTP_MAX_MESSAGE_SIZE + 1];
    size_t message_len;
    bool message_too_large;
} smtp_session_t;
```

[GUARANTEE]
```c
void smtp_session_reset_transaction(smtp_session_t* session);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：session(smtp_session_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 无返回值；非 NULL session 的 sender、recipient 和 DATA transaction 状态全部清空，greeting 与 authentication 状态保持不变。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 维护 session->mail_from：清空 sender
- 维护 session->has_mail_from：置 false
- 维护 session->rcpt_count：置 0
- 维护 session->in_data_mode：置 false
- 维护 session->message_len：置 0
- 维护 session->message_too_large：置 false
- 开始新的 MAIL transaction 时必须先调用本函数再写入新 sender，或由调用方只清理旧 recipient/DATA 字段；禁止先写入新 mail_from 再调用本函数

**System Algorithm**:
- session 为 NULL 时直接返回；否则把 mail_from 首字节置 NUL、has_mail_from 置 false、rcpt_count 置 0、in_data_mode 置 false、message_len 置 0、message_too_large 置 false。不得修改 greeted、authenticated、helo_name 或 auth_user。
