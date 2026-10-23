/* In-band converter report format, shared by the diagnostic converter
 * (screen_hevc.c) and the checkpoint filter (stream_diag_sender.c); the player
 * mirrors it in player/worker.js (NATIVE_REPORT). An HEVC prefix SEI
 * (start code, NAL 39, user_data_unregistered): 00 00 00 01 4e 01 05 <size>
 * <16-byte UUID> <ASCII text> 80, size = 16 + text length. */
#ifndef ZQ_REPORT_H
#define ZQ_REPORT_H
#define ZQ_REPORT_UUID "ownice-zq-diag01"
#endif
