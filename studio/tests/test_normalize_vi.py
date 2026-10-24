import pytest

from server.normalize_vi import normalize, number_to_words, split_sentences


@pytest.mark.parametrize(
    ("digits", "expected"),
    [
        ("0", "không"),
        ("5", "năm"),
        ("10", "mười"),
        ("11", "mười một"),
        ("15", "mười lăm"),
        ("20", "hai mươi"),
        ("21", "hai mươi mốt"),
        ("24", "hai mươi tư"),
        ("25", "hai mươi lăm"),
        ("100", "một trăm"),
        ("105", "một trăm lẻ năm"),
        ("111", "một trăm mười một"),
        ("205", "hai trăm lẻ năm"),
        ("1000", "một nghìn"),
        ("2001", "hai nghìn không trăm lẻ một"),
        ("1050", "một nghìn không trăm năm mươi"),
        ("100000", "một trăm nghìn"),
        ("1000000", "một triệu"),
        ("1500000", "một triệu năm trăm nghìn"),
        ("2025", "hai nghìn không trăm hai mươi lăm"),
        ("1000000000", "một tỷ"),
    ],
)
def test_number_to_words(digits, expected):
    assert number_to_words(digits) == expected


def test_decimal_uses_pha():
    assert normalize("Tôi có 3.5 quả táo") == "Tôi có ba phẩy năm quả táo"


def test_percent_and_currency():
    assert normalize("Giảm 50% còn 120.000đ") == "Giảm năm mươi phần trăm còn một trăm hai mươi nghìn đồng"


def test_time_and_hours():
    assert normalize("Họp lúc 7:30 sáng") == "Họp lúc bảy giờ ba mươi sáng"


def test_units():
    assert normalize("Đi 5 km, nặng 3 kg") == "Đi năm ki lô mét, nặng ba ki lô gam"


def test_plain_text_unchanged():
    text = "Xin chào Việt Nam!"
    assert normalize(text) == text


def test_split_sentences():
    assert split_sentences("Một. Hai ba! Bốn?") == ["Một.", "Hai ba!", "Bốn?"]
