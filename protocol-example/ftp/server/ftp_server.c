#include "ftp_server.h"

#include "../auth/auth.h"
#include "../network/tcp_server.h"
#include "../protocol/ftp_command.h"
#include "../protocol/ftp_response.h"
#include "../resource/file_ops.h"
#include "ftp_session.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <netinet/in.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

typedef struct ftp_session_node {
    ftp_session_t* session;
    struct ftp_session_node* next;
} ftp_session_node_t;

struct ftp_server {
    uint16_t port;
    char root[PATH_MAX];
    ftp_tcp_server_t* tcp;
    ftp_session_node_t* sessions;
};

static ftp_session_t* find_session(ftp_server_t* server, int fd) {
    ftp_session_node_t* cur = server->sessions;
    while (cur != NULL) {
        if (cur->session->control_fd == fd) {
            return cur->session;
        }
        cur = cur->next;
    }
    return NULL;
}

static void remove_session(ftp_server_t* server, int fd) {
    ftp_session_node_t** cur = &server->sessions;
    while (*cur != NULL) {
        if ((*cur)->session->control_fd == fd) {
            ftp_session_node_t* dead = *cur;
            *cur = dead->next;
            ftp_session_destroy(dead->session);
            free(dead);
            return;
        }
        cur = &(*cur)->next;
    }
}

static int add_session(ftp_server_t* server, ftp_session_t* session) {
    ftp_session_node_t* node = (ftp_session_node_t*)calloc(1, sizeof(*node));
    if (node == NULL) {
        return -1;
    }
    node->session = session;
    node->next = server->sessions;
    server->sessions = node;
    return 0;
}

static int queue_response(ftp_server_t* server, ftp_session_t* session, int code, const char* text) {
    char line[2048];
    if (ftp_response_format(line, sizeof(line), code, text) < 0) {
        return -1;
    }
    if (ftp_connection_queue_str(session->conn, line) < 0) {
        return -1;
    }
    return ftp_tcp_server_update_interest(server->tcp, session->control_fd, true);
}

static int flush_control_now(ftp_server_t* server, ftp_session_t* session) {
    const int rc = ftp_connection_flush(session->conn);
    if (rc < 0) {
        return -1;
    }
    return ftp_tcp_server_update_interest(server->tcp, session->control_fd, ftp_connection_has_pending(session->conn));
}

static bool is_under_root(const char* root, const char* path) {
    const size_t n = strlen(root);
    if (strncmp(root, path, n) != 0) {
        return false;
    }
    return path[n] == '\0' || path[n] == '/';
}

static int normalize_virtual_path(const char* base, const char* arg, char* out, size_t out_len) {
    char work[2048];
    if (arg == NULL || arg[0] == '\0') {
        snprintf(work, sizeof(work), "%s", base);
    } else if (arg[0] == '/') {
        snprintf(work, sizeof(work), "%s", arg);
    } else if (strcmp(base, "/") == 0) {
        snprintf(work, sizeof(work), "/%s", arg);
    } else {
        snprintf(work, sizeof(work), "%s/%s", base, arg);
    }

    char* segs[256];
    size_t segc = 0;
    char* saveptr = NULL;
    char* tok = strtok_r(work, "/", &saveptr);
    while (tok != NULL) {
        if (strcmp(tok, ".") == 0 || strcmp(tok, "") == 0) {
            tok = strtok_r(NULL, "/", &saveptr);
            continue;
        }
        if (strcmp(tok, "..") == 0) {
            if (segc > 0) {
                --segc;
            }
            tok = strtok_r(NULL, "/", &saveptr);
            continue;
        }
        if (segc >= sizeof(segs) / sizeof(segs[0])) {
            return -1;
        }
        segs[segc++] = tok;
        tok = strtok_r(NULL, "/", &saveptr);
    }

    size_t used = 0;
    if (out_len < 2) {
        return -1;
    }
    out[used++] = '/';

    for (size_t i = 0; i < segc; ++i) {
        const size_t sl = strlen(segs[i]);
        if (used + sl + 1 >= out_len) {
            return -1;
        }
        memcpy(out + used, segs[i], sl);
        used += sl;
        if (i + 1 < segc) {
            out[used++] = '/';
        }
    }

    out[used] = '\0';
    return 0;
}

