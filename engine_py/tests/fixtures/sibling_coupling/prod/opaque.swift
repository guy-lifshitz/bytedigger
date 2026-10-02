// GH1200 fixture (AC37, gate round-2 finding N2) — a scope file whose extension
// falls in the "anything else" row of spec §1.3.1: the tool has NO language
// support for it, so only the path family can run.
//
// A gate must not report "clean" for a file it cannot analyse, so scope mode
// must emit `W_UNSUPPORTED_SCOPE_EXT` always and exit 2 under `--require-clean`
// — even under the DEFAULT channel set, where `E_PARTIAL_CHANNELS_GATE` cannot
// be the cause.

struct OpaqueBox {
    let identifier: Int
}
