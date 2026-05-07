#include "topic_tree.h"

#include <stdlib.h>
#include <string.h>
#include <sys/types.h>

typedef struct filter_entry {
    char* filter;
    int* sids;
    size_t sid_count;
    size_t sid_cap;
} filter_entry_t;

struct mqtt_topic_tree {
    filter_entry_t* entries;
    size_t entry_count;
    size_t entry_cap;
};

static void ensure_entry_cap(mqtt_topic_tree_t* t, size_t need) {
    if (!t) {
        return;
    }
    if (need <= t->entry_cap) {
        return;
    }
    size_t nc = t->entry_cap ? t->entry_cap * 2 : 16;
    while (nc < need) {
        nc *= 2;
    }
    filter_entry_t* p = (filter_entry_t*)realloc(t->entries, nc * sizeof(*p));
    if (!p) {
        return;
    }
    t->entries = p;
    t->entry_cap = nc;
}

static void ensure_sid_cap(filter_entry_t* e, size_t need) {
    if (!e) {
        return;
    }
    if (need <= e->sid_cap) {
        return;
    }
    size_t nc = e->sid_cap ? e->sid_cap * 2 : 8;
    while (nc < need) {
        nc *= 2;
    }
    int* p = (int*)realloc(e->sids, nc * sizeof(*p));
    if (!p) {
        return;
    }
    e->sids = p;
    e->sid_cap = nc;
}

static ssize_t find_entry(const mqtt_topic_tree_t* t, const char* filter) {
    if (!t || !filter) {
        return -1;
    }
    for (size_t i = 0; i < t->entry_count; ++i) {
        if (t->entries[i].filter && strcmp(t->entries[i].filter, filter) == 0) {
            return (ssize_t)i;
        }
    }
    return -1;
}

static bool entry_has_sid(const filter_entry_t* e, int sid) {
    if (!e) {
        return false;
    }
    for (size_t i = 0; i < e->sid_count; ++i) {
        if (e->sids[i] == sid) {
            return true;
        }
    }
    return false;
}

static void entry_remove_sid(filter_entry_t* e, int sid) {
    if (!e) {
        return;
    }
    for (size_t i = 0; i < e->sid_count; ++i) {
        if (e->sids[i] == sid) {
            e->sids[i] = e->sids[e->sid_count - 1];
            e->sid_count--;
            return;
        }
    }
}

static void delete_entry(mqtt_topic_tree_t* t, size_t idx) {
    if (!t || idx >= t->entry_count) {
        return;
    }
    free(t->entries[idx].filter);
    free(t->entries[idx].sids);

    t->entries[idx] = t->entries[t->entry_count - 1];
    t->entry_count--;
}

mqtt_topic_tree_t* mqtt_topic_tree_create(void) {
    return (mqtt_topic_tree_t*)calloc(1, sizeof(mqtt_topic_tree_t));
}

void mqtt_topic_tree_destroy(mqtt_topic_tree_t* t) {
    if (!t) {
        return;
    }
    for (size_t i = 0; i < t->entry_count; ++i) {
        free(t->entries[i].filter);
        free(t->entries[i].sids);
    }
    free(t->entries);
    free(t);
}

void mqtt_topic_tree_subscribe(mqtt_topic_tree_t* t, int session_id, const char* filter) {
    if (!t || !filter) {
        return;
    }

    ssize_t idx = find_entry(t, filter);
    if (idx < 0) {
        ensure_entry_cap(t, t->entry_count + 1);
        if (t->entry_count >= t->entry_cap) {
            return;
        }
        filter_entry_t* e = &t->entries[t->entry_count++];
        memset(e, 0, sizeof(*e));
        e->filter = strdup(filter);
        if (!e->filter) {
            t->entry_count--;
            return;
        }
        idx = (ssize_t)(t->entry_count - 1);
    }

    filter_entry_t* e = &t->entries[(size_t)idx];
    if (!entry_has_sid(e, session_id)) {
        ensure_sid_cap(e, e->sid_count + 1);
        if (e->sid_count < e->sid_cap) {
            e->sids[e->sid_count++] = session_id;
        }
    }
}

