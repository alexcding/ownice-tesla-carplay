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
    unsigned nal_flags;
};
int zq_screen_property(void *stream, unsigned flags, const void *key,
                       const void *qualifier, const void *value) {
    (void)flags; (void)qualifier;
    struct screen_context *s = ScreenStreamGetContext(stream);
    if (!s || !key || !value) return -6756;
    if (CFEqual(key, zq_avcc())) {
        if (CFGetTypeID(value) != CFDataGetTypeID()) return -6756;
        long n = CFDataGetLength(value);
        if (n <= 0) return -6756;
        struct zq_codec c = {s->header, s->header_len, 0, 0};
        int err = zq_codec_config(&c, CFDataGetBytePtr(value), (size_t)n);
        if (err) return err;
        s->header = c.header; s->header_len = c.header_len;
        s->nal_flags = c.length_size | (c.hevc << 8);
        return 0;
    }
    int width = CFEqual(key, zq_width());
    if (width || CFEqual(key, zq_height())) {
        int err = 0;
        long long n = CFGetInt64(value, &err);
        if (err || n < 0 || n > 8192) return -6756;
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
        if (!err) ScreenStreamSend_CB(out, n, s->width, s->height, ticks);
    }
    free(out);
    if (completion) completion(completion_context);
    return err;
}
