#include "smtp_server.h"

#include "../auth/smtp_auth.h"
#include "../network/tcp_server.h"
#include "../protocol/smtp_command.h"
#include "../protocol/smtp_response.h"
#include "../storage/mail_store.h"
#include "smtp_session.h"

#include <ctype.h>
#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

typedef struct smtp_session_node {
    smtp_session_t* session;
    struct smtp_session_node* next;
} smtp_session_node_t;

struct smtp_server {
    uint16_t port;
    char hostname[128];
    char mail_root[PATH_MAX];
    smtp_tcp_server_t* tcp;
    smtp_session_node_t* sessions;
};

static smtp_session_t* find_session(smtp_server_t* server, int fd) {
    smtp_session_node_t* cur = server->sessions;
    while (cur != NULL) {
        if (cur->session->control_fd == fd) {
            return cur->session;
        }
        cur = cur->next;
    }
    return NULL;
}

static int add_session(smtp_server_t* server, smtp_session_t* session) {
    smtp_session_node_t* node = (smtp_session_node_t*)calloc(1, sizeof(*node));
    if (node == NULL) {
        return -1;
    }
    node->session = session;
    node->next = server->sessions;
    server->sessions = node;
    return 0;
}

static void remove_session(smtp_server_t* server, int fd) {
    smtp_session_node_t** cur = &server->sessions;
    while (*cur != NULL) {
        if ((*cur)->session->control_fd == fd) {
            smtp_session_node_t* dead = *cur;
            *cur = dead->next;
            smtp_session_destroy(dead->session);
            free(dead);
            return;
        }
        cur = &(*cur)->next;
    }
}

static void close_control(smtp_server_t* server, smtp_session_t* session) {
    smtp_tcp_server_close_client(server->tcp, session->control_fd);
}

static int queue_raw(smtp_server_t* server, smtp_session_t* session, const char* text) {
    if (smtp_connection_queue_str(session->conn, text) < 0) {
        return -1;
    }
    return smtp_tcp_server_update_interest(server->tcp, session->control_fd, true);
}

static int queue_code(smtp_server_t* server, smtp_session_t* session, int code, const char* text) {
    char line[2048];
    if (smtp_response_format(line, sizeof(line), code, text) < 0) {
        return -1;
    }
    return queue_raw(server, session, line);
}

static int flush_control_now(smtp_server_t* server, smtp_session_t* session) {
    const int rc = smtp_connection_flush(session->conn);
    if (rc < 0) {
        return -1;
    }
    return smtp_tcp_server_update_interest(server->tcp, session->control_fd, smtp_connection_has_pending(session->conn));
}

static int append_message_line(smtp_session_t* session, const char* raw_line) {
    const char* line = raw_line;
    if (line[0] == '.' && line[1] != '\0') {
        ++line;
    }

    const size_t line_len = strlen(line);
    const size_t need = line_len + 2;
    if (session->message_len + need > SMTP_MAX_MESSAGE_SIZE) {
        session->message_too_large = true;
        return 0;
    }

    memcpy(session->message_buf + session->message_len, line, line_len);
    session->message_len += line_len;
    session->message_buf[session->message_len++] = '\r';
    session->message_buf[session->message_len++] = '\n';
    session->message_buf[session->message_len] = '\0';
    return 0;
}

static void handle_data_line(smtp_server_t* server, smtp_session_t* session, const char* line) {
    if (strcmp(line, ".") != 0) {
        append_message_line(session, line);
        return;
    }

    session->in_data_mode = false;

    if (session->message_too_large) {
        queue_code(server, session, 552, "Message size exceeds fixed maximum message size");
        smtp_session_reset_transaction(session);
        return;
    }

    const char* rcpt_ptrs[SMTP_MAX_RECIPIENTS];
    for (size_t i = 0; i < session->rcpt_count; ++i) {
        rcpt_ptrs[i] = session->rcpt_to[i];
    }

    smtp_mail_t mail;
    memset(&mail, 0, sizeof(mail));
    mail.helo_name = session->helo_name;
    mail.auth_user = session->auth_user;
    mail.mail_from = session->mail_from;
    mail.rcpt_to = rcpt_ptrs;
    mail.rcpt_count = session->rcpt_count;
    mail.data = session->message_buf;
    mail.data_len = session->message_len;

    char saved[PATH_MAX];
    if (smtp_mail_store_write(server->mail_root, &mail, saved, sizeof(saved)) < 0) {
        queue_code(server, session, 451, "Requested action aborted: local error in processing");
        smtp_session_reset_transaction(session);
        return;
    }

    queue_code(server, session, 250, "Message accepted for delivery");
    smtp_session_reset_transaction(session);
}