static int map_virtual_to_abs(const ftp_server_t* server, const char* vpath, char* out, size_t out_len) {
    if (strcmp(vpath, "/") == 0) {
        return snprintf(out, out_len, "%s", server->root) >= 0 ? 0 : -1;
    }
    return snprintf(out, out_len, "%s%s", server->root, vpath) >= 0 ? 0 : -1;
}

static int resolve_existing_path(
    const ftp_server_t* server,
    const ftp_session_t* session,
    const char* arg,
    char* out_abs,
    char* out_virtual,
    size_t out_len) {
    char vpath[PATH_MAX];
    if (normalize_virtual_path(session->cwd, arg, vpath, sizeof(vpath)) < 0) {
        return -1;
    }

    char abs_path[PATH_MAX];
    if (map_virtual_to_abs(server, vpath, abs_path, sizeof(abs_path)) < 0) {
        return -1;
    }

    char resolved[PATH_MAX];
    if (realpath(abs_path, resolved) == NULL) {
        return -1;
    }
    if (!is_under_root(server->root, resolved)) {
        errno = EACCES;
        return -1;
    }

    strncpy(out_abs, resolved, out_len - 1);
    out_abs[out_len - 1] = '\0';
    if (out_virtual != NULL) {
        strncpy(out_virtual, vpath, out_len - 1);
        out_virtual[out_len - 1] = '\0';
    }
    return 0;
}

static int resolve_new_path(
    const ftp_server_t* server,
    const ftp_session_t* session,
    const char* arg,
    char* out_abs,
    char* out_virtual,
    size_t out_len) {
    char vpath[PATH_MAX];
    if (normalize_virtual_path(session->cwd, arg, vpath, sizeof(vpath)) < 0) {
        return -1;
    }

    char abs_path[PATH_MAX];
    if (map_virtual_to_abs(server, vpath, abs_path, sizeof(abs_path)) < 0) {
        return -1;
    }

    char parent[PATH_MAX];
    strncpy(parent, abs_path, sizeof(parent) - 1);
    parent[sizeof(parent) - 1] = '\0';
    char* slash = strrchr(parent, '/');
    if (slash == NULL) {
        errno = EINVAL;
        return -1;
    }
    if (slash == parent) {
        slash[1] = '\0';
    } else {
        *slash = '\0';
    }

    char parent_resolved[PATH_MAX];
    if (realpath(parent, parent_resolved) == NULL) {
        return -1;
    }
    if (!is_under_root(server->root, parent_resolved)) {
        errno = EACCES;
        return -1;
    }

    strncpy(out_abs, abs_path, out_len - 1);
    out_abs[out_len - 1] = '\0';
    if (out_virtual != NULL) {
        strncpy(out_virtual, vpath, out_len - 1);
        out_virtual[out_len - 1] = '\0';
    }
    return 0;
}

static int set_blocking(int fd) {
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0) {
        return -1;
    }
    return fcntl(fd, F_SETFL, flags & ~O_NONBLOCK);
}

static int start_pasv(ftp_session_t* session, int control_fd, char* msg, size_t msg_len) {
    ftp_session_reset_pasv(session);

    const int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        return -1;
    }

    int opt = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons(0);

    if (bind(fd, (struct sockaddr*)&addr, sizeof(addr)) < 0 || listen(fd, 1) < 0) {
        close(fd);
        return -1;
    }

    struct sockaddr_in bound;
    socklen_t bound_len = sizeof(bound);
    if (getsockname(fd, (struct sockaddr*)&bound, &bound_len) < 0) {
        close(fd);
        return -1;
    }

    struct sockaddr_in local;
    socklen_t local_len = sizeof(local);
    memset(&local, 0, sizeof(local));
    if (getsockname(control_fd, (struct sockaddr*)&local, &local_len) < 0 || local.sin_family != AF_INET) {
        local.sin_family = AF_INET;
        local.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    }

    const uint32_t ip = ntohl(local.sin_addr.s_addr);
    const uint16_t port = ntohs(bound.sin_port);
    const unsigned h1 = (ip >> 24) & 0xff;
    const unsigned h2 = (ip >> 16) & 0xff;
    const unsigned h3 = (ip >> 8) & 0xff;
    const unsigned h4 = ip & 0xff;
    const unsigned p1 = (port >> 8) & 0xff;
    const unsigned p2 = port & 0xff;

    snprintf(msg, msg_len, "Entering Passive Mode (%u,%u,%u,%u,%u,%u)", h1, h2, h3, h4, p1, p2);
    session->pasv_listen_fd = fd;
    return 0;
}

