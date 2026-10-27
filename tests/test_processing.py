"""Pure reply-policy tests; run with unittest discovery from the parent repo."""

import math
import unittest

from astrbot_plugin_reply_filter.processing import (
    DEFAULT_SPLIT_SYMBOLS,
    Settings,
    cap_messages,
    clean_text,
    fallback_chunks,
    remove_thinking,
    render_kind,
    split_short_text,
    visible_length,
)


class SettingsTests(unittest.TestCase):
    def test_defaults_and_unknown_host_configuration(self):
        self.assertEqual(Settings.from_mapping({}), Settings())
        self.assertEqual(Settings.from_mapping({"unrelated_host_option": 42}), Settings())
        self.assertEqual(Settings().split_symbols, DEFAULT_SPLIT_SYMBOLS)
        self.assertTrue(Settings().force_non_streaming)
        self.assertIn(".", Settings().split_symbols)

    def test_blocked_words_are_frozen_without_mutating_configuration(self):
        words = ["secret", "敏感词"]
        config = {"blocked_words": words}
        settings = Settings.from_mapping(config)
        words.append("later")
        self.assertEqual(settings.blocked_words, ("secret", "敏感词"))
        self.assertIs(config["blocked_words"], words)

    def test_each_integer_setting_accepts_both_bounds(self):
        bounds = {
            "long_text_threshold": (1, 100000),
            "auto_pdf_threshold": (1, 100000),
            "max_segment_length": (1, 10000),
            "max_messages": (1, 50),
            "image_width": (320, 1920),
            "font_size": (12, 48),
            "max_render_chars": (1000, 500000),
            "cache_retention_hours": (1, 168),
        }
        for name, (low, high) in bounds.items():
            for value in (low, high):
                with self.subTest(name=name, value=value):
                    self.assertEqual(getattr(Settings.from_mapping({name: value}), name), value)

    def test_each_integer_setting_rejects_out_of_range_and_wrong_types(self):
        bounds = {
            "long_text_threshold": (1, 100000),
            "auto_pdf_threshold": (1, 100000),
            "max_segment_length": (1, 10000),
            "max_messages": (1, 50),
            "image_width": (320, 1920),
            "font_size": (12, 48),
            "max_render_chars": (1000, 500000),
            "cache_retention_hours": (1, 168),
        }
        for name, (low, high) in bounds.items():
            for value in (low - 1, high + 1, True, False, float(low), str(low), None):
                with self.subTest(name=name, value=value):
                    with self.assertRaisesRegex(ValueError, name):
                        Settings.from_mapping({name: value})

    def test_boolean_settings_require_actual_bools(self):
        for name in (
            "enabled", "force_non_streaming", "pdf_on_image_error", "strip_symbols",
            "preserve_urls_numbers", "remove_think",
        ):
            for value in (True, False):
                with self.subTest(name=name, valid=value):
                    self.assertIs(getattr(Settings.from_mapping({name: value}), name), value)
            for value in (0, 1, "true", "false", None):
                with self.subTest(name=name, invalid=value):
                    with self.assertRaisesRegex(ValueError, name):
                        Settings.from_mapping({name: value})

    def test_numeric_intervals_accept_finite_endpoints(self):
        for name, values in (
            ("send_interval", (0, 0.25, 10)),
            ("render_timeout", (0.1, 1, 300)),
        ):
            for value in values:
                with self.subTest(name=name, value=value):
                    self.assertEqual(getattr(Settings.from_mapping({name: value}), name), value)

    def test_numeric_intervals_reject_invalid_values(self):
        for name, values in (
            ("send_interval", (-0.01, 10.01, math.nan, math.inf, -math.inf, True, "1", None)),
            ("render_timeout", (0, 0.099, 300.01, math.nan, math.inf, False, "30", None)),
        ):
            for value in values:
                with self.subTest(name=name, value=value):
                    with self.assertRaisesRegex(ValueError, name):
                        Settings.from_mapping({name: value})

    def test_enormous_numeric_intervals_are_rejected_as_configuration_errors(self):
        for name in ("send_interval", "render_timeout"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, name):
                    Settings.from_mapping({name: 10 ** 1000})

    def test_render_modes_are_explicit(self):
        for mode in ("image", "pdf", "auto"):
            with self.subTest(mode=mode):
                self.assertEqual(Settings.from_mapping({"render_mode": mode}).render_mode, mode)
        for mode in ("png", "IMAGE", "", None, 1, []):
            with self.subTest(invalid=mode):
                with self.assertRaisesRegex(ValueError, "render_mode"):
                    Settings.from_mapping({"render_mode": mode})

    def test_split_symbols_must_be_a_literal_string(self):
        self.assertEqual(Settings.from_mapping({"split_symbols": ".*[]"}).split_symbols, ".*[]")
        self.assertEqual(Settings.from_mapping({"split_symbols": ""}).split_symbols, "")
        for value in (None, 1, [","], {","}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "split_symbols"):
                    Settings.from_mapping({"split_symbols": value})

    def test_blocked_words_reject_malformed_values(self):
        for value in ("word", {"word"}, None, [""], ["  "], [1], ["valid", None]):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "blocked_words"):
                    Settings.from_mapping({"blocked_words": value})


