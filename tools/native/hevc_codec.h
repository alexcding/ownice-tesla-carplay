#ifndef ZQ_HEVC_CODEC_H
#define ZQ_HEVC_CODEC_H
#include <stddef.h>
#include <stdint.h>

/* Own converter, independent of the vendor receiver. Output is always Annex B.
 * Configuration parsing is transactional: failure leaves the previous config.
 * The layout of this struct is private; screen_hevc.c adapts the vendor ABI. */
struct zq_codec {
    uint8_t *header;
    size_t header_len;
    unsigned length_size;
    unsigned hevc;
};
int zq_codec_config(struct zq_codec *, const uint8_t *, size_t);
int zq_codec_frame(const struct zq_codec *, const uint8_t *, size_t,
                   uint8_t **, size_t *);
void zq_codec_clear(struct zq_codec *);
#endif