static int open_data_connection(ftp_session_t* session) {
    if (session->pasv_listen_fd < 0) {
        errno = ENOTCONN;
        return -1;
    }

    struct pollfd pfd;
    pfd.fd = session->pasv_listen_fd;
    pfd.events = POLLIN;
    pfd.revents = 0;

    const int prc = poll(&pfd, 1, 10000);
    if (prc <= 0) {
        errno = ETIMEDOUT;
        ftp_session_reset_pasv(session);
        return -1;
    }

    int data_fd = accept(session->pasv_listen_fd, NULL, NULL);
    ftp_session_reset_pasv(session);
    if (data_fd < 0) {
        return -1;
    }

    if (set_blocking(data_fd) < 0) {
        close(data_fd);
        return -1;
    }

    return data_fd;
}

static int ensure_auth(ftp_server_t* server, ftp_session_t* session) {
    if (session->authenticated) {
        return 1;
    }
    queue_response(server, session, 530, "Please login with USER and PASS");
    return 0;
}

static void close_control(ftp_server_t* server, ftp_session_t* session) {
    ftp_tcp_server_close_client(server->tcp, session->control_fd);
}

static void handle_user(ftp_server_t* server, ftp_session_t* session, const ftp_command_t* cmd) {
    if (!cmd->has_arg) {
        queue_response(server, session, 501, "Missing username");
        return;
    }

    strncpy(session->pending_user, cmd->arg, sizeof(session->pending_user) - 1);
    session->pending_user[sizeof(session->pending_user) - 1] = '\0';
    session->authenticated = false;
    queue_response(server, session, 331, "User name okay, need password");
}

static void handle_pass(ftp_server_t* server, ftp_session_t* session, const ftp_command_t* cmd) {
    if (session->pending_user[0] == '\0') {
        queue_response(server, session, 503, "Login with USER first");
        return;
    }
    if (!cmd->has_arg) {
        queue_response(server, session, 501, "Missing password");
        return;
    }

    if (ftp_auth_validate(session->pending_user, cmd->arg)) {
        session->authenticated = true;
        queue_response(server, session, 230, "Login successful");
    } else {
        session->authenticated = false;
        queue_response(server, session, 530, "Authentication failed");
    }
}

static void handle_pwd(ftp_server_t* server, ftp_session_t* session) {
    char msg[1200];
    snprintf(msg, sizeof(msg), "\"%s\" is the current directory", session->cwd);
    queue_response(server, session, 257, msg);
}

static void handle_cwd(ftp_server_t* server, ftp_session_t* session, const ftp_command_t* cmd) {
    if (!cmd->has_arg) {
        queue_response(server, session, 501, "Missing directory");
        return;
    }

    char abs[PATH_MAX];
    char virt[PATH_MAX];
    if (resolve_existing_path(server, session, cmd->arg, abs, virt, sizeof(abs)) < 0) {
        queue_response(server, session, 550, "Failed to change directory");
        return;
    }

    struct stat st;
    if (stat(abs, &st) < 0 || !S_ISDIR(st.st_mode)) {
        queue_response(server, session, 550, "Not a directory");
        return;
    }

    strncpy(session->cwd, virt, sizeof(session->cwd) - 1);
    session->cwd[sizeof(session->cwd) - 1] = '\0';
    queue_response(server, session, 250, "Directory successfully changed");
}

static void handle_pasv(ftp_server_t* server, ftp_session_t* session) {
    char msg[256];
    if (start_pasv(session, session->control_fd, msg, sizeof(msg)) < 0) {
        queue_response(server, session, 425, "Cannot open passive connection");
        return;
    }
    queue_response(server, session, 227, msg);
}

