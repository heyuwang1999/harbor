from harbor_api.core.text import count_tokens, detect_script, normalize


def test_normalize_converts_hong_kong_traditional_to_simplified() -> None:
    # 僱 is the HK variant of 雇; hk2s handles it where a plain t2s mapping may not.
    assert normalize("僱員連續工作滿四星期") == "雇员连续工作满四星期"


def test_normalize_is_idempotent_on_simplified_text() -> None:
    simplified = normalize("僱員連續工作滿四星期")

    assert normalize(simplified) == simplified


def test_normalize_leaves_english_untouched() -> None:
    assert normalize("Sick leave requires a medical certificate.") == (
        "Sick leave requires a medical certificate."
    )


def test_detect_script_distinguishes_english_traditional_and_simplified() -> None:
    assert detect_script("How long is the probation period?") == "en"
    assert detect_script("請病假要唔要醫生紙？") == "zh-Hant"
    assert detect_script("强积金供款比例是多少？") == "zh-Hans"


def test_detect_script_treats_mostly_english_with_a_stray_character_as_english() -> None:
    assert detect_script("What is the MPF 強積金 rate for a new joiner this year?") == "en"


def test_count_tokens_charges_one_token_per_cjk_character() -> None:
    assert count_tokens("年假") == 2
    assert count_tokens("annual leave") == 3  # 12 chars / 4
    assert count_tokens("") == 1  # never zero: callers use it for budgeting
