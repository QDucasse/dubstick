from dubstick.agreement import Opinion, fold_bpm, judge

STRONG = {"bpm_confidence": 3.8, "key_strength": 0.8}
WEAK = {"bpm_confidence": 0.9, "key_strength": 0.8}


def verdict(*opinions):
    return judge(list(opinions), 90, 180)


def test_fold_half_and_double_time():
    assert fold_bpm(70, 90, 180) == 140
    assert fold_bpm(280, 90, 180) == 140
    assert fold_bpm(128, 90, 180) == 128
    assert fold_bpm(0, 90, 180) is None


def test_full_track_checked_against_preview():
    v = verdict(Opinion("essentia_local", 139.6, "8A", **STRONG), Opinion("essentia_deezer", 70, "9A", **STRONG))
    assert v.agreement == "agree"
    assert v.bpm == 139.6 and v.camelot == "8A"


def test_disagree_on_bpm():
    v = verdict(Opinion("essentia_youtube", 132, "8A"), Opinion("essentia_deezer", 128, "8A"))
    assert v.agreement == "disagree"


def test_disagree_on_key():
    v = verdict(Opinion("essentia_recording", 128, "8A"), Opinion("essentia_deezer", 128, "3B"))
    assert v.agreement == "disagree"


def test_best_full_source_wins():
    v = verdict(
        Opinion("essentia_youtube", 120, "1A"),
        Opinion("essentia_recording", 126, "7A"),
        Opinion("essentia_local", 128, "8A"),
        Opinion("essentia_deezer", 128, "8A"),
    )
    assert v.agreement == "agree" and v.bpm == 128

    without_local = verdict(Opinion("essentia_youtube", 120, "1A"), Opinion("essentia_recording", 126, "7A"))
    assert without_local.bpm == 126


def test_single_source_depends_on_confidence():
    assert verdict(Opinion("essentia_youtube", 125, "4A", **STRONG)).agreement == "single"
    assert verdict(Opinion("essentia_youtube", 125, "4A", **WEAK)).agreement == "weak"
    assert verdict(Opinion("essentia_deezer", 125, "4A", key_strength=0.3)).agreement == "weak"


def test_agreement_outweighs_low_confidence():
    v = verdict(Opinion("essentia_youtube", 125, "4A", **WEAK), Opinion("essentia_deezer", 125, "4A", **WEAK))
    assert v.agreement == "agree"


def test_file_tag_does_not_vote():
    assert verdict(Opinion("file_tag", 125, None)).agreement == "none"