static int parse_mailbox_arg(const char* arg, const char* key, char* out, size_t out_len) {
    if (arg == NULL || key == NULL || out == NULL || out_len == 0) {
        return -1;
    }

    while (*arg == ' ' || *arg == '\t') {
        ++arg;
    }

    const size_t klen = strlen(key);
    if (strncasecmp(arg, key, klen) != 0) {
        return -1;
    }

    const char* p = arg + klen;
    while (*p == ' ' || *p == '\t') {
        ++p;
    }

    char buf[512];
    size_t n = 0;

    if (*p == '<') {
        ++p;
        while (*p != '\0' && *p != '>' && n < sizeof(buf) - 1) {
            buf[n++] = *p++;
        }
        if (*p != '>') {
            return -1;
        }
    } else {
        while (*p != '\0' && !isspace((unsigned char)*p) && n < sizeof(buf) - 1) {
            buf[n++] = *p++;
        }
    }

    while (n > 0 && (buf[n - 1] == ' ' || buf[n - 1] == '\t')) {
        --n;
    }
    buf[n] = '\0';

    if (n == 0 || n >= out_len) {
        return -1;
    }

    memcpy(out, buf, n + 1);
    return 0;
}

static void begin_login_auth(smtp_server_t* server, smtp_session_t* session) {
    session->auth_state = SMTP_AUTH_STATE_LOGIN_WAIT_USER;
    queue_code(server, session, 334, "VXNlcm5hbWU6");
}

static int decode_b64_to_text(const char* b64, char* out, size_t out_len) {
    unsigned char tmp[1024];
    size_t dec_len = 0;
    if (smtp_auth_decode_base64(b64, tmp, sizeof(tmp) - 1, &dec_len) < 0) {
        return -1;
    }
    tmp[dec_len] = '\0';

    if (dec_len == 0 || dec_len >= out_len) {
        return -1;
    }
    memcpy(out, tmp, dec_len + 1);
    return 0;
}

static void finish_auth_if_valid(
    smtp_server_t* server,
    smtp_session_t* session,
    const char* user,
    const char* pass) {
    if (smtp_auth_validate(user, pass)) {
        session->authenticated = true;
        strncpy(session->auth_user, user, sizeof(session->auth_user) - 1);
        session->auth_user[sizeof(session->auth_user) - 1] = '\0';
        queue_code(server, session, 235, "Authentication successful");
    } else {
        session->authenticated = false;
        session->auth_user[0] = '\0';
        queue_code(server, session, 535, "Authentication credentials invalid");
    }
    smtp_session_reset_auth_exchange(session);
}

static void handle_auth_continuation(smtp_server_t* server, smtp_session_t* session, const char* line) {
    if (session->auth_state == SMTP_AUTH_STATE_LOGIN_WAIT_USER) {
        if (decode_b64_to_text(line, session->auth_login_user, sizeof(session->auth_login_user)) < 0) {
            queue_code(server, session, 501, "Invalid base64 in AUTH LOGIN username");
            smtp_session_reset_auth_exchange(session);
            return;
        }
        session->auth_state = SMTP_AUTH_STATE_LOGIN_WAIT_PASS;
        queue_code(server, session, 334, "UGFzc3dvcmQ6");
        return;
    }

    if (session->auth_state == SMTP_AUTH_STATE_LOGIN_WAIT_PASS) {
        char pass[256];
        if (decode_b64_to_text(line, pass, sizeof(pass)) < 0) {
            queue_code(server, session, 501, "Invalid base64 in AUTH LOGIN password");
            smtp_session_reset_auth_exchange(session);
            return;
        }
        finish_auth_if_valid(server, session, session->auth_login_user, pass);
        return;
    }

    if (session->auth_state == SMTP_AUTH_STATE_PLAIN_WAIT_BLOB) {
        unsigned char blob[1024];
        size_t blob_len = 0;
        char user[256];
        char pass[256];
        if (smtp_auth_decode_base64(line, blob, sizeof(blob), &blob_len) < 0 ||
            smtp_auth_parse_plain_blob(blob, blob_len, user, sizeof(user), pass, sizeof(pass)) < 0) {
            queue_code(server, session, 501, "Invalid AUTH PLAIN payload");
            smtp_session_reset_auth_exchange(session);
            return;
        }
        finish_auth_if_valid(server, session, user, pass);
        return;
    }
}

