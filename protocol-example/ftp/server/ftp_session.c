#include "ftp_session.h"

#include <stdlib.h>
#include <string.h>
#include <unistd.h>

ftp_session_t* ftp_session_create(int control_fd) {
    ftp_session_t* session = (ftp_session_t*)calloc(1, sizeof(*session));
    if (session == NULL) {
        return NULL;
    }

    session->control_fd = control_fd;
    session->conn = ftp_connection_create(control_fd);
    if (session->conn == NULL) {
        free(session);
        return NULL;
    }

    strcpy(session->cwd, "/");
    session->pasv_listen_fd = -1;
    return session;
}

void ftp_session_reset_pasv(ftp_session_t* session) {
    if (session == NULL) {
        return;
    }
    if (session->pasv_listen_fd >= 0) {
        close(session->pasv_listen_fd);
        session->pasv_listen_fd = -1;
    }
}

void ftp_session_destroy(ftp_session_t* session) {
    if (session == NULL) {
        return;
    }

    ftp_session_reset_pasv(session);
    ftp_connection_destroy(session->conn);
    free(session);
}
