#include "file_ops.h"

#include <dirent.h>
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

typedef struct {
    const char* ext;
    const char* mime;
} mime_entry_t;

static const mime_entry_t mime_table[] = {
    { ".html",    "text/html; charset=utf-8" },
    { ".htm",     "text/html; charset=utf-8" },
    { ".css",     "text/css; charset=utf-8" },
    { ".js",      "application/javascript" },
    { ".json",    "application/json" },
    { ".txt",     "text/plain; charset=utf-8" },
    { ".xml",     "application/xml" },
    { ".png",     "image/png" },
    { ".jpg",     "image/jpeg" },
    { ".jpeg",    "image/jpeg" },
    { ".gif",     "image/gif" },
    { ".svg",     "image/svg+xml" },
    { ".ico",     "image/x-icon" },
    { ".pdf",     "application/pdf" },
    { ".zip",     "application/zip" },
    { ".gz",      "application/gzip" },
    { ".tar",     "application/x-tar" },
    { ".mp3",     "audio/mpeg" },
    { ".mp4",     "video/mp4" },
    { ".webm",    "video/webm" },
    { ".woff2",   "font/woff2" },
    { ".wasm",    "application/wasm" },
    { ".bin",     "application/octet-stream" },
    { NULL,       "application/octet-stream" },
};

const char* http_mime_by_ext(const char* path) {
    if (path == NULL) {
        return "application/octet-stream";
    }
    const char* dot = strrchr(path, '.');
    if (dot == NULL) {
        return "application/octet-stream";
    }
    for (const mime_entry_t* e = mime_table; e->ext != NULL; ++e) {
        if (strcasecmp(dot, e->ext) == 0) {
            return e->mime;
        }
    }
    return "application/octet-stream";
}

int http_resolve_path(const char* root_dir, const char* uri_path,
                      char* out_abs, size_t out_len) {
    if (root_dir == NULL || uri_path == NULL || out_abs == NULL || out_len == 0) {
        return -1;
    }

    /* Build normalized virtual path */
    const char* p = uri_path;

    /* Skip leading '/' */
    if (*p == '/') {
        ++p;
    }

    /* Split and resolve . and .. */
    char* segs[256];
    size_t segc = 0;
    char work[PATH_MAX];
    strncpy(work, p, sizeof(work) - 1);
    work[sizeof(work) - 1] = '\0';

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

    /* Assemble absolute path under root */
    size_t used = (size_t)snprintf(out_abs, out_len, "%s", root_dir);
    if (used >= out_len) {
        return -1;
    }

    for (size_t i = 0; i < segc; ++i) {
        const size_t sl = strlen(segs[i]);
        if (used + 1 + sl >= out_len) {
            return -1;
        }
        out_abs[used++] = '/';
        memcpy(out_abs + used, segs[i], sl);
        used += sl;
        out_abs[used] = '\0';
    }

    /* Sandbox check: realpath and verify under root */
    char resolved[PATH_MAX];
    bool existed = (realpath(out_abs, resolved) != NULL);

    if (!existed) {
        /* If the file doesn't exist yet (POST case), resolve the parent */
        char* last_slash = strrchr(out_abs, '/');
        if (last_slash != NULL) {
            *last_slash = '\0';
            if (realpath(out_abs, resolved) == NULL) {
                return -1;
            }
            *last_slash = '/';
        } else {
            return -1;
        }
    }

    const size_t root_n = strlen(root_dir);
    if (strncmp(root_dir, resolved, root_n) != 0 ||
        (resolved[root_n] != '\0' && resolved[root_n] != '/')) {
        errno = EACCES;
        return -1;
    }

    /* Only canonicalize path if it already existed; keep the assembled
     * path (which passed sandbox via parent) when creating new files. */
    if (existed) {
        strncpy(out_abs, resolved, out_len - 1);
        out_abs[out_len - 1] = '\0';
    }
    return 0;
}

int http_stat_path(const char* abs_path) {
    struct stat st;
    if (stat(abs_path, &st) < 0) {
        return -1;
    }
    if (S_ISDIR(st.st_mode)) {
        return 1;
    }
    if (S_ISREG(st.st_mode)) {
        return 0;
    }
    return -1;
}

