[PROMPT]
Implement function `handle_data_line`. Responsibility: 处理 DATA mode 中的行：非终止行追加，'.' 终止并尝试落盘

[RELY]
- STRUCT `smtp_server_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_server smtp_server_t;
```

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

- STRUCT `smtp_mail_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_mail {
    const char* helo_name;
    const char* auth_user;
    const char* mail_from;
    const char* const* rcpt_to;
    size_t rcpt_count;
    const char* data;
    size_t data_len;
} smtp_mail_t;
```

- FUNC `append_message_line`
  role: 被该函数调用以完成子步骤
```c
static int append_message_line(smtp_session_t* session, const char* raw_line);
```

- FUNC `smtp_mail_store_write`
  role: 被该函数调用以完成子步骤
```c
int smtp_mail_store_write(const char* root_dir, const smtp_mail_t* mail, char* out_path, size_t out_path_len);
```

- FUNC `smtp_session_reset_transaction`
  role: 被该函数调用以完成子步骤
```c
void smtp_session_reset_transaction(smtp_session_t* session);
```

- FUNC `queue_code`
  role: 被该函数调用以完成子步骤
```c
static int queue_code(smtp_server_t* server, smtp_session_t* session, int code, const char* text);
```

[GUARANTEE]
```c
static void handle_data_line(smtp_server_t* server, smtp_session_t* session, const char* line);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(smtp_server_t*，可为 NULL，BORROWED)；session(smtp_session_t*，可为 NULL，BORROWED)；line(const char*，可为 NULL，BORROWED)。

**Post-Condition**:
- 无返回值；普通 DATA 行只追加到消息缓冲。终止行在 oversized 时产生 552，在落盘成功时产生 250，在落盘失败时产生 451；三种终止结果均退出 DATA mode 并清空当前 transaction。局部 rcpt_ptrs 的生命周期必须覆盖同步 smtp_mail_store_write 调用。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。
- 维护 session->in_data_mode：终止行时置 false
- 维护 session->message_too_large：决定是否返回 552
- 维护 session->message_buf：落盘 DATA 内容
- 遵循 SMTP_DATA.terminator 映射规则：单独一行 '.' 结束 DATA
- 遵循 SMTP_DATA.message_data 映射规则：终止前所有行累积到 session->message_buf
- session->rcpt_to 是连续的二维字符数组，smtp_mail_t.rcpt_to 是 pointer array；两者只能通过局部 rcpt_ptrs 逐项桥接
- smtp_mail_t.data_len 必须等于 session->message_len，落盘不得依赖 message_buf 的 NUL 终止
- smtp_mail_store_write 同步返回前 rcpt_ptrs 保持有效，函数返回后 storage 不得保留该局部数组

**System Algorithm**:
- line 不是单独的 '.' 时只调用 append_message_line(session, line) 并返回。终止行首先把 session->in_data_mode 置 false；若 message_too_large 为 true，则发送 552、reset transaction 并返回。否则构造局部 const char* rcpt_ptrs[SMTP_MAX_RECIPIENTS]，对 [0, session->rcpt_count) 逐项执行 rcpt_ptrs[i] = session->rcpt_to[i]，完整初始化 smtp_mail_t 的 helo_name、auth_user、mail_from、rcpt_to=rcpt_ptrs、rcpt_count、data 和 data_len 后，同步调用 smtp_mail_store_write。禁止把 session->rcpt_to 二维字符数组强转为 const char** 或 const char* const*。落盘成功发送 250，失败发送 451，随后均 reset transaction。
