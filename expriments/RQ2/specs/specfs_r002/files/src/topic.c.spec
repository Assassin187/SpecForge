[PROMPT]
LANG:
C99
ROLE:
MQTT Topic Name / Topic Filter validation and level-wise case-sensitive matching with '+' and '#' wildcards, empty levels, '/' separators and the '$' rule.

[RELY]
DEPENDENCY:
  - src/topic.h
SYSTEM_DEPENDENCY:
  - stddef.h
  - stdint.h

[GUARANTEE]
PATH:
src/topic.h
DEPENDENCY:

SYSTEM_DEPENDENCY:
  - stddef.h
  - stdint.h
DATA:

INTERFACE:
  - SIGNATURE:
int topic_utf8_validate(const uint8_t *data, size_t len)
    NAME:
topic_utf8_validate
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Well-formed UTF-8 without U+0000 and without surrogate/invalid ranges
    VISIBILITY:
public
  - SIGNATURE:
int topic_name_validate(const uint8_t *name, size_t len)
    NAME:
topic_name_validate
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Legal Topic Name: nonempty, UTF-8, no wildcards
    VISIBILITY:
public
  - SIGNATURE:
int topic_filter_validate(const uint8_t *filter, size_t len)
    NAME:
topic_filter_validate
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Legal Topic Filter including wildcard placement
    VISIBILITY:
public
  - SIGNATURE:
int topic_filter_matches(const uint8_t *filter, size_t filter_len, const uint8_t *topic, size_t topic_len)
    NAME:
topic_filter_matches
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Level-wise match of a filter against a topic name
    VISIBILITY:
public

[SPECIFICATION]
SOURCE:
  PATH:
src/topic.c
  DEPENDENCY:
    - src/topic.h
  SYSTEM_DEPENDENCY:
    - stddef.h
    - stdint.h
  DATA:
    - NAME:
topic_utf8_decode
      KIND:
TYPE
      VISIBILITY:
PRIVATE
      ROLE:
Private helper result for one decoded code point: length in bytes and code point value
    - NAME:
topic_starts_wildcard
      KIND:
MACRO
      VISIBILITY:
PRIVATE
      VALUE:
'#' or '+'
      ROLE:
Private test for a filter whose first character is a wildcard
  INTERFACE:
    - SIGNATURE:
int topic_utf8_validate(const uint8_t *data, size_t len)
      NAME:
topic_utf8_validate
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Decode every sequence, reject overlong forms, surrogates, >U+10FFFF and the encoded NUL
      VISIBILITY:
public
    - SIGNATURE:
int topic_name_validate(const uint8_t *name, size_t len)
      NAME:
topic_name_validate
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Require len>=1, len<=65535, no 0x23/0x2B byte, then topic_utf8_validate
      VISIBILITY:
public
    - SIGNATURE:
int topic_filter_validate(const uint8_t *filter, size_t len)
      NAME:
topic_filter_validate
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Require len>=1, len<=65535, UTF-8, '#' whole last level and '+' whole levels
      VISIBILITY:
public
    - SIGNATURE:
int topic_filter_matches(const uint8_t *filter, size_t filter_len, const uint8_t *topic, size_t topic_len)
      NAME:
topic_filter_matches
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Walk levels by '/' with '+' one level and '#' tail; apply the '$' rule; no normalization
      VISIBILITY:
public
PUBLIC_SYMBOLS:
  - NAME:
topic_utf8_validate
    KIND:
FUNC
    SIGNATURE:
int topic_utf8_validate(const uint8_t *data, size_t len)
    ROLE:
UTF-8 validation
  - NAME:
topic_name_validate
    KIND:
FUNC
    SIGNATURE:
int topic_name_validate(const uint8_t *name, size_t len)
    ROLE:
Topic Name validation
  - NAME:
topic_filter_validate
    KIND:
FUNC
    SIGNATURE:
int topic_filter_validate(const uint8_t *filter, size_t len)
    ROLE:
Topic Filter validation
  - NAME:
topic_filter_matches
    KIND:
FUNC
    SIGNATURE:
int topic_filter_matches(const uint8_t *filter, size_t filter_len, const uint8_t *topic, size_t topic_len)
    ROLE:
Topic matching