class ThinkingAndCleaningTests(unittest.TestCase):
    def test_simple_and_adjacent_thinking_blocks_are_removed(self):
        self.assertEqual(remove_thinking("前<think>隐私</think>后<think>另一段</think>结尾"), "前后结尾")

    def test_nested_same_name_thinking_blocks(self):
        self.assertEqual(remove_thinking("A<think>outer<think>inner</think>outer</think>B"), "AB")

    def test_nested_mixed_names_and_case_are_supported(self):
        text = "A< THINK >outer<Analysis>inner</analysis><thinking>more</thinking></ THINK >B"
        self.assertEqual(remove_thinking(text), "AB")

    def test_unclosed_thinking_drops_the_entire_remaining_tail(self):
        for text in ("answer<think>secret", "answer<think>outer<analysis>inner</analysis>"):
            with self.subTest(text=text):
                self.assertEqual(remove_thinking(text), "answer")
        self.assertEqual(remove_thinking("<analysis>all secret"), "")

    def test_mismatched_closing_tag_does_not_leak_nested_content(self):
        self.assertEqual(remove_thinking("A<think>secret</analysis>still secret</think>B"), "AB")
        self.assertEqual(remove_thinking("A<think><analysis>secret</think>still secret"), "A")

    def test_non_thinking_markup_is_not_removed(self):
        text = "<thinking-about>保留</thinking-about><b>正文</b>"
        self.assertEqual(remove_thinking(text), text)

    def test_thinking_stack_continues_nested_blocks_across_separate_fragments(self):
        stack = []
        self.assertEqual(remove_thinking("可见<think>隐私", stack), "可见")
        self.assertEqual(stack, ["think"])
        self.assertEqual(remove_thinking("<analysis>子隐私", stack), "")
        self.assertEqual(stack, ["think", "analysis"])
        self.assertEqual(remove_thinking("</analysis>隐私</think>答案", stack), "答案")
        self.assertEqual(stack, [])

    def test_cleaning_shared_thinking_state_does_not_leak_across_components(self):
        settings = Settings(blocked_words=("秘密",))
        stack = []
        self.assertEqual(clean_text("前<think>隐藏", settings, stack), "前")
        self.assertEqual(clean_text("仍然隐藏", settings, stack), "")
        self.assertEqual(clean_text("</think>后秘密", settings, stack), "后")
        self.assertEqual(stack, [])

    def test_disabling_thinking_removal_preserves_markup_and_external_state(self):
        stack = ["think"]
        text = "<analysis>保留</analysis>正文"
        self.assertEqual(clean_text(text, Settings(remove_think=False), stack), text)
        self.assertEqual(stack, ["think"])

    def test_cleaning_normalizes_lines_and_removes_control_and_invisible_characters(self):
        text = "\ufeff A\r\nB\rC\tD\x00\x1b\u200b \n"
        self.assertEqual(clean_text(text, Settings()), "A\nB\nC\tD")

    def test_cleaning_blocked_words_is_literal_and_removes_every_occurrence(self):
        settings = Settings(blocked_words=("secret", ".*"))
        self.assertEqual(clean_text("secretA.*Bsecret\nC", settings), "AB\nC")

    def test_cleaning_can_preserve_thinking_markup(self):
        settings = Settings(remove_think=False)
        self.assertEqual(clean_text(" <think>保留</think>答案 ", settings), "<think>保留</think>答案")

    def test_empty_and_fully_filtered_text_stays_empty(self):
        for text in ("", " \r\n\t", "<think>secret</think>", "\x00\ufeff\u200b"):
            with self.subTest(text=text):
                self.assertEqual(clean_text(text, Settings()), "")


