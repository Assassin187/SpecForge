#include "topic/topic_tree.h"

#include <stdlib.h>
#include <string.h>
#include <stdio.h>

#define INITIAL_ENTRY_CAP 4
#define INITIAL_SID_CAP   4

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

static void ensure_entry_cap(mqtt_topic_tree_t* t, size_t need);
static void ensure_sid_cap(filter_entry_t* e, size_t need);
static ssize_t find_entry(const mqtt_topic_tree_t* t, const char* filter);
static bool entry_has_sid(const filter_entry_t* e, int sid);
static void entry_remove_sid(filter_entry_t* e, int sid);
static void delete_entry(mqtt_topic_tree_t* t, size_t idx);
static bool next_level(const char* s, size_t s_len, size_t* io_pos, const char** out_start, size_t* out_len, bool* out_done);

mqtt_topic_tree_t* mqtt_topic_tree_create(void)
{
    mqtt_topic_tree_t* t = (mqtt_topic_tree_t*)calloc(1, sizeof(mqtt_topic_tree_t));
    if (!t) return NULL;
    t->entries = NULL;
    t->entry_count = 0;
    t->entry_cap = 0;
    return t;
}

void mqtt_topic_tree_destroy(mqtt_topic_tree_t* t)
{
    if (!t) return;

    for (size_t i = 0; i < t->entry_count; ++i) {
        free(t->entries[i].filter);
        free(t->entries[i].sids);
    }
    free(t->entries);
    free(t);
}

static ssize_t find_entry(const mqtt_topic_tree_t* t, const char* filter)
{
    if (!t || !filter) return -1;
    for (size_t i = 0; i < t->entry_count; ++i) {
        if (strcmp(t->entries[i].filter, filter) == 0) {
            return (ssize_t)i;
        }
    }
    return -1;
}

static bool entry_has_sid(const filter_entry_t* e, int sid)
{
    if (!e) return false;
    for (size_t i = 0; i < e->sid_count; ++i) {
        if (e->sids[i] == sid) {
            return true;
        }
    }
    return false;
}

static void ensure_entry_cap(mqtt_topic_tree_t* t, size_t need)
{
    if (t->entry_cap >= need) return;

    size_t new_cap = t->entry_cap == 0 ? INITIAL_ENTRY_CAP : t->entry_cap * 2;
    while (new_cap < need) {
        new_cap *= 2;
    }

    filter_entry_t* new_entries = (filter_entry_t*)realloc(t->entries, new_cap * sizeof(filter_entry_t));
    if (!new_entries) return; // allocation failure; caller must handle

    t->entries = new_entries;
    t->entry_cap = new_cap;
}

static void ensure_sid_cap(filter_entry_t* e, size_t need)
{
    if (e->sid_cap >= need) return;

    size_t new_cap = e->sid_cap == 0 ? INITIAL_SID_CAP : e->sid_cap * 2;
    while (new_cap < need) {
        new_cap *= 2;
    }

    int* new_sids = (int*)realloc(e->sids, new_cap * sizeof(int));
    if (!new_sids) return; // allocation failure; caller must handle

    e->sids = new_sids;
    e->sid_cap = new_cap;
}

void mqtt_topic_tree_subscribe(mqtt_topic_tree_t* t, int session_id, const char* filter)
{
    if (!t || !filter) return;

    ssize_t idx = find_entry(t, filter);
    filter_entry_t* entry = NULL;

    if (idx == -1) {
        // New filter: add entry
        ensure_entry_cap(t, t->entry_count + 1);
        if (t->entry_cap <= t->entry_count) return; // realloc failed

        idx = (ssize_t)t->entry_count;
        t->entries[idx].filter = strdup(filter);
        if (!t->entries[idx].filter) {
            return;
        }
        t->entries[idx].sids = NULL;
        t->entries[idx].sid_count = 0;
        t->entries[idx].sid_cap = 0;
        t->entry_count++;
        entry = &t->entries[idx];
    } else {
        entry = &t->entries[idx];
    }

    if (entry_has_sid(entry, session_id)) {
        return;
    }

    ensure_sid_cap(entry, entry->sid_count + 1);
    if (entry->sid_cap <= entry->sid_count) {
        // Realloc failed; if this was a new entry, we should roll back
        if (idx == (ssize_t)(t->entry_count - 1) && entry->sid_count == 0) {
            free(entry->filter);
            entry->filter = NULL;
            t->entry_count--;
        }
        return;
    }

    entry->sids[entry->sid_count] = session_id;
    entry->sid_count++;
}

static void entry_remove_sid(filter_entry_t* e, int sid)
{
    if (!e) return;
    for (size_t i = 0; i < e->sid_count; ++i) {
        if (e->sids[i] == sid) {
            e->sids[i] = e->sids[e->sid_count - 1];
            e->sid_count--;
            break;
        }
    }
}

