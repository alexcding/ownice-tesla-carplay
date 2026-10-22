/* Original AVC/HEVC configuration and access-unit converter. No vendor code. */
#include "hevc_codec.h"

extern void *malloc(size_t);
extern void free(void *);
extern void *memcpy(void *, const void *, size_t);

#define LIMIT (16u * 1024u * 1024u)
#define BAD (-6756)
#define NOMEM (-6728)
static unsigned be16(const uint8_t *p) { return (unsigned)p[0] * 256 + p[1]; }
static uint32_t be32(const uint8_t *p) {
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 |
           (uint32_t)p[2] << 8 | p[3];
}
static int tag(const uint8_t *p, const char *s) {
    return p[0] == s[0] && p[1] == s[1] && p[2] == s[2] && p[3] == s[3];
}
/* Accept a raw configuration record, or ISO BMFF avcC/hvcC and visual sample
 * entries. Bounds and nesting are explicit; never scan arbitrary payload bytes
 * for a coincidental box name. Extended-size boxes are not accepted. */
static int record(const uint8_t **p, size_t *len, unsigned *hevc, unsigned depth) {
    const uint8_t *b = *p;
    size_t n = *len;
    if (!n || depth > 3) return BAD;
    if (b[0] == 1) {
        *hevc = n >= 23 && (b[13] & 0xf0) == 0xf0 &&
                (b[15] & 0xfc) == 0xfc && (b[16] & 0xfc) == 0xfc &&
                (b[17] & 0xf8) == 0xf8 && (b[18] & 0xf8) == 0xf8;
        return 0;
    }
    for (size_t at = 0; n - at >= 8;) {
        uint32_t size = be32(b + at);
        if (size < 8 || size > n - at) return BAD;
        if (tag(b + at + 4, "hvcC") || tag(b + at + 4, "avcC")) {
            *hevc = tag(b + at + 4, "hvcC");
            *p = b + at + 8;
            *len = size - 8;
            return *len && **p == 1 ? 0 : BAD;
        }
        size_t skip = 0;
        if (tag(b + at + 4, "hvc1") || tag(b + at + 4, "hev1") ||
            tag(b + at + 4, "avc1")) skip = 86;
        if (tag(b + at + 4, "stsd")) skip = 16;
        if (skip && size > skip) {
            const uint8_t *child = b + at + skip;
            size_t child_len = size - skip;
            if (!record(&child, &child_len, hevc, depth + 1)) {
                *p = child; *len = child_len; return 0;
            }
        }
        at += size;
    }
    return BAD;
}
static int add(uint8_t *out, size_t *used, const uint8_t *nal, size_t n) {
    if (!n || n > LIMIT - 4 || *used > LIMIT - n - 4) return BAD;
    if (out) {
        out[*used] = out[*used + 1] = out[*used + 2] = 0;
        out[*used + 3] = 1;
        memcpy(out + *used + 4, nal, n);
    }
    *used += n + 4;
    return 0;
}
static int config_pass(const uint8_t *p, size_t n, unsigned hevc,
                       uint8_t *out, size_t *used, unsigned *length_size) {
    size_t at;
    unsigned groups, seen = 0;
    *used = 0;
    if (!n || p[0] != 1) return BAD;
    if (hevc) {
        if (n < 23) return BAD;
        *length_size = (p[21] & 3) + 1;
        groups = p[22]; at = 23;
    } else {
        if (n < 7 || (p[4] & 0xfc) != 0xfc || (p[5] & 0xe0) != 0xe0) return BAD;
        *length_size = (p[4] & 3) + 1;
        groups = 2; at = 6;
    }
    if (*length_size == 3) return BAD; /* reserved lengthSizeMinusOne */
    /* Emit VPS, SPS, PPS in decoder order even if hvcC lists its arrays in a
     * different order. The browser detects HEVC from the leading VPS. */
    for (unsigned phase = 0; phase < (hevc ? 3u : 1u); ++phase) {
      at = hevc ? 23 : 6;
      for (unsigned g = 0; g < groups; ++g) {
        unsigned type, count;
        if (hevc) {
            if (n - at < 3) return BAD;
            type = p[at++] & 63; count = be16(p + at); at += 2;
        } else {
            type = g ? 8 : 7;
            if (!g) count = p[5] & 31;
            else { if (at == n) return BAD; count = p[at++]; }
        }
        for (unsigned i = 0; i < count; ++i) {
            if (n - at < 2) return BAD;
            unsigned size = be16(p + at); at += 2;
            if (!size || size > n - at) return BAD;
            if (hevc) {
                if (size < 2 || (p[at] & 0x80) || !(p[at + 1] & 7) ||
                    ((p[at] >> 1) & 63) != type) return BAD;
                if (type == 32 + phase) {
                    seen |= 1u << (type - 32);
                    if (add(out, used, p + at, size)) return BAD;
                }
            } else {
                if ((p[at] & 0x9f) != type) return BAD;
                seen |= 1u << g;
                if (add(out, used, p + at, size)) return BAD;
            }
            at += size;
        }
      }
      if (hevc && at != n) return BAD;
    }
    return seen == (hevc ? 7u : 3u) ? 0 : BAD;
}
int zq_codec_config(struct zq_codec *c, const uint8_t *p, size_t n) {
    size_t size, written;
    unsigned hevc, length_size;
    if (!c || !p || !n || n > LIMIT || record(&p, &n, &hevc, 0)) return BAD;
    if (config_pass(p, n, hevc, 0, &size, &length_size)) return BAD;
    uint8_t *header = malloc(size);
    if (!header) return NOMEM;
    if (config_pass(p, n, hevc, header, &written, &length_size) || written != size) {
        free(header); return BAD;
    }
    free(c->header);
    c->header = header; c->header_len = size;
    c->length_size = length_size; c->hevc = hevc;
    return 0;
}
int zq_codec_frame(const struct zq_codec *c, const uint8_t *p, size_t n,
                   uint8_t **out, size_t *out_len) {
    size_t at = 0, total = 0;
    unsigned key = 0;
    if (!out || !out_len) return BAD;
    *out = 0; *out_len = 0;
    if (!c || !p || !n || n > LIMIT || !c->header || !c->header_len ||
        (c->length_size != 1 && c->length_size != 2 && c->length_size != 4)) return BAD;
    while (at < n) {
        if (n - at < c->length_size) return BAD;
        uint32_t size = 0;
        for (unsigned i = 0; i < c->length_size; ++i) size = size << 8 | p[at++];
        if (!size || size > n - at || (p[at] & 0x80)) return BAD;
        unsigned type = p[at] & 31;
        if (c->hevc) {
            if (size < 2 || !(p[at + 1] & 7)) return BAD;
            type = (p[at] >> 1) & 63;
            key |= type >= 16 && type <= 21;
        } else key |= type == 5;
        if (add(0, &total, p + at, size)) return BAD;
        at += size;
    }
    if (key && c->header_len > LIMIT - total) return BAD;
    uint8_t *result = malloc(total + (key ? c->header_len : 0));
    if (!result) return NOMEM;
    total = 0;
    if (key) { memcpy(result, c->header, c->header_len); total = c->header_len; }
    at = 0;
    while (at < n) {
        uint32_t size = 0;
        for (unsigned i = 0; i < c->length_size; ++i) size = size << 8 | p[at++];
        /* The first pass established these bounds. */
        add(result, &total, p + at, size); at += size;
    }
    *out = result; *out_len = total;
    return 0;
}
void zq_codec_clear(struct zq_codec *c) {
    if (!c) return;
    free(c->header); c->header = 0; c->header_len = 0;
    c->hevc = c->length_size = 0;
}