static void with_data_transfer(
    ftp_server_t* server,
    ftp_session_t* session,
    int (*op)(int, const char*),
    const char* abs_path,
    const char* begin_msg,
    const char* ok_msg) {
    queue_response(server, session, 150, begin_msg);
    if (flush_control_now(server, session) < 0) {
        queue_response(server, session, 451, "Control channel flush failed");
        return;
    }

    const int data_fd = open_data_connection(session);
    if (data_fd < 0) {
        queue_response(server, session, 425, "Use PASV first or open data connection failed");
        return;
    }
    const int rc = op(data_fd, abs_path);
    close(data_fd);

    if (rc < 0) {
        queue_response(server, session, 551, "Transfer failed");
        return;
    }
    queue_response(server, session, 226, ok_msg);
}

static void handle_list(ftp_server_t* server, ftp_session_t* session, const ftp_command_t* cmd) {
    char abs[PATH_MAX];
    if (resolve_existing_path(server, session, cmd->has_arg ? cmd->arg : NULL, abs, NULL, sizeof(abs)) < 0) {
        queue_response(server, session, 550, "Directory not found");
        return;
    }

    queue_response(server, session, 150, "Here comes the directory listing");
    if (flush_control_now(server, session) < 0) {
        queue_response(server, session, 451, "Control channel flush failed");
        return;
    }

    const int data_fd = open_data_connection(session);
    if (data_fd < 0) {
        queue_response(server, session, 425, "Use PASV first or open data connection failed");
        return;
    }
    const int rc = ftp_fileops_list_dir(data_fd, abs);
    close(data_fd);

    if (rc < 0) {
        queue_response(server, session, 551, "LIST failed");
        return;
    }
    queue_response(server, session, 226, "Directory send OK");
}

static void handle_retr(ftp_server_t* server, ftp_session_t* session, const ftp_command_t* cmd) {
    if (!cmd->has_arg) {
        queue_response(server, session, 501, "Missing file path");
        return;
    }

    char abs[PATH_MAX];
    if (resolve_existing_path(server, session, cmd->arg, abs, NULL, sizeof(abs)) < 0) {
        queue_response(server, session, 550, "File not found");
        return;
    }

    with_data_transfer(server, session, ftp_fileops_send_file, abs, "Opening data connection", "Transfer complete");
}

static void handle_stor(ftp_server_t* server, ftp_session_t* session, const ftp_command_t* cmd) {
    if (!cmd->has_arg) {
        queue_response(server, session, 501, "Missing file path");
        return;
    }

    char abs[PATH_MAX];
    if (resolve_new_path(server, session, cmd->arg, abs, NULL, sizeof(abs)) < 0) {
        queue_response(server, session, 550, "Cannot create target file");
        return;
    }

    with_data_transfer(server, session, ftp_fileops_recv_file, abs, "Ready to receive data", "Transfer complete");
}

static void handle_delete_like(
    ftp_server_t* server,
    ftp_session_t* session,
    const ftp_command_t* cmd,
    int (*op)(const char*),
    const char* ok_msg,
    const char* fail_msg,
    bool existing_path) {
    if (!cmd->has_arg) {
        queue_response(server, session, 501, "Missing path argument");
        return;
    }

    char abs[PATH_MAX];
    int rc_resolve = existing_path
        ? resolve_existing_path(server, session, cmd->arg, abs, NULL, sizeof(abs))
        : resolve_new_path(server, session, cmd->arg, abs, NULL, sizeof(abs));
    if (rc_resolve < 0) {
        queue_response(server, session, 550, fail_msg);
        return;
    }

    if (op(abs) < 0) {
        queue_response(server, session, 550, fail_msg);
        return;
    }

    queue_response(server, session, 250, ok_msg);
}

