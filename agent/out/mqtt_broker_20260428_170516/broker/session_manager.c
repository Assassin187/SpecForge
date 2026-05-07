#include "broker/session_manager.h"
#include <stdlib.h>
#include <string.h>

struct mqtt_session_manager {
    mqtt_session_t** sessions;
    size_t count;
    size_t cap;
};

static void ensure_cap(mqtt_session_manager_t* m, size_t need) {
    if (!m || need <= m->cap) {
        return;
    }

    size_t new_cap = m->cap == 0 ? 16 : m->cap;
    while (new_cap < need) {
        new_cap *= 2;
    }

    mqtt_session_t** new_sessions = realloc(m->sessions, new_cap * sizeof(mqtt_session_t*));
    if (!new_sessions) {
        return;
    }

    m->sessions = new_sessions;
    m->cap = new_cap;
}

mqtt_session_manager_t* mqtt_session_manager_create(void) {
    mqtt_session_manager_t* m = calloc(1, sizeof(mqtt_session_manager_t));
    return m;
}

void mqtt_session_manager_destroy(mqtt_session_manager_t* m) {
    if (!m) {
        return;
    }

    for (size_t i = 0; i < m->count; ++i) {
        mqtt_session_destroy(m->sessions[i]);
    }

    free(m->sessions);
    free(m);
}

void mqtt_session_manager_add(mqtt_session_manager_t* m, mqtt_session_t* s) {
    if (!m || !s) {
        return;
    }

    int sid = mqtt_session_id(s);
    for (size_t i = 0; i < m->count; ++i) {
        if (mqtt_session_id(m->sessions[i]) == sid) {
            mqtt_session_destroy(m->sessions[i]);
            m->sessions[i] = s;
            return;
        }
    }

    ensure_cap(m, m->count + 1);
    if (m->cap <= m->count) {
        mqtt_session_destroy(s);
        return;
    }

    m->sessions[m->count++] = s;
}

void mqtt_session_manager_remove(mqtt_session_manager_t* m, int session_id) {
    if (!m) {
        return;
    }

    for (size_t i = 0; i < m->count; ++i) {
        if (mqtt_session_id(m->sessions[i]) == session_id) {
            mqtt_session_destroy(m->sessions[i]);

            if (i != m->count - 1) {
                m->sessions[i] = m->sessions[m->count - 1];
            }

            --m->count;
            break;
        }
    }
}

mqtt_session_t* mqtt_session_manager_get(mqtt_session_manager_t* m, int session_id) {
    if (!m) {
        return NULL;
    }

    for (size_t i = 0; i < m->count; ++i) {
        if (mqtt_session_id(m->sessions[i]) == session_id) {
            return m->sessions[i];
        }
    }

    return NULL;
}