static int ensure_ready_for_mail(smtp_server_t* server, smtp_session_t* session) {
    if (!session->greeted) {
        queue_code(server, session, 503, "Send HELO/EHLO first");
        return 0;
    }
    if (!session->authenticated) {
        queue_code(server, session, 530, "Authentication required");
        return 0;
    }
    return 1;
}

static void handle_auth(smtp_server_t* server, smtp_session_t* session, const smtp_command_t* cmd) {
    if (!session->greeted) {
        queue_code(server, session, 503, "Send HELO/EHLO first");
        return;
    }
    if (session->authenticated) {
        queue_code(server, session, 503, "Already authenticated");
        return;
    }
    if (!cmd->has_arg) {
        queue_code(server, session, 501, "Missing AUTH mechanism");
        return;
    }

    char arg_copy[1024];
    strncpy(arg_copy, cmd->arg, sizeof(arg_copy) - 1);
    arg_copy[sizeof(arg_copy) - 1] = '\0';

    char* mech = arg_copy;
    while (*mech == ' ' || *mech == '\t') {
        ++mech;
    }

    char* rest = mech;
    while (*rest != '\0' && !isspace((unsigned char)*rest)) {
        *rest = (char)toupper((unsigned char)*rest);
        ++rest;
    }
    if (*rest != '\0') {
        *rest++ = '\0';
        while (*rest == ' ' || *rest == '\t') {
            ++rest;
        }
    }

    if (strcmp(mech, "LOGIN") == 0) {
        if (*rest == '\0') {
            begin_login_auth(server, session);
            return;
        }

        if (decode_b64_to_text(rest, session->auth_login_user, sizeof(session->auth_login_user)) < 0) {
            queue_code(server, session, 501, "Invalid AUTH LOGIN initial response");
            smtp_session_reset_auth_exchange(session);
            return;
        }
        session->auth_state = SMTP_AUTH_STATE_LOGIN_WAIT_PASS;
        queue_code(server, session, 334, "UGFzc3dvcmQ6");
        return;
    }

    if (strcmp(mech, "PLAIN") == 0) {
        if (*rest == '\0') {
            session->auth_state = SMTP_AUTH_STATE_PLAIN_WAIT_BLOB;
            queue_code(server, session, 334, "");
            return;
        }

        unsigned char blob[1024];
        size_t blob_len = 0;
        char user[256];
        char pass[256];
        if (smtp_auth_decode_base64(rest, blob, sizeof(blob), &blob_len) < 0 ||
            smtp_auth_parse_plain_blob(blob, blob_len, user, sizeof(user), pass, sizeof(pass)) < 0) {
            queue_code(server, session, 501, "Invalid AUTH PLAIN payload");
            return;
        }
        finish_auth_if_valid(server, session, user, pass);
        return;
    }

    queue_code(server, session, 504, "Unrecognized authentication type");
}

