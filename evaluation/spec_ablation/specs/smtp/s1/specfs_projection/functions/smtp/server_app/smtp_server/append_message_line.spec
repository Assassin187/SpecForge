[PROMPT]
Implement function `append_message_line`. Responsibility: 向 DATA 缓冲追加一行，处理 dot-stuffing 并追加 CRLF

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
static int append_message_line(smtp_session_t* session, const char* raw_line);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：session(smtp_session_t*，可为 NULL，BORROWED)；raw_line(const char*，可为 NULL，BORROWED)。

**Post-Condition**:
- 返回值表达向 DATA 缓冲追加一行，处理 dot-stuffing 并追加 CRLF的结果；成功、失败和特殊分支按当前 C 实现的返回码区分。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 维护 session->message_buf：追加行内容与 CRLF
- 维护 session->message_len：增加写入长度
- 维护 session->message_too_large：超出 SMTP_MAX_MESSAGE_SIZE 时置 true
- 遵循 SMTP_DATA.dot_stuffed_line 映射规则：行首为两个字符以上且首字符为 '.' 时去掉一个前导 dot
- 遵循 SMTP_DATA.line_ending 映射规则：每个 DATA 行以 CRLF 形式存入 message_buf

**System Algorithm**:
- 向 DATA 缓冲追加一行，处理 dot-stuffing 并追加 CRLF。
