/* Adapter for the observed stock 2.9.11 ScreenStream ABI. */
#include "hevc_codec.h"
#include "zq_report.h"
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
static void zq_report(struct screen_context *s, unsigned flags, uint64_t ticks,
                      const char *label, int err, size_t len) {
    uint8_t sei[128];
    char *text = (char *)sei + 24;
    unsigned at = 0;
    while (*label) text[at++] = *label++;
    zq_num(text, &at, " flags=", flags);
    zq_num(text, &at, " err=", (unsigned)err);
    zq_num(text, &at, " len=", (unsigned)len);
    zq_num(text, &at, " header=", (unsigned)s->header_len);
    zq_num(text, &at, " size=", (unsigned)s->width << 16 | ((unsigned)s->height & 0xffff));
    const char *uuid = ZQ_REPORT_UUID;
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
    __atomic_fetch_or(&s->nal_flags, 1u << (result + 9), __ATOMIC_RELAXED);
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
        /* The frame thread updates the same word (diagnostic counters): swap in
         * the format bits without losing a concurrent update (ldrex/strex). */
        unsigned old = __atomic_load_n(&s->nal_flags, __ATOMIC_RELAXED);
        while (!__atomic_compare_exchange_n(&s->nal_flags, &old,
                                            (old & ~0x1ffu) | c.length_size | (c.hevc << 8),
                                            1, __ATOMIC_RELEASE, __ATOMIC_RELAXED)) {}
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
        unsigned flags = __atomic_load_n(&s->nal_flags, __ATOMIC_ACQUIRE);
        struct zq_codec c = {s->header, s->header_len, flags & 255, (flags >> 8) & 1};
        err = zq_codec_frame(&c, data, len, &out, &n);
        if (!err) {
            ScreenStreamSend_CB(out, n, s->width, s->height, ticks);
#ifdef ZQ_DIAG
            /* Take the pending events and clear the streak in one atomic step. */
            if (flags & ~0x1ffu) {
                unsigned taken = __atomic_fetch_and(&s->nal_flags, 0x1ffu, __ATOMIC_RELAXED);
                if (taken & ~0x1ffu) zq_report(s, taken, ticks, "zq events", 0, 0);
            }
#endif
        }
#ifdef ZQ_DIAG
        else {
            /* Count the drop (streak in bits 16-31, unsigned wrap, never stops
             * recording), then mark drops pending (bit 9); `now` is the word
             * after both updates. */
            __atomic_fetch_add(&s->nal_flags, 1u << 16, __ATOMIC_RELAXED);
            unsigned now = __atomic_or_fetch(&s->nal_flags, 1u << 9, __ATOMIC_RELAXED);
            /* A blackout has no good frame to carry the deferred events: report
             * the first drop of a streak and every 32nd, from this same frame
             * callback, once the stream has its dimensions. */
            if (s->width && s->height && ((now >> 16) & 31) == 1)
                zq_report(s, now, ticks, "zq dropped", err, len);
        }
#endif
    }
    free(out);
    if (completion) completion(completion_context);
    return err;
}