static void handle_command(smtp_server_t* server, smtp_session_t* session, const smtp_command_t* cmd) {
    switch (cmd->kind) {
        case SMTP_CMD_HELO:
            if (!cmd->has_arg) {
                queue_code(server, session, 501, "Missing HELO domain");
                break;
            }
            session->greeted = true;
            strncpy(session->helo_name, cmd->arg, sizeof(session->helo_name) - 1);
            session->helo_name[sizeof(session->helo_name) - 1] = '\0';
            smtp_session_reset_transaction(session);
            smtp_session_reset_auth_exchange(session);
            queue_code(server, session, 250, server->hostname);
            break;
        case SMTP_CMD_EHLO: {
            if (!cmd->has_arg) {
                queue_code(server, session, 501, "Missing EHLO domain");
                break;
            }
            session->greeted = true;
            strncpy(session->helo_name, cmd->arg, sizeof(session->helo_name) - 1);
            session->helo_name[sizeof(session->helo_name) - 1] = '\0';
            smtp_session_reset_transaction(session);
            smtp_session_reset_auth_exchange(session);

            char caps[1024];
            if (smtp_response_format_ehlo_caps(caps, sizeof(caps), server->hostname, SMTP_MAX_MESSAGE_SIZE) < 0) {
                queue_code(server, session, 451, "Temporary server failure");
                break;
            }
            queue_raw(server, session, caps);
            break;
        }
        case SMTP_CMD_AUTH:
            handle_auth(server, session, cmd);
            break;
        case SMTP_CMD_MAIL: {
            if (!ensure_ready_for_mail(server, session)) {
                break;
            }
            if (!cmd->has_arg || parse_mailbox_arg(cmd->arg, "FROM:", session->mail_from, sizeof(session->mail_from)) < 0) {
                queue_code(server, session, 501, "Syntax: MAIL FROM:<address>");
                break;
            }
            session->has_mail_from = true;
            session->rcpt_count = 0;
            session->message_len = 0;
            session->message_too_large = false;
            queue_code(server, session, 250, "Sender OK");
            break;
        }
        case SMTP_CMD_RCPT: {
            if (!ensure_ready_for_mail(server, session)) {
                break;
            }
            if (!session->has_mail_from) {
                queue_code(server, session, 503, "Need MAIL FROM before RCPT TO");
                break;
            }
            if (session->rcpt_count >= SMTP_MAX_RECIPIENTS) {
                queue_code(server, session, 452, "Too many recipients");
                break;
            }
            if (!cmd->has_arg ||
                parse_mailbox_arg(
                    cmd->arg,
                    "TO:",
                    session->rcpt_to[session->rcpt_count],
                    sizeof(session->rcpt_to[session->rcpt_count])) < 0) {
                queue_code(server, session, 501, "Syntax: RCPT TO:<address>");
                break;
            }
            ++session->rcpt_count;
            queue_code(server, session, 250, "Recipient OK");
            break;
        }
        case SMTP_CMD_DATA:
            if (!ensure_ready_for_mail(server, session)) {
                break;
            }
            if (!session->has_mail_from || session->rcpt_count == 0) {
                queue_code(server, session, 503, "Need MAIL FROM and RCPT TO before DATA");
                break;
            }
            session->in_data_mode = true;
            session->message_len = 0;
            session->message_too_large = false;
            session->message_buf[0] = '\0';
            queue_code(server, session, 354, "End data with <CR><LF>.<CR><LF>");
            break;
        case SMTP_CMD_RSET:
            smtp_session_reset_transaction(session);
            smtp_session_reset_auth_exchange(session);
            queue_code(server, session, 250, "OK");
            break;
        case SMTP_CMD_NOOP:
            queue_code(server, session, 250, "OK");
            break;
        case SMTP_CMD_VRFY:
            queue_code(server, session, 252, "Cannot VRFY user, but will accept message");
            break;
        case SMTP_CMD_QUIT:
            queue_code(server, session, 221, "Bye");
            flush_control_now(server, session);
            close_control(server, session);
            break;
        default:
            queue_code(server, session, 500, "Command unrecognized");
            break;
    }
}

static void on_accept_cb(void* user, int fd, const struct sockaddr_storage* peer, socklen_t peer_len) {
    (void)peer;
    (void)peer_len;

    smtp_server_t* server = (smtp_server_t*)user;
    smtp_session_t* session = smtp_session_create(fd);
    if (session == NULL) {
        smtp_tcp_server_close_client(server->tcp, fd);
        return;
    }

    if (add_session(server, session) < 0) {
        smtp_session_destroy(session);
        smtp_tcp_server_close_client(server->tcp, fd);
        return;
    }

    char greet[256];
    snprintf(greet, sizeof(greet), "%s ESMTP ready", server->hostname);
    queue_code(server, session, 220, greet);
}