static void handle_rnfr(ftp_server_t* server, ftp_session_t* session, const ftp_command_t* cmd) {
    if (!cmd->has_arg) {
        queue_response(server, session, 501, "Missing source path");
        return;
    }

    char abs[PATH_MAX];
    if (resolve_existing_path(server, session, cmd->arg, abs, NULL, sizeof(abs)) < 0) {
        queue_response(server, session, 550, "Source not found");
        return;
    }

    strncpy(session->rename_from, abs, sizeof(session->rename_from) - 1);
    session->rename_from[sizeof(session->rename_from) - 1] = '\0';
    session->has_rename_from = true;
    queue_response(server, session, 350, "Ready for RNTO");
}

static void handle_rnto(ftp_server_t* server, ftp_session_t* session, const ftp_command_t* cmd) {
    if (!session->has_rename_from) {
        queue_response(server, session, 503, "Need RNFR first");
        return;
    }
    if (!cmd->has_arg) {
        queue_response(server, session, 501, "Missing target path");
        return;
    }

    char abs[PATH_MAX];
    if (resolve_new_path(server, session, cmd->arg, abs, NULL, sizeof(abs)) < 0) {
        queue_response(server, session, 550, "Invalid target path");
        return;
    }

    if (ftp_fileops_rename(session->rename_from, abs) < 0) {
        queue_response(server, session, 550, "Rename failed");
        return;
    }

    session->has_rename_from = false;
    session->rename_from[0] = '\0';
    queue_response(server, session, 250, "Rename successful");
}

static void handle_command(ftp_server_t* server, ftp_session_t* session, const ftp_command_t* cmd) {
    switch (cmd->kind) {
        case FTP_CMD_USER:
            handle_user(server, session, cmd);
            break;
        case FTP_CMD_PASS:
            handle_pass(server, session, cmd);
            break;
        case FTP_CMD_QUIT:
            queue_response(server, session, 221, "Goodbye");
            close_control(server, session);
            break;
        case FTP_CMD_SYST:
            queue_response(server, session, 215, "UNIX Type: L8");
            break;
        case FTP_CMD_NOOP:
            queue_response(server, session, 200, "NOOP ok");
            break;
        case FTP_CMD_TYPE:
            if (!cmd->has_arg) {
                queue_response(server, session, 501, "Missing type");
            } else if (strcmp(cmd->arg, "I") == 0 || strcmp(cmd->arg, "A") == 0) {
                queue_response(server, session, 200, "Type set");
            } else {
                queue_response(server, session, 504, "Type not supported");
            }
            break;
        case FTP_CMD_PWD:
            if (ensure_auth(server, session)) {
                handle_pwd(server, session);
            }
            break;
        case FTP_CMD_CWD:
            if (ensure_auth(server, session)) {
                handle_cwd(server, session, cmd);
            }
            break;
        case FTP_CMD_PASV:
            if (ensure_auth(server, session)) {
                handle_pasv(server, session);
            }
            break;
        case FTP_CMD_LIST:
            if (ensure_auth(server, session)) {
                handle_list(server, session, cmd);
            }
            break;
        case FTP_CMD_NLST:
            if (ensure_auth(server, session)) {
                handle_list(server, session, cmd);
            }
            break;
        case FTP_CMD_RETR:
            if (ensure_auth(server, session)) {
                handle_retr(server, session, cmd);
            }
            break;
        case FTP_CMD_STOR:
            if (ensure_auth(server, session)) {
                handle_stor(server, session, cmd);
            }
            break;
        case FTP_CMD_DELE:
            if (ensure_auth(server, session)) {
                handle_delete_like(server, session, cmd, ftp_fileops_delete, "Delete successful", "Delete failed", true);
            }
            break;
        case FTP_CMD_MKD:
            if (ensure_auth(server, session)) {
                handle_delete_like(server, session, cmd, ftp_fileops_mkdir, "Directory created", "MKD failed", false);
            }
            break;
        case FTP_CMD_RMD:
            if (ensure_auth(server, session)) {
                handle_delete_like(server, session, cmd, ftp_fileops_rmdir, "Directory removed", "RMD failed", true);
            }
            break;
        case FTP_CMD_RNFR:
            if (ensure_auth(server, session)) {
                handle_rnfr(server, session, cmd);
            }
            break;
        case FTP_CMD_RNTO:
            if (ensure_auth(server, session)) {
                handle_rnto(server, session, cmd);
            }
            break;
        default:
            queue_response(server, session, 500, "Unknown command");
            break;
    }
}

