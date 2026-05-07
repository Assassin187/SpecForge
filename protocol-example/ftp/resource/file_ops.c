#include "file_ops.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

static int wait_fd_writable(int fd) {
    struct pollfd pfd;
    pfd.fd = fd;
    pfd.events = POLLOUT;
    pfd.revents = 0;
    return poll(&pfd, 1, 5000);
}

static int send_all(int fd, const void* buf, size_t len) {
    const char* p = (const char*)buf;
    size_t off = 0;

    while (off < len) {
        const ssize_t n = send(fd, p + off, len - off, 0);
        if (n > 0) {
            off += (size_t)n;
            continue;
        }
        if (n < 0 && errno == EINTR) {
            continue;
        }
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            if (wait_fd_writable(fd) <= 0) {
                return -1;
            }
            continue;
        }
        return -1;
    }

    return 0;
}

static int recv_to_fd(int data_fd, int out_fd) {
    char buf[8192];
    while (1) {
        const ssize_t n = recv(data_fd, buf, sizeof(buf), 0);
        if (n > 0) {
            size_t off = 0;
            while (off < (size_t)n) {
                const ssize_t w = write(out_fd, buf + off, (size_t)n - off);
                if (w > 0) {
                    off += (size_t)w;
                    continue;
                }
                if (w < 0 && errno == EINTR) {
                    continue;
                }
                return -1;
            }
            continue;
        }
        if (n == 0) {
            return 0;
        }
        if (errno == EINTR) {
            continue;
        }
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            struct pollfd pfd;
            pfd.fd = data_fd;
            pfd.events = POLLIN;
            pfd.revents = 0;
            if (poll(&pfd, 1, 5000) <= 0) {
                return -1;
            }
            continue;
        }
        return -1;
    }
}

static int send_from_fd(int data_fd, int in_fd) {
    char buf[8192];
    while (1) {
        const ssize_t n = read(in_fd, buf, sizeof(buf));
        if (n > 0) {
            if (send_all(data_fd, buf, (size_t)n) < 0) {
                return -1;
            }
            continue;
        }
        if (n == 0) {
            return 0;
        }
        if (errno == EINTR) {
            continue;
        }
        return -1;
    }
}

int ftp_fileops_list_dir(int data_fd, const char* abs_dir) {
    DIR* dir = opendir(abs_dir);
    if (dir == NULL) {
        return -1;
    }

    struct dirent* ent;
    char line[2048];
    while ((ent = readdir(dir)) != NULL) {
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) {
            continue;
        }

        char path[2048];
        snprintf(path, sizeof(path), "%s/%s", abs_dir, ent->d_name);

        struct stat st;
        if (stat(path, &st) < 0) {
            continue;
        }

        struct tm tmv;
        localtime_r(&st.st_mtime, &tmv);
        char tbuf[64];
        strftime(tbuf, sizeof(tbuf), "%b %d %H:%M", &tmv);

        const char kind = S_ISDIR(st.st_mode) ? 'd' : '-';
        const int n = snprintf(
            line,
            sizeof(line),
            "%crw-r--r-- 1 ftp ftp %10lld %s %s\r\n",
            kind,
            (long long)st.st_size,
            tbuf,
            ent->d_name);
        if (n <= 0 || (size_t)n >= sizeof(line)) {
            continue;
        }

        if (send_all(data_fd, line, (size_t)n) < 0) {
            closedir(dir);
            return -1;
        }
    }

    closedir(dir);
    return 0;
}

int ftp_fileops_send_file(int data_fd, const char* abs_file) {
    const int fd = open(abs_file, O_RDONLY);
    if (fd < 0) {
        return -1;
    }

    const int rc = send_from_fd(data_fd, fd);
    close(fd);
    return rc;
}

int ftp_fileops_recv_file(int data_fd, const char* abs_file) {
    const int fd = open(abs_file, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        return -1;
    }

    const int rc = recv_to_fd(data_fd, fd);
    close(fd);
    return rc;
}

int ftp_fileops_delete(const char* abs_path) {
    return unlink(abs_path);
}

int ftp_fileops_mkdir(const char* abs_path) {
    return mkdir(abs_path, 0755);
}

int ftp_fileops_rmdir(const char* abs_path) {
    return rmdir(abs_path);
}

int ftp_fileops_rename(const char* old_abs_path, const char* new_abs_path) {
    return rename(old_abs_path, new_abs_path);
}
