/*
 * topic.h - MQTT 3.1.1 Topic Name / Topic Filter validation and matching.
 *
 * All values are byte slices with explicit lengths: no normalization, no
 * case folding and no substitution of characters is performed. Validation
 * functions return 1 when the value is legal and 0 when it is not; matching
 * returns 1 on match and 0 on no match.
 *
 * Validation covers: UTF-8 well-formedness without U+0000 (MQTT-1.5.3-1/-2),
 * a minimum length of one character, the wildcard position rules for '#'
 * (whole level, last character) and '+' (whole level), and the prohibition of
 * wildcard characters inside a Topic Name.
 */
#ifndef MQTT_BROKER_TOPIC_H
#define MQTT_BROKER_TOPIC_H

#include <stddef.h>
#include <stdint.h>

/*
 * Return 1 when data is well-formed UTF-8 (RFC 3629) that contains no
 * encoding of U+0000 and no encodings of the surrogate range U+D800-U+DFFF,
 * and no code point above U+10FFFF; otherwise 0. len may be 0 (returns 1).
 */
int topic_utf8_validate(const uint8_t *data, size_t len);

/* Return 1 when name is a legal Topic Name: utf8 valid, nonempty, no '+'/'#'. */
int topic_name_validate(const uint8_t *name, size_t len);

/* Return 1 when filter is a legal Topic Filter including wildcard placement. */
int topic_filter_validate(const uint8_t *filter, size_t len);

/*
 * Return 1 when the validated filter matches the validated topic name using
 * case-sensitive level comparison, '+' matching exactly one level (which may
 * be empty) and '#' matching the parent level plus any number of child
 * levels, including zero. A filter beginning with '#' or '+' never matches a
 * topic name beginning with '$'.
 */
int topic_filter_matches(const uint8_t *filter, size_t filter_len,
                         const uint8_t *topic, size_t topic_len);

#endif /* MQTT_BROKER_TOPIC_H */
