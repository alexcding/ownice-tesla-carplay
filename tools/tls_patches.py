"""Opt-in (--tls-chacha): z-tesla's player sockets prefer ChaCha20-Poly1305 (firmware 2.9.11).

The player's control/video/audio WebSockets (z-tesla, libwebsockets with a statically
linked OpenSSL 1.1.1) negotiate TLS 1.3 AES-256-GCM with Chrome. The T113's Cortex-A7
has no AES instructions, so software AES-GCM is the box's most expensive cipher, and
every video frame goes through it. ChaCha20-Poly1305 is the cheap one on such CPUs;
Chrome offers it in every TLS 1.3 handshake but ranks AES first on machines with AES
hardware, so the server has to choose.

z-tesla-sh.sh starts z-tesla with OPENSSL_CONF pointing at a config whose
system_default puts ChaCha20 first with server preference. Only z-tesla gets it:
the z-connman web servers (Go, own TLS) that serve uploads and rollback are untouched.
An OpenSSL that does not load the file keeps today's cipher. Check after a power
cycle: `openssl s_client -connect 3.3.3.3:5002` reports the negotiated cipher.
"""

Z_TESLA_SH_PATH = "usr/z-loader/bin/z-tesla-sh.sh"
CONF_PATH = "usr/z-loader/zq-openssl.cnf"

CONF = """\
# ownice-tesla-carplay: z-tesla's TLS 1.3 prefers ChaCha20 (no AES instructions on the T113)
openssl_conf = zq_init

[zq_init]
ssl_conf = zq_ssl

[zq_ssl]
system_default = zq_tls

[zq_tls]
Ciphersuites = TLS_CHACHA20_POLY1305_SHA256:TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384
Options = ServerPreference
"""

SCRIPT_PATCHES = {
    Z_TESLA_SH_PATH: [
        ("z-tesla loads the ChaCha20-first OpenSSL config",
         'echo "$process_sender start"\n        $LOADER_PATH/bin/z-tesla &',
         'echo "$process_sender start"\n        OPENSSL_CONF=$LOADER_PATH/zq-openssl.cnf $LOADER_PATH/bin/z-tesla &',
         1),
    ],
}