static void on_accept_cb(void* user, int fd, const struct sockaddr_storage* peer, socklen_t peer_len) {
    (void)peer;
    (void)peer_len;

    ftp_server_t* server = (ftp_server_t*)user;
    ftp_session_t* session = ftp_session_create(fd);
    if (session == NULL) {
        ftp_tcp_server_close_client(server->tcp, fd);
        return;
    }

    if (add_session(server, session) < 0) {
        ftp_session_destroy(session);
        ftp_tcp_server_close_client(server->tcp, fd);
        return;
    }

    queue_response(server, session, 220, "FTP server ready");
}

static void on_event_cb(void* user, int fd, uint32_t events) {
    ftp_server_t* server = (ftp_server_t*)user;
    ftp_session_t* session = find_session(server, fd);
    if (session == NULL) {
        return;
    }

    if ((events & FTP_IO_WRITABLE) != 0u) {
        const int frc = ftp_connection_flush(session->conn);
        if (frc < 0) {
            close_control(server, session);
            return;
        }
        if (!ftp_connection_has_pending(session->conn)) {
            ftp_tcp_server_update_interest(server->tcp, session->control_fd, false);
        }
    }

    if ((events & FTP_IO_READABLE) != 0u) {
        const int rrc = ftp_connection_read(session->conn);
        if (rrc == 0 || rrc == -1) {
            close_control(server, session);
            return;
        }

        char* line;
        while ((line = ftp_connection_pop_line(session->conn)) != NULL) {
            ftp_command_t cmd;
            if (ftp_command_parse(line, &cmd) == 0) {
                handle_command(server, session, &cmd);
            } else {
                queue_response(server, session, 500, "Bad command syntax");
            }
            free(line);

            session = find_session(server, fd);
            if (session == NULL) {
                return;
            }
        }
    }
}

static void on_close_cb(void* user, int fd) {
    ftp_server_t* server = (ftp_server_t*)user;
    remove_session(server, fd);
}

ftp_server_t* ftp_server_create(uint16_t port, const char* root_dir) {
    if (root_dir == NULL) {
        return NULL;
    }

    ftp_server_t* server = (ftp_server_t*)calloc(1, sizeof(*server));
    if (server == NULL) {
        return NULL;
    }

    char real_root[PATH_MAX];
    if (realpath(root_dir, real_root) == NULL) {
        free(server);
        return NULL;
    }

    struct stat st;
    if (stat(real_root, &st) < 0 || !S_ISDIR(st.st_mode)) {
        free(server);
        return NULL;
    }

    server->port = port;
    strncpy(server->root, real_root, sizeof(server->root) - 1);
    server->root[sizeof(server->root) - 1] = '\0';

    ftp_tcp_callbacks_t cb;
    memset(&cb, 0, sizeof(cb));
    cb.on_accept = on_accept_cb;
    cb.on_event = on_event_cb;
    cb.on_close = on_close_cb;

    server->tcp = ftp_tcp_server_create(port, cb, server);
    if (server->tcp == NULL) {
        free(server);
        return NULL;
    }

    return server;
}

int ftp_server_start(ftp_server_t* server) {
    if (server == NULL) {
        return -1;
    }
    return ftp_tcp_server_start(server->tcp);
}

int ftp_server_run(ftp_server_t* server) {
    if (server == NULL) {
        return -1;
    }
    return ftp_tcp_server_run(server->tcp);
}

void ftp_server_stop(ftp_server_t* server) {
    if (server != NULL) {
        ftp_tcp_server_stop(server->tcp);
    }
}

void ftp_server_destroy(ftp_server_t* server) {
    if (server == NULL) {
        return;
    }

    ftp_session_node_t* cur = server->sessions;
    while (cur != NULL) {
        ftp_session_node_t* next = cur->next;
        ftp_session_destroy(cur->session);
        free(cur);
        cur = next;
    }

    ftp_tcp_server_destroy(server->tcp);
    free(server);
}