class RoutingTests(unittest.TestCase):
    def test_150_characters_remain_text_and_151_render(self):
        self.assertIsNone(render_kind("字" * 150, False, Settings()))
        self.assertEqual(render_kind("字" * 151, False, Settings()), "image")

    def test_boundary_counts_visible_characters_not_whitespace(self):
        text = "字 " * 150 + "\n\t\u3000"
        self.assertEqual(visible_length(text), 150)
        self.assertIsNone(render_kind(text, False, Settings()))
        self.assertEqual(render_kind(text + "！", False, Settings()), "image")

    def test_visible_length_counts_unicode_and_punctuation(self):
        self.assertEqual(visible_length(" 甲\t乙\n！🙂\u3000"), 4)
        self.assertEqual(visible_length(""), 0)

    def test_even_short_markdown_is_rendered(self):
        self.assertEqual(render_kind("**短**", True, Settings()), "image")
        self.assertEqual(render_kind("`x`", True, Settings(render_mode="pdf")), "pdf")

    def test_explicit_pdf_mode_does_not_render_plain_short_text(self):
        settings = Settings(render_mode="pdf")
        self.assertIsNone(render_kind("字" * 150, False, settings))
        self.assertEqual(render_kind("字" * 151, False, settings), "pdf")

    def test_auto_pdf_threshold_is_strictly_greater(self):
        settings = Settings(render_mode="auto", auto_pdf_threshold=3000)
        self.assertEqual(render_kind("字" * 3000, False, settings), "image")
        self.assertEqual(render_kind("字" * 3001, False, settings), "pdf")

    def test_custom_plain_text_threshold_is_respected(self):
        settings = Settings(long_text_threshold=3)
        self.assertIsNone(render_kind("abc", False, settings))
        self.assertEqual(render_kind("abcd", False, settings), "image")


