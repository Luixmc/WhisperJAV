"""Minimum on-screen time for the subtitles CrispASR writes (crispasr_runner)."""

from whisperjav.pipelines.crispasr_runner import (
    MIN_DURATION_MS,
    enforce_min_duration,
    spread_short_subtitles,
)


def test_lines_said_at_the_same_instant_play_one_after_another():
    # SDMF-018 at 11:31: two zero-length lines, then a 26-second one.
    times = [[691360, 691360], [691360, 691360], [691360, 717680]]
    assert spread_short_subtitles(times) == 3
    assert times == [[691360, 692160], [692160, 692960], [692960, 717680]]


def test_a_zero_length_line_before_a_gap_just_gets_the_minimum():
    times = [[633920, 633920], [634720, 639120]]
    spread_short_subtitles(times)
    assert times == [[633920, 634720], [634720, 639120]]


def test_a_long_next_line_is_not_pushed_under_the_minimum():
    times = [[1000, 1000], [1000, 1900]]
    spread_short_subtitles(times)
    assert times == [[1000, 1450], [1450, 1900]]
    assert all(end > start for start, end in times)


def test_lines_long_enough_are_left_alone():
    times = [[0, 1000], [1500, 4000]]
    assert spread_short_subtitles(times) == 0
    assert times == [[0, 1000], [1500, 4000]]


def test_the_file_keeps_its_text_and_gets_the_new_times(tmp_path):
    srt = tmp_path / "x.srt"
    srt.write_text(
        "1\n00:10:33,280 --> 00:10:33,920\nやめ！\n\n"
        "2\n00:10:33,920 --> 00:10:33,920\nねえ！\n\n"
        "3\n00:10:34,720 --> 00:10:39,120\nなんだよ？\n", encoding="utf-8")
    assert enforce_min_duration(srt) == 3  # the third one only moves
    assert srt.read_text(encoding="utf-8") == (
        "1\n00:10:33,280 --> 00:10:34,080\nやめ！\n\n"
        "2\n00:10:34,080 --> 00:10:34,880\nねえ！\n\n"
        "3\n00:10:34,880 --> 00:10:39,120\nなんだよ？\n\n")


def test_a_file_without_short_lines_is_not_rewritten(tmp_path):
    srt = tmp_path / "x.srt"
    original = "1\n00:00:01,000 --> 00:00:03,000\nそうですね。\n"
    srt.write_text(original, encoding="utf-8")
    assert enforce_min_duration(srt) == 0
    assert srt.read_text(encoding="utf-8") == original
    assert MIN_DURATION_MS == 800