static void on_event_cb(void* user, int fd, uint32_t events) {
    smtp_server_t* server = (smtp_server_t*)user;
    smtp_session_t* session = find_session(server, fd);
    if (session == NULL) {
        return;
    }

    if ((events & SMTP_IO_WRITABLE) != 0u) {
        const int frc = smtp_connection_flush(session->conn);
        if (frc < 0) {
            close_control(server, session);
            return;
        }
        if (!smtp_connection_has_pending(session->conn)) {
            smtp_tcp_server_update_interest(server->tcp, session->control_fd, false);
        }
    }

    if ((events & SMTP_IO_READABLE) != 0u) {
        const int rrc = smtp_connection_read(session->conn);
        if (rrc == 0 || rrc == -1) {
            close_control(server, session);
            return;
        }

        char* line;
        while ((line = smtp_connection_pop_line(session->conn)) != NULL) {
            if (strlen(line) > 2048) {
                queue_code(server, session, 500, "Line too long");
                free(line);
                continue;
            }

            if (session->in_data_mode) {
                handle_data_line(server, session, line);
                free(line);
                continue;
            }

            if (session->auth_state != SMTP_AUTH_STATE_NONE) {
                handle_auth_continuation(server, session, line);
                free(line);
                continue;
            }

            smtp_command_t cmd;
            if (smtp_command_parse(line, &cmd) == 0) {
                handle_command(server, session, &cmd);
            } else {
                queue_code(server, session, 500, "Bad command syntax");
            }
            free(line);

            session = find_session(server, fd);
            if (session == NULL) {
                return;
            }
        }

        flush_control_now(server, session);
    }
}

static void on_close_cb(void* user, int fd) {
    smtp_server_t* server = (smtp_server_t*)user;
    remove_session(server, fd);
}

smtp_server_t* smtp_server_create(uint16_t port, const char* mail_root) {
    if (mail_root == NULL || mail_root[0] == '\0') {
        return NULL;
    }

    smtp_server_t* server = (smtp_server_t*)calloc(1, sizeof(*server));
    if (server == NULL) {
        return NULL;
    }

    server->port = port;
    snprintf(server->hostname, sizeof(server->hostname), "localhost");
    if (snprintf(server->mail_root, sizeof(server->mail_root), "%s", mail_root) < 0 ||
        strlen(mail_root) >= sizeof(server->mail_root)) {
        free(server);
        return NULL;
    }

    if (smtp_mail_store_init(server->mail_root) < 0) {
        free(server);
        return NULL;
    }

    smtp_tcp_callbacks_t cb;
    memset(&cb, 0, sizeof(cb));
    cb.on_accept = on_accept_cb;
    cb.on_event = on_event_cb;
    cb.on_close = on_close_cb;

    server->tcp = smtp_tcp_server_create(port, cb, server);
    if (server->tcp == NULL) {
        free(server);
        return NULL;
    }

    return server;
}

int smtp_server_start(smtp_server_t* server) {
    if (server == NULL) {
        return -1;
    }
    return smtp_tcp_server_start(server->tcp);
}

int smtp_server_run(smtp_server_t* server) {
    if (server == NULL) {
        return -1;
    }
    return smtp_tcp_server_run(server->tcp);
}

void smtp_server_stop(smtp_server_t* server) {
    if (server != NULL) {
        smtp_tcp_server_stop(server->tcp);
    }
}

void smtp_server_destroy(smtp_server_t* server) {
    if (server == NULL) {
        return;
    }

    smtp_session_node_t* cur = server->sessions;
    while (cur != NULL) {
        smtp_session_node_t* next = cur->next;
        smtp_session_destroy(cur->session);
        free(cur);
        cur = next;
    }

    smtp_tcp_server_destroy(server->tcp);
    free(server);
}
