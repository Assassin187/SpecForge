#include "smtp_session.h"

#include <stdlib.h>
#include <string.h>

smtp_session_t* smtp_session_create(int control_fd) {
    smtp_session_t* session = (smtp_session_t*)calloc(1, sizeof(*session));
    if (session == NULL) {
        return NULL;
    }

    session->control_fd = control_fd;
    session->conn = smtp_connection_create(control_fd);
    if (session->conn == NULL) {
        free(session);
        return NULL;
    }

    return session;
}

void smtp_session_reset_transaction(smtp_session_t* session) {
    if (session == NULL) {
        return;
    }

    session->has_mail_from = false;
    session->mail_from[0] = '\0';
    session->rcpt_count = 0;
    session->in_data_mode = false;
    session->message_len = 0;
    session->message_too_large = false;
    session->message_buf[0] = '\0';
}

void smtp_session_reset_auth_exchange(smtp_session_t* session) {
    if (session == NULL) {
        return;
    }
    session->auth_state = SMTP_AUTH_STATE_NONE;
    session->auth_login_user[0] = '\0';
}

void smtp_session_destroy(smtp_session_t* session) {
    if (session == NULL) {
        return;
    }

    smtp_connection_destroy(session->conn);
    free(session);
}
