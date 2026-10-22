/* Adapter for the observed stock 2.9.11 ScreenStream ABI. */
#include "hevc_codec.h"
extern void free(void *);
extern void *ScreenStreamGetContext(void *);
extern int CFEqual(const void *, const void *);
extern unsigned CFGetTypeID(const void *);
extern unsigned CFDataGetTypeID(void);
extern long long CFGetInt64(const void *, int *);
extern const uint8_t *CFDataGetBytePtr(const void *);
extern long CFDataGetLength(const void *);
extern void ScreenStreamSend_CB(const void *, size_t, int, int, uint64_t);
extern const void *zq_avcc(void), *zq_width(void), *zq_height(void);

/* Stock ScreenStreamInitialize allocates these six words; its finalizer owns
 * config and header. Keep that layout and leave config null: our header is an
 * independent allocation, not a borrowed CFData pointer. */
struct screen_context {
    void *config;
    int width, height;
    uint8_t *header;
    size_t header_len;
    unsigned nal_flags; /* length size, bit 8 HEVC; ZQ_DIAG: bit 9 pending drops; bits 10-15 config events; bits 16-31 drop streak (modulo 65536) */
};

#ifdef ZQ_DIAG
/* A private UUID-tagged SEI carries scalar events, never picture data. */
static void zq_num(char *out, unsigned *at, const char *label, unsigned v) {
    while (*label) out[(*at)++] = *label++;
    for (unsigned i = 0; i < 8; ++i)
        out[(*at)++] = "0123456789abcdef"[(v >> (28 - 4 * i)) & 15];
}
static void zq_report(struct screen_context *s, uint64_t ticks) {
    uint8_t sei[128];
    char *text = (char *)sei + 24;
    unsigned at = 0;
    const char *label = "zq events";
    while (*label) text[at++] = *label++;
    zq_num(text, &at, " flags=", s->nal_flags);
    zq_num(text, &at, " header=", (unsigned)s->header_len);
    zq_num(text, &at, " size=", (unsigned)s->width << 16 | ((unsigned)s->height & 0xffff));
    const char *uuid = "ownice-zq-diag01";
    sei[0] = sei[1] = sei[2] = 0; sei[3] = 1;
    sei[4] = 39 << 1; sei[5] = 1;
    sei[6] = 5; sei[7] = (uint8_t)(16 + at);
    for (unsigned i = 0; i < 16; ++i) sei[8 + i] = (uint8_t)uuid[i];
    sei[24 + at] = 0x80;
    ScreenStreamSend_CB(sei, 25 + at, s->width, s->height, ticks);
}
/* Keep the stock six-word allocation. Pending event bits are emitted only after
 * a real frame callback, so setup never calls into the sender and the first
 * reconnect checkpoint always describes video. Codes: 1 ok, 2 null value,
 * 3 wrong type, 4 empty data, 5 malformed config, 6 allocation failure.
 * Dropped-frame count wraps (never saturates); bit 9 retains pending drops
 * even at an exact multiple of 65536. Good frames clear the entire streak. */
static void zq_config_event(struct screen_context *s, unsigned result) {
    s->nal_flags |= (1u << (result + 9));
}
#endif
int zq_screen_property(void *stream, unsigned flags, const void *key,
                       const void *qualifier, const void *value) {
    (void)flags; (void)qualifier;
    struct screen_context *s = ScreenStreamGetContext(stream);
    if (!s) return -6756;
#ifdef ZQ_DIAG
    if (!key || !value) zq_config_event(s, 2);
#endif
    if (!key || !value) return -6756;
    if (CFEqual(key, zq_avcc())) {
        if (CFGetTypeID(value) != CFDataGetTypeID()) {
#ifdef ZQ_DIAG
            zq_config_event(s, 3);
#endif
            return -6756;
        }
        long n = CFDataGetLength(value);
        if (n <= 0) {
#ifdef ZQ_DIAG
            zq_config_event(s, 4);
#endif
            return -6756;
        }
        struct zq_codec c = {s->header, s->header_len, 0, 0};
        int err = zq_codec_config(&c, CFDataGetBytePtr(value), (size_t)n);
#ifdef ZQ_DIAG
        zq_config_event(s, err ? (err == -6728 ? 6 : 5) : 1);
#endif
        if (err) return err;
        s->header = c.header; s->header_len = c.header_len;
        s->nal_flags = (s->nal_flags & ~0x1ffu) | c.length_size | (c.hevc << 8);
        return 0;
    }
    int width = CFEqual(key, zq_width());
    if (width || CFEqual(key, zq_height())) {
        int err = 0;
        long long n = CFGetInt64(value, &err);
        if (err || n < 0 || n > 8192) {
#ifdef ZQ_DIAG
            zq_config_event(s, 5);
#endif
            return -6756;
        }
        if (width) s->width = (int)n; else s->height = (int)n;
        return 0;
    }
    return -6714;
}
int zq_screen_frame(void *stream, const uint8_t *data, size_t len,
                    uint64_t ticks, const void *options,
                    void (*completion)(void *), void *completion_context) {
    (void)options;
    struct screen_context *s = ScreenStreamGetContext(stream);
    uint8_t *out = 0;
    size_t n = 0;
    int err = -6756;
    if (s) {
        struct zq_codec c = {s->header, s->header_len,
                             s->nal_flags & 255, (s->nal_flags >> 8) & 1};
        err = zq_codec_frame(&c, data, len, &out, &n);
        if (!err) {
            ScreenStreamSend_CB(out, n, s->width, s->height, ticks);
#ifdef ZQ_DIAG
            if (s->nal_flags & ~0x1ffu) {
                zq_report(s, ticks);
                s->nal_flags &= 0x1ffu;
            }
#endif
        }
#ifdef ZQ_DIAG
        else {
            s->nal_flags += 1u << 16; /* unsigned wrap, never stop recording */
            s->nal_flags |= 1u << 9;
        }
#endif
    }
    free(out);
    if (completion) completion(completion_context);
    return err;
}
