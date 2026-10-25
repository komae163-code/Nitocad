"""Pure section helpers for spar and lug (stdlib only)."""


def i_beam_points(height, flange_w, flange_t, web_t):
    h, fw, tf, tw = height, flange_w, flange_t, web_t
    return [(-fw / 2, 0), (fw / 2, 0), (fw / 2, tf), (tw / 2, tf), (tw / 2, h - tf), (fw / 2, h - tf),
            (fw / 2, h), (-fw / 2, h), (-fw / 2, h - tf), (-tw / 2, h - tf), (-tw / 2, tf), (-fw / 2, tf)]