void mqtt_topic_tree_unsubscribe(mqtt_topic_tree_t* t, int session_id, const char* filter) {
    if (!t || !filter) {
        return;
    }
    const ssize_t idx = find_entry(t, filter);
    if (idx < 0) {
        return;
    }
    filter_entry_t* e = &t->entries[(size_t)idx];
    entry_remove_sid(e, session_id);
    if (e->sid_count == 0) {
        delete_entry(t, (size_t)idx);
    }
}

void mqtt_topic_tree_remove_session(mqtt_topic_tree_t* t, int session_id) {
    if (!t) {
        return;
    }
    size_t i = 0;
    while (i < t->entry_count) {
        filter_entry_t* e = &t->entries[i];
        entry_remove_sid(e, session_id);
        if (e->sid_count == 0) {
            delete_entry(t, i);
            continue;
        }
        i++;
    }
}

static bool next_level(const char* s, size_t s_len, size_t* io_pos, const char** out_start, size_t* out_len, bool* out_done) {
    if (!s || !io_pos || !out_start || !out_len || !out_done) {
        return false;
    }
    if (*io_pos > s_len) {
        *out_done = true;
        return true;
    }

    const size_t start = *io_pos;
    size_t pos = start;
    while (pos < s_len && s[pos] != '/') {
        pos++;
    }

    *out_start = s + start;
    *out_len = pos - start;

    if (pos < s_len && s[pos] == '/') {
        *io_pos = pos + 1;
        *out_done = false;
        return true;
    }

    // end reached
    *io_pos = s_len + 1; // sentinel: after end
    *out_done = true;
    return true;
}

bool mqtt_topic_match(const char* filter, const char* topic) {
    if (!filter || !topic) {
        return false;
    }

    const size_t flen = strlen(filter);
    const size_t tlen = strlen(topic);

    size_t fpos = 0;
    size_t tpos = 0;
    bool fdone = false;
    bool tdone = false;

    while (1) {
        const char* fseg = NULL;
        const char* tseg = NULL;
        size_t fseg_len = 0;
        size_t tseg_len = 0;

        (void)next_level(filter, flen, &fpos, &fseg, &fseg_len, &fdone);
        if (fdone && fseg == NULL) {
            // shouldn't happen
            return false;
        }

        if (fseg_len == 1 && fseg[0] == '#') {
            // '#' must be last
            return fdone;
        }

        // Need one topic level to match unless filter ended.
        (void)next_level(topic, tlen, &tpos, &tseg, &tseg_len, &tdone);

        if (tseg == NULL) {
            return false;
        }

        if (fseg_len == 1 && fseg[0] == '+') {
            // matches exactly one level
        } else {
            if (fseg_len != tseg_len || memcmp(fseg, tseg, fseg_len) != 0) {
                return false;
            }
        }

        if (fdone) {
            return tdone;
        }
    }
}

bool mqtt_topic_tree_match_subscribers(const mqtt_topic_tree_t* t,
                                      const char* topic,
                                      int** out_sids,
                                      size_t* out_count) {
    if (out_sids) {
        *out_sids = NULL;
    }
    if (out_count) {
        *out_count = 0;
    }
    if (!t || !topic || !out_sids || !out_count) {
        return false;
    }

    int* uniq = NULL;
    size_t cnt = 0;
    size_t cap = 0;

    for (size_t i = 0; i < t->entry_count; ++i) {
        const filter_entry_t* e = &t->entries[i];
        if (!e->filter) {
            continue;
        }
        if (!mqtt_topic_match(e->filter, topic)) {
            continue;
        }
        for (size_t k = 0; k < e->sid_count; ++k) {
            const int sid = e->sids[k];
            bool exists = false;
            for (size_t j = 0; j < cnt; ++j) {
                if (uniq[j] == sid) {
                    exists = true;
                    break;
                }
            }
            if (exists) {
                continue;
            }
            if (cnt == cap) {
                size_t nc = cap ? cap * 2 : 16;
                int* p = (int*)realloc(uniq, nc * sizeof(*p));
                if (!p) {
                    free(uniq);
                    return false;
                }
                uniq = p;
                cap = nc;
            }
            uniq[cnt++] = sid;
        }
    }

    *out_sids = uniq;
    *out_count = cnt;
    return true;
}