int http_read_file(const char* abs_path, char** out_data, size_t* out_len) {
    if (abs_path == NULL || out_data == NULL || out_len == NULL) {
        return -1;
    }

    FILE* fp = fopen(abs_path, "rb");
    if (fp == NULL) {
        return -1;
    }

    if (fseek(fp, 0, SEEK_END) < 0) {
        fclose(fp);
        return -1;
    }

    const long sz = ftell(fp);
    if (sz < 0) {
        fclose(fp);
        return -1;
    }

    rewind(fp);

    char* buf = (char*)malloc((size_t)sz + 1);
    if (buf == NULL) {
        fclose(fp);
        return -1;
    }

    const size_t nread = fread(buf, 1, (size_t)sz, fp);
    fclose(fp);

    if (nread != (size_t)sz) {
        free(buf);
        return -1;
    }

    buf[nread] = '\0';
    *out_data = buf;
    *out_len = nread;
    return 0;
}

char* http_dir_listing_html(const char* abs_dir, const char* uri_prefix) {
    DIR* d = opendir(abs_dir);
    if (d == NULL) {
        return NULL;
    }

    /* Estimate size */
    size_t cap = 16384;
    char* buf = (char*)malloc(cap);
    if (buf == NULL) {
        closedir(d);
        return NULL;
    }

    int written = snprintf(buf, cap,
        "<!DOCTYPE html>\r\n"
        "<html><head><meta charset=\"utf-8\">"
        "<title>Index of %s</title>"
        "<style>body{font-family:monospace;margin:2em}"
        "a{text-decoration:none;color:#0366d6}"
        ".dir{font-weight:bold}"
        "</style></head>\r\n"
        "<body>\r\n"
        "<h1>Index of %s</h1>\r\n"
        "<hr><pre>\r\n",
        uri_prefix, uri_prefix);
    if (written < 0) {
        free(buf);
        closedir(d);
        return NULL;
    }

    struct dirent* ent;
    while ((ent = readdir(d)) != NULL) {
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) {
            continue;
        }

        char full[PATH_MAX];
        snprintf(full, sizeof(full), "%s/%s", abs_dir, ent->d_name);

        struct stat st;
        if (stat(full, &st) < 0) {
            continue;
        }

        char entry[1024];
        char size_str[32] = "-";
        if (S_ISREG(st.st_mode)) {
            const off_t sz = st.st_size;
            if (sz < 1024) {
                snprintf(size_str, sizeof(size_str), "%ld B", (long)sz);
            } else if (sz < 1024 * 1024) {
                snprintf(size_str, sizeof(size_str), "%.1f KB", (double)sz / 1024.0);
            } else {
                snprintf(size_str, sizeof(size_str), "%.1f MB", (double)sz / (1024.0 * 1024.0));
            }
        } else if (S_ISDIR(st.st_mode)) {
            snprintf(size_str, sizeof(size_str), "[dir]");
        }

        const char* uri_end = uri_prefix;
        while (*uri_end != '\0') {
            ++uri_end;
        }
        const bool has_slash = (uri_end > uri_prefix && *(uri_end - 1) == '/');

        snprintf(entry, sizeof(entry),
            "<a href=\"%s%s%s\">%-40s</a> %10s\r\n",
            uri_prefix, has_slash ? "" : "/", ent->d_name,
            ent->d_name, size_str);

        /* Grow if needed */
        const size_t need = (size_t)written + strlen(entry) + 1;
        if (need > cap) {
            cap = need * 2;
            char* tmp = (char*)realloc(buf, cap);
            if (tmp == NULL) {
                free(buf);
                closedir(d);
                return NULL;
            }
            buf = tmp;
        }

        written += snprintf(buf + written, cap - (size_t)written, "%s", entry);
    }
    closedir(d);

    const char* footer = "</pre><hr></body></html>\r\n";
    const size_t fneed = (size_t)written + strlen(footer) + 1;
    if (fneed > cap) {
        char* tmp = (char*)realloc(buf, fneed);
        if (tmp == NULL) {
            free(buf);
            return NULL;
        }
        buf = tmp;
    }
    snprintf(buf + written, fneed - (size_t)written, "%s", footer);

    return buf;
}
