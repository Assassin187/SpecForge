#ifndef HTTP_FILE_OPS_H
#define HTTP_FILE_OPS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Return the MIME type for a file extension. Never returns NULL. */
const char* http_mime_by_ext(const char* path);

/* Resolve a URI path to an absolute filesystem path under root_dir,
 * with sandbox enforcement against path traversal.
 * On success, stores result in out_abs (size out_len) and returns 0.
 * On failure returns -1. */
int http_resolve_path(const char* root_dir, const char* uri_path,
                      char* out_abs, size_t out_len);

/* Stat the file at abs_path. Returns:
 *  0 = regular file, 1 = directory, -1 = not found / error. */
int http_stat_path(const char* abs_path);

/* Read the full contents of a file into a heap buffer.
 * Caller must free *out_data. *out_len receives the byte count.
 * Returns 0 on success, -1 on error. */
int http_read_file(const char* abs_path, char** out_data, size_t* out_len);

/* Build an HTML directory listing for abs_dir under the given uri_prefix.
 * Caller must free the returned string. Returns NULL on error. */
char* http_dir_listing_html(const char* abs_dir, const char* uri_prefix);

#ifdef __cplusplus
}
#endif

#endif