static void delete_entry(mqtt_topic_tree_t* t, size_t idx)
{
    if (idx >= t->entry_count) return;

    free(t->entries[idx].filter);
    free(t->entries[idx].sids);

    if (idx != t->entry_count - 1) {
        t->entries[idx] = t->entries[t->entry_count - 1];
    }
    t->entry_count--;
}

void mqtt_topic_tree_unsubscribe(mqtt_topic_tree_t* t, int session_id, const char* filter)
{
    if (!t || !filter) return;

    ssize_t idx = find_entry(t, filter);
    if (idx == -1) return;

    entry_remove_sid(&t->entries[idx], session_id);
    if (t->entries[idx].sid_count == 0) {
        delete_entry(t, (size_t)idx);
    }
}

void mqtt_topic_tree_remove_session(mqtt_topic_tree_t* t, int session_id)
{
    if (!t) return;

    for (size_t i = 0; i < t->entry_count; ) {
        entry_remove_sid(&t->entries[i], session_id);
        if (t->entries[i].sid_count == 0) {
            delete_entry(t, i);
            // Do not increment i; the next entry has shifted into position i
        } else {
            i++;
        }
    }
}

bool mqtt_topic_match(const char* filter, const char* topic)
{
    if (!filter || !topic) return false;

    size_t f_len = strlen(filter);
    size_t t_len = strlen(topic);
    size_t f_pos = 0, t_pos = 0;
    bool f_done = false, t_done = false;
    const char* f_seg; size_t f_seg_len;
    const char* t_seg; size_t t_seg_len;

    while (!f_done || !t_done) {
        bool f_got = next_level(filter, f_len, &f_pos, &f_seg, &f_seg_len, &f_done);
        bool t_got = next_level(topic, t_len, &t_pos, &t_seg, &t_seg_len, &t_done);

        if (!f_got && !t_got) break;

        // If one is done and the other isn't, mismatch unless # handles it
        if (f_done && !t_done) return false;
        if (!f_got) return false;

        // Handle multi-level wildcard
        if (f_seg_len == 1 && f_seg[0] == '#') {
            // # must be last in filter
            if (!f_done) return false;
            return true;
        }

        // Handle single-level wildcard
        if (f_seg_len == 1 && f_seg[0] == '+') {
            if (!t_got) return false;
            continue;
        }

        // Exact match
        if (!t_got) return false;
        if (f_seg_len != t_seg_len) return false;
        if (memcmp(f_seg, t_seg, f_seg_len) != 0) return false;
    }

    return f_done && t_done;
}

static bool next_level(const char* s, size_t s_len, size_t* io_pos, const char** out_start, size_t* out_len, bool* out_done)
{
    if (*io_pos >= s_len) {
        *out_done = true;
        return false;
    }

    size_t start = *io_pos;
    size_t end = start;

    while (end < s_len && s[end] != '/') {
        end++;
    }

    *out_start = s + start;
    *out_len = end - start;
    *io_pos = end + 1; // skip past '/'

    if (end == s_len) {
        *out_done = true;
    } else {
        *out_done = false;
    }

    return true;
}

bool mqtt_topic_tree_match_subscribers(const mqtt_topic_tree_t* t, const char* topic, int** out_sids, size_t* out_count)
{
    if (!t || !topic || !out_sids || !out_count) {
        return false;
    }

    *out_sids = NULL;
    *out_count = 0;

    if (t->entry_count == 0) {
        return true;
    }

    int* uniq = NULL;
    size_t uniq_cap = 0;
    size_t uniq_count = 0;

    for (size_t i = 0; i < t->entry_count; ++i) {
        if (mqtt_topic_match(t->entries[i].filter, topic)) {
            for (size_t j = 0; j < t->entries[i].sid_count; ++j) {
                int sid = t->entries[i].sids[j];
                // Check if already in uniq
                bool found = false;
                for (size_t k = 0; k < uniq_count; ++k) {
                    if (uniq[k] == sid) {
                        found = true;
                        break;
                    }
                }
                if (!found) {
                    if (uniq_count >= uniq_cap) {
                        size_t new_cap = uniq_cap == 0 ? INITIAL_SID_CAP : uniq_cap * 2;
                        int* new_uniq = (int*)realloc(uniq, new_cap * sizeof(int));
                        if (!new_uniq) {
                            free(uniq);
                            return false;
                        }
                        uniq = new_uniq;
                        uniq_cap = new_cap;
                    }
                    uniq[uniq_count] = sid;
                    uniq_count++;
                }
            }
        }
    }

    *out_sids = uniq;
    *out_count = uniq_count;
    return true;
}