class ShortTextTests(unittest.TestCase):
    def test_default_split_symbols_and_repeated_delimiters(self):
        self.assertEqual(split_short_text("你好，世界！\n下一句。；；结束", Settings()), ["你好", "世界", "下一句", "结束"])

    def test_plain_english_sentences_split_on_periods_without_merging(self):
        text = "First sentence. Second sentence!Third sentence? Done."
        self.assertEqual(split_short_text(text, Settings()), [
            "First sentence", "Second sentence", "Third sentence", "Done",
        ])

    def test_unicode_punctuation_and_symbols_are_removed(self):
        self.assertEqual(split_short_text("“你好”「朋友」★🙂 + = —— END", Settings()), ["你好朋友    END"])

    def test_strip_symbols_can_be_disabled_but_splitting_remains(self):
        self.assertEqual(split_short_text("“你好”★，`世界`!", Settings(strip_symbols=False)), ["“你好”★", "`世界`"])

    def test_custom_delimiters_are_literal_not_regex(self):
        settings = Settings(split_symbols=".*[]", strip_symbols=False, preserve_urls_numbers=False)
        self.assertEqual(split_short_text("A.B*C[D]E", settings), ["A", "B", "C", "D", "E"])

    def test_empty_delimiters_allow_line_breaks_inside_a_segment(self):
        settings = Settings(split_symbols="", strip_symbols=False)
        self.assertEqual(split_short_text("A\nB", settings), ["A\nB"])

    def test_url_query_fragment_and_punctuation_are_preserved(self):
        url = "https://example.com/a-b?q=hello_world&n=1.2#part"
        self.assertEqual(split_short_text("访问 " + url + "。谢谢！", Settings()), ["访问 " + url, "谢谢"])

    def test_trailing_sentence_punctuation_is_not_part_of_a_url(self):
        self.assertEqual(split_short_text("https://example.com/path, 后文", Settings()), ["https://example.com/path", "后文"])

    def test_balanced_parentheses_in_a_url_are_not_removed_as_sentence_punctuation(self):
        url = "https://en.wikipedia.org/wiki/Function_(mathematics)"
        self.assertEqual(split_short_text("参考 " + url + "。", Settings()), ["参考 " + url])
        self.assertEqual(split_short_text("(" + url + ").", Settings()), [url])

    def test_email_plus_address_survives_symbol_stripping(self):
        address = "first.last+tag@example-domain.co.uk"
        self.assertEqual(split_short_text("邮箱 " + address + "，收到", Settings()), ["邮箱 " + address, "收到"])

    def test_decimals_versions_times_dates_and_percentages_are_atomic(self):
        text = "价格 -3.14，版本 v1.2.3，时间 12:30，日期 2026-10-27，比例 +12.5% 和 25％"
        self.assertEqual(split_short_text(text, Settings()), [
            "价格 -3.14", "版本 v1.2.3", "时间 12:30", "日期 2026-10-27", "比例 +12.5% 和 25％",
        ])

    def test_protected_atoms_exceed_soft_length_limit_without_corruption(self):
        url = "https://example.com/" + "x" * 50
        settings = Settings(max_segment_length=5)
        self.assertEqual(split_short_text("前 " + url + " 后", settings), ["前", url, "后"])
        self.assertEqual(split_short_text("123456789.123456789", settings), ["123456789.123456789"])

    def test_unprotected_text_obeys_hard_segment_length(self):
        settings = Settings(max_segment_length=3)
        self.assertEqual(split_short_text("甲乙丙丁戊己庚", settings), ["甲乙丙", "丁戊己", "庚"])

    def test_protection_can_be_disabled_explicitly(self):
        settings = Settings(preserve_urls_numbers=False)
        self.assertEqual(split_short_text("3.14 v1.2.3 a+b@example.com", settings), ["3", "14 v1", "2", "3 abexample", "com"])

    def test_empty_and_symbol_only_input_does_not_create_empty_messages(self):
        for text in ("", "   ", "，。！？\n", "★🙂“”"):
            with self.subTest(text=text):
                self.assertEqual(split_short_text(text, Settings()), [])

    def test_grouping_to_max_messages_does_not_truncate_content_or_order(self):
        segments = ["片段" + str(index) for index in range(25)]
        groups = cap_messages(segments, Settings().max_messages)
        self.assertEqual(len(groups), 10)
        self.assertEqual(groups[:9], [[item] for item in segments[:9]])
        self.assertEqual(groups[-1], segments[9:])
        self.assertEqual([item for group in groups for item in group], segments)

    def test_grouping_at_limit_below_limit_and_limit_one(self):
        for count in (0, 2, 3):
            items = list(range(count))
            with self.subTest(count=count):
                self.assertEqual(cap_messages(items, 3), [[item] for item in items])
        self.assertEqual(cap_messages([1, 2, 3], 1), [[1, 2, 3]])

    def test_grouping_retains_object_identity_and_does_not_mutate_input(self):
        items = [object() for _ in range(4)]
        original = items.copy()
        groups = cap_messages(items, 2)
        self.assertEqual(items, original)
        self.assertTrue(all(a is b for a, b in zip([item for group in groups for item in group], items)))

    def test_fallback_chunks_are_lossless_for_markdown_and_whitespace(self):
        text = "```python\nprint('hello')\n```\n\n**bold**\t" * 100
        chunks = fallback_chunks(text, 17)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(0 < len(chunk) <= 17 for chunk in chunks))
        self.assertEqual(fallback_chunks(""), [])
        self.assertEqual(fallback_chunks("x" * 1001), ["x" * 1000, "x"])


if __name__ == "__main__":
    unittest.main()
