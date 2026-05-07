#include "mail_store.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

static int ensure_dir_recursive(const char* path) {
    if (path == NULL || path[0] == '\0') {
        errno = EINVAL;
        return -1;
    }

    char tmp[PATH_MAX];
    if (snprintf(tmp, sizeof(tmp), "%s", path) < 0 || strlen(path) >= sizeof(tmp)) {
        errno = ENAMETOOLONG;
        return -1;
    }

    const size_t n = strlen(tmp);
    if (n == 0) {
        errno = EINVAL;
        return -1;
    }

    for (size_t i = 1; i < n; ++i) {
        if (tmp[i] != '/') {
            continue;
        }
        tmp[i] = '\0';
        if (tmp[0] != '\0' && mkdir(tmp, 0755) < 0 && errno != EEXIST) {
            return -1;
        }
        tmp[i] = '/';
    }

    if (mkdir(tmp, 0755) < 0 && errno != EEXIST) {
        return -1;
    }
    return 0;
}

static int write_all(int fd, const void* data, size_t len) {
    const char* p = (const char*)data;
    size_t off = 0;
    while (off < len) {
        ssize_t n = write(fd, p + off, len - off);
        if (n > 0) {
            off += (size_t)n;
            continue;
        }
        if (n < 0 && errno == EINTR) {
            continue;
        }
        return -1;
    }
    return 0;
}

int smtp_mail_store_init(const char* root_dir) {
    return ensure_dir_recursive(root_dir);
}

int smtp_mail_store_write(const char* root_dir, const smtp_mail_t* mail, char* out_path, size_t out_path_len) {
    if (root_dir == NULL || mail == NULL || mail->mail_from == NULL || mail->data == NULL || mail->rcpt_to == NULL) {
        errno = EINVAL;
        return -1;
    }

    if (ensure_dir_recursive(root_dir) < 0) {
        return -1;
    }

    static unsigned counter = 0;
    char path[PATH_MAX];
    int fd = -1;

    for (int i = 0; i < 16; ++i) {
        ++counter;
        const int n = snprintf(
            path,
            sizeof(path),
            "%s/mail_%ld_%d_%u.eml",
            root_dir,
            (long)time(NULL),
            (int)getpid(),
            counter);
        if (n < 0 || (size_t)n >= sizeof(path)) {
            errno = ENAMETOOLONG;
            return -1;
        }

        fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0644);
        if (fd >= 0) {
            break;
        }
        if (errno != EEXIST) {
            return -1;
        }
    }

    if (fd < 0) {
        errno = EEXIST;
        return -1;
    }

    char header[4096];
    int hdr_n = snprintf(
        header,
        sizeof(header),
        "X-SMTP-HELO: %s\r\n"
        "X-SMTP-Auth-User: %s\r\n"
        "X-SMTP-Envelope-From: %s\r\n",
        mail->helo_name != NULL ? mail->helo_name : "-",
        (mail->auth_user != NULL && mail->auth_user[0] != '\0') ? mail->auth_user : "-",
        mail->mail_from);
    if (hdr_n < 0 || (size_t)hdr_n >= sizeof(header)) {
        close(fd);
        unlink(path);
        errno = EOVERFLOW;
        return -1;
    }

    if (write_all(fd, header, (size_t)hdr_n) < 0) {
        close(fd);
        unlink(path);
        return -1;
    }

    for (size_t i = 0; i < mail->rcpt_count; ++i) {
        const char* rcpt = mail->rcpt_to[i] != NULL ? mail->rcpt_to[i] : "";
        char line[640];
        const int ln = snprintf(line, sizeof(line), "X-SMTP-Envelope-To: %s\r\n", rcpt);
        if (ln < 0 || (size_t)ln >= sizeof(line) || write_all(fd, line, (size_t)ln) < 0) {
            close(fd);
            unlink(path);
            return -1;
        }
    }

    const char* sep = "\r\n";
    if (write_all(fd, sep, 2) < 0 || write_all(fd, mail->data, mail->data_len) < 0) {
        close(fd);
        unlink(path);
        return -1;
    }

    if (close(fd) < 0) {
        unlink(path);
        return -1;
    }

    if (out_path != NULL && out_path_len > 0) {
        snprintf(out_path, out_path_len, "%s", path);
    }
    return 0;
}
