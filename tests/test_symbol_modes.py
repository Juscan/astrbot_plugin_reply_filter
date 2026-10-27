"""Symbol-mode regressions for pure policy and stubbed hook formatting.

These tests deliberately use the existing plugin fixtures without inheriting a
TestCase, so discovery does not repeat the hook regression suite. Assertions
inspect result.chain; no real AstrBot/OneBot delivery or browser is exercised.
"""

import unittest

from astrbot_plugin_reply_filter import processing
from astrbot_plugin_reply_filter.processing import Settings, split_short_text
from astrbot_plugin_reply_filter.rendering import MarkdownDocument
from astrbot_plugin_reply_filter.tests import test_plugin as plugin_stubs


EMOTICONS = (
    "(^_^)",
    "(｡･ω･｡)ﾉ",
    "(๑•̀ㅂ•́)و",
    "T_T",
    ">_<",
    ":-)",
    "¯\\_(ツ)_/¯",
)
NATURAL_EXTRA_SYMBOLS = "~～…%％‰‱@#&/\\_*-"
GRAPHEMES = (
    "👨‍👩‍👧‍👦",  # Family joined with zero-width joiners.
    "👍🏽",  # Skin-tone modifier.
    "👩🏽‍💻",  # Modifier and joiner in the same cluster.
    "🇨🇳",  # Regional-indicator pair.
    "1️⃣",  # Digit, variation selector, enclosing keycap.
    "#️⃣",
    "*️⃣",
    "❤️",  # Emoji variation selector.
    "e\u0301",  # Combining acute accent.
    "कि",  # Devanagari vowel sign.
    "가",  # Hangul jamo syllable.
)


def mode_settings(mode="natural", **overrides):
    """Use the public configuration loader, not unvalidated mutable settings."""
    return Settings.from_mapping({"symbol_filter_mode": mode, **overrides})


class SymbolModeSettingsTests(unittest.TestCase):
    def test_defaults_are_strict_and_custom_strings_are_empty(self):
        for settings in (Settings(), Settings.from_mapping({})):
            with self.subTest(settings=settings):
                self.assertEqual(settings.symbol_filter_mode, "strict")
                self.assertEqual(settings.custom_keep_symbols, "")
                self.assertEqual(settings.custom_remove_symbols, "")

    def test_all_three_explicit_modes_are_accepted(self):
        for mode in ("strict", "natural", "custom"):
            with self.subTest(mode=mode):
                self.assertEqual(mode_settings(mode).symbol_filter_mode, mode)

    def test_unknown_modes_and_non_string_modes_are_rejected(self):
        for value in ("", "STRICT", "Natural", "lenient", None, True, 1, [], {}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "symbol_filter_mode"):
                    Settings.from_mapping({"symbol_filter_mode": value})

    def test_custom_strings_accept_empty_unicode_and_256_characters(self):
        for name in ("custom_keep_symbols", "custom_remove_symbols"):
            for value in ("", " ", "，。🙂", "a" * 256):
                with self.subTest(name=name, value=value):
                    self.assertEqual(getattr(mode_settings("custom", **{name: value}), name), value)

    def test_custom_strings_reject_non_strings_even_in_inactive_modes(self):
        for mode in ("strict", "natural", "custom"):
            for name in ("custom_keep_symbols", "custom_remove_symbols"):
                for value in (None, 0, False, ["!"], {"!"}, {"x": "!"}):
                    with self.subTest(mode=mode, name=name, value=value):
                        with self.assertRaisesRegex(ValueError, name):
                            mode_settings(mode, **{name: value})

    def test_custom_strings_reject_257_characters_even_in_inactive_modes(self):
        for mode in ("strict", "natural", "custom"):
            for name in ("custom_keep_symbols", "custom_remove_symbols"):
                with self.subTest(mode=mode, name=name):
                    with self.assertRaisesRegex(ValueError, name):
                        mode_settings(mode, **{name: "a" * 257})

    def test_custom_strings_reject_embedded_control_characters(self):
        for name in ("custom_keep_symbols", "custom_remove_symbols"):
            for control in ("\x00", "\t", "\n", "\r", "\x1b", "\x7f", "\x85"):
                with self.subTest(name=name, control=repr(control)):
                    with self.assertRaisesRegex(ValueError, name):
                        mode_settings("custom", **{name: "a" + control + "b"})


class StrictCompatibilityTests(unittest.TestCase):
    def test_default_mode_matches_explicit_strict_for_legacy_inputs(self):
        texts = (
            "你好，世界！\n下一句。",
            "“你好”「朋友」★🙂 + = —— END",
            "A~B…C～D",
            "访问 https://example.com/a-b?q=1.2#part。",
            "first.last+tag@example.com -3.14 12:30 25％",
            " ".join(EMOTICONS),
        )
        for text in texts:
            with self.subTest(text=text):
                self.assertEqual(split_short_text(text, Settings()),
                                 split_short_text(text, mode_settings("strict")))

    def test_strict_retains_legacy_punctuation_and_symbol_removal(self):
        settings = mode_settings("strict", split_symbols="")
        self.assertEqual(split_short_text("“你好”「朋友」★🙂 + = —— END", settings),
                         ["你好朋友    END"])

    def test_strict_does_not_gain_emoticon_protection(self):
        settings = mode_settings("strict", split_symbols="", preserve_urls_numbers=False)
        expected = {"(^_^)": [], "T_T": ["TT"], ">_<": [], ":-)": [], "¯\\_(ツ)_/¯": ["ツ"]}
        for face, result in expected.items():
            with self.subTest(face=face):
                self.assertEqual(split_short_text(face, settings), result)

    def test_strict_still_splits_on_natural_extra_delimiters(self):
        self.assertEqual(split_short_text("A~B…C～D", mode_settings("strict")),
                         ["A", "B", "C", "D"])

    def test_strict_strip_false_keeps_symbols_but_still_splits(self):
        settings = mode_settings("strict", strip_symbols=False)
        self.assertEqual(split_short_text("“甲”🙂~乙！", settings), ["“甲”🙂", "乙"])


class NaturalSymbolTests(unittest.TestCase):
    def test_ordinary_punctuation_is_removed_without_delimiters(self):
        settings = mode_settings(split_symbols="", preserve_urls_numbers=False)
        self.assertEqual(split_short_text("“甲”（乙）【丙】〔丁〕，。！？；：、'\"", settings),
                         ["甲乙丙丁"])

    def test_unicode_symbol_categories_survive_even_when_configured_as_delimiters(self):
        symbols = "🙂★♥♬☀☕©®™€￥$＋−×÷=<>|^`"
        settings = mode_settings(split_symbols=symbols, preserve_urls_numbers=False)
        self.assertEqual(split_short_text(symbols, settings), [symbols])

    def test_explicit_natural_keep_set_overrides_all_matching_delimiters(self):
        settings = mode_settings(split_symbols=NATURAL_EXTRA_SYMBOLS, preserve_urls_numbers=False)
        self.assertEqual(split_short_text(NATURAL_EXTRA_SYMBOLS, settings), [NATURAL_EXTRA_SYMBOLS])

    def test_ordinary_sentence_delimiters_still_split(self):
        self.assertEqual(split_short_text("甲，乙。丙！丁？戊；己：庚、辛", mode_settings()),
                         ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛"])

    def test_default_tildes_and_ellipsis_no_longer_split_natural_chat(self):
        self.assertEqual(split_short_text("好呀~真的～稍等…马上来！", mode_settings()),
                         ["好呀~真的～稍等…马上来"])

    def test_url_email_and_structured_numbers_keep_existing_protection(self):
        url = "https://example.com/a-b?q=hello_world&n=1.2#part"
        address = "first.last+tag@example-domain.co.uk"
        source = url + "，" + address + "，-3.14，12:30，2026-10-27，25％"
        self.assertEqual(split_short_text(source, mode_settings()),
                         [url, address, "-3.14", "12:30", "2026-10-27", "25％"])

    def test_preserve_urls_numbers_false_releases_number_punctuation(self):
        settings = mode_settings(preserve_urls_numbers=False)
        self.assertEqual(split_short_text("3.14，12:30，+25%", settings),
                         ["3", "14", "12", "30", "+25%"])

    def test_strip_false_preserves_ordinary_punctuation_without_disabling_splitting(self):
        settings = mode_settings(strip_symbols=False)
        self.assertEqual(split_short_text("“甲”🙂~，乙！", settings), ["“甲”🙂~", "乙"])

    def test_strip_false_still_does_not_split_on_natural_keep_set(self):
        settings = mode_settings(strip_symbols=False, split_symbols=NATURAL_EXTRA_SYMBOLS)
        self.assertEqual(split_short_text(NATURAL_EXTRA_SYMBOLS, settings), [NATURAL_EXTRA_SYMBOLS])

    def test_symbol_only_natural_chat_is_not_dropped(self):
        self.assertEqual(split_short_text("🙂★~…", mode_settings()), ["🙂★~…"])
        self.assertEqual(split_short_text("，。！？", mode_settings()), [])


class ProtectedAtomTests(unittest.TestCase):
    def test_all_requested_emoticons_are_preserved_in_natural_and_custom(self):
        for mode in ("natural", "custom"):
            for face in EMOTICONS:
                with self.subTest(mode=mode, face=face):
                    self.assertEqual(split_short_text(face, mode_settings(mode)), [face])

    def test_emoticons_are_atomic_at_a_one_character_soft_length_limit(self):
        for mode in ("natural", "custom"):
            settings = mode_settings(mode, max_segment_length=1)
            for face in EMOTICONS:
                with self.subTest(mode=mode, face=face):
                    self.assertEqual(split_short_text("甲" + face + "乙", settings), ["甲", face, "乙"])

    def test_emoticons_override_punctuation_delimiters_inside_the_atom(self):
        settings = mode_settings(split_symbols="()^_:.<>/\\¯･•")
        for face in EMOTICONS:
            with self.subTest(face=face):
                self.assertEqual(split_short_text(face, settings), [face])

    def test_ordinary_chinese_parentheses_are_not_protected_as_an_emoticon(self):
        settings = mode_settings(split_symbols="", max_segment_length=1)
        self.assertEqual(split_short_text("(普通中文)", settings), ["普", "通", "中", "文"])

    def test_parenthesized_arithmetic_is_not_one_protected_atom(self):
        settings = mode_settings(split_symbols="", max_segment_length=1, preserve_urls_numbers=False)
        for expression in ("1+2=3", "0+0", "0=0", "0*0", "0/0"):
            with self.subTest(expression=expression):
                self.assertEqual(split_short_text("(" + expression + ")", settings), list(expression))

    def test_grapheme_clusters_are_never_split_at_the_soft_length_boundary(self):
        for mode in ("natural", "custom"):
            settings = mode_settings(mode, split_symbols="", max_segment_length=1,
                                     preserve_urls_numbers=False)
            for grapheme in GRAPHEMES:
                with self.subTest(mode=mode, grapheme=grapheme):
                    self.assertEqual(split_short_text("甲" + grapheme + "乙", settings),
                                     ["甲", grapheme, "乙"])

    def test_adjacent_emoji_clusters_remain_separate_soft_length_atoms(self):
        settings = mode_settings(max_segment_length=1, preserve_urls_numbers=False)
        self.assertEqual(split_short_text("👍🏽🇨🇳1️⃣", settings), ["👍🏽", "🇨🇳", "1️⃣"])

    def test_numeric_protection_never_cuts_into_a_keycap_grapheme(self):
        cases = (("+1️⃣", ["+", "1️⃣"]), ("-1️⃣", ["-", "1️⃣"]),
                 ("3.1️⃣", ["3", "1️⃣"]))
        for mode in ("natural", "custom"):
            settings = mode_settings(mode, max_segment_length=1)
            self.assertTrue(settings.preserve_urls_numbers)
            for source, expected in cases:
                with self.subTest(mode=mode, source=source):
                    segments = split_short_text(source, settings)
                    self.assertEqual(segments, expected)
                    self.assertEqual("".join(segments).count("1️⃣"), 1)
                    for segment in segments:
                        if "\ufe0f" in segment or "\u20e3" in segment:
                            self.assertEqual(segment, "1️⃣")

    def test_url_still_exceeds_soft_length_without_corruption(self):
        url = "https://example.com/" + "x" * 40
        for mode in ("natural", "custom"):
            with self.subTest(mode=mode):
                self.assertEqual(split_short_text("甲 " + url + " 乙",
                                                 mode_settings(mode, max_segment_length=1)),
                                 ["甲", url, "乙"])


class CustomSymbolTests(unittest.TestCase):
    def test_empty_custom_strings_behave_like_natural(self):
        for text in ("“你好”🙂~世界…！", " ".join(EMOTICONS), "3.14，https://example.com/a_b"):
            with self.subTest(text=text):
                self.assertEqual(split_short_text(text, mode_settings("custom")),
                                 split_short_text(text, mode_settings("natural")))

    def test_custom_strings_are_ignored_in_strict_and_natural_modes(self):
        for mode, expected in (("strict", ["甲乙"]), ("natural", ["甲★乙"])):
            with self.subTest(mode=mode):
                settings = mode_settings(mode, split_symbols="", custom_keep_symbols="，",
                                         custom_remove_symbols="★")
                self.assertEqual(split_short_text("甲，★乙", settings), expected)

    def test_custom_keep_preserves_punctuation_before_delimiter_splitting(self):
        settings = mode_settings("custom", custom_keep_symbols="，。")
        self.assertEqual(split_short_text("甲，乙。丙！", settings), ["甲，乙。丙"])

    def test_custom_remove_can_delete_natural_kept_symbols(self):
        settings = mode_settings("custom", split_symbols="", custom_remove_symbols="*★🙂")
        self.assertEqual(split_short_text("甲*★🙂乙", settings), ["甲乙"])

    def test_custom_remove_wins_over_custom_keep(self):
        settings = mode_settings("custom", split_symbols="", custom_keep_symbols="，★",
                                 custom_remove_symbols="，★")
        self.assertEqual(split_short_text("甲，★乙", settings), ["甲乙"])

    def test_removing_an_ordinary_delimiter_keeps_its_segment_boundary(self):
        for keep in ("", "，"):
            with self.subTest(keep=keep):
                settings = mode_settings("custom", custom_keep_symbols=keep,
                                         custom_remove_symbols="，")
                self.assertEqual(split_short_text("甲，乙", settings), ["甲", "乙"])

    def test_custom_remove_is_literal_and_can_delete_non_symbol_characters(self):
        settings = mode_settings("custom", split_symbols="", custom_remove_symbols="甲[]")
        self.assertEqual(split_short_text("甲[乙]甲", settings), ["乙"])

    def test_removing_any_emoji_member_removes_the_entire_grapheme(self):
        cases = (
            ("👨‍👩‍👧‍👦", "👧"),
            ("👍🏽", "🏽"),
            ("👩🏽‍💻", "💻"),
            ("🇨🇳", "🇨"),
            ("1️⃣", "1"),
            ("#️⃣", "#"),
            ("*️⃣", "*"),
            ("❤️", "\ufe0f"),
        )
        for atom, removed in cases:
            with self.subTest(atom=atom, removed=removed):
                settings = mode_settings("custom", split_symbols="", custom_remove_symbols=removed,
                                         preserve_urls_numbers=False)
                self.assertEqual(split_short_text("甲" + atom + "乙", settings), ["甲乙"])

    def test_removing_an_emoticon_member_removes_the_entire_face(self):
        cases = (
            ("(^_^)", "_"),
            ("(｡･ω･｡)ﾉ", "ω"),
            ("(๑•̀ㅂ•́)و", "ㅂ"),
            ("T_T", "_"),
            (">_<", ">"),
            (":-)", ":"),
            ("¯\\_(ツ)_/¯", "ツ"),
        )
        for face, removed in cases:
            with self.subTest(face=face, removed=removed):
                settings = mode_settings("custom", split_symbols="", custom_remove_symbols=removed)
                self.assertEqual(split_short_text(face, settings), [])

    def test_custom_keep_cannot_rescue_an_atom_marked_for_deletion(self):
        for atom, removed in (("👍🏽", "🏽"), ("(^_^)", "_")):
            with self.subTest(atom=atom):
                settings = mode_settings("custom", split_symbols="", custom_keep_symbols=atom,
                                         custom_remove_symbols=removed)
                self.assertEqual(split_short_text("甲" + atom + "乙", settings), ["甲乙"])

    def test_url_email_and_number_protection_has_highest_priority(self):
        url = "https://example.com/a_b?q=1.2#tag"
        email = "first.last+tag@example.com"
        atoms = [url, email, "-3.14", "12:30", "2026-10-27", "25％"]
        settings = mode_settings("custom", custom_remove_symbols=":/@.+-%_?=#％",
                                 max_segment_length=1)
        self.assertEqual(split_short_text("，".join(atoms), settings), atoms)

    def test_numeric_protection_does_not_prevent_whole_keycap_deletion(self):
        settings = mode_settings("custom", max_segment_length=1, custom_remove_symbols="1")
        self.assertTrue(settings.preserve_urls_numbers)
        for source, expected in (("+1️⃣", ["+"]), ("-1️⃣", ["-"]), ("3.1️⃣", ["3"])):
            with self.subTest(source=source):
                segments = split_short_text(source, settings)
                self.assertEqual(segments, expected)
                self.assertNotIn("\ufe0f", "".join(segments))
                self.assertNotIn("\u20e3", "".join(segments))

    def test_disabling_url_number_protection_allows_custom_deletion(self):
        settings = mode_settings("custom", split_symbols="", preserve_urls_numbers=False,
                                 custom_remove_symbols="@.+-")
        self.assertEqual(split_short_text("a+b@example.com -3.14", settings), ["abexamplecom 314"])

    def test_ordinary_parenthesized_arithmetic_is_not_deleted_as_an_emoticon(self):
        settings = mode_settings("custom", split_symbols="", preserve_urls_numbers=False,
                                 custom_remove_symbols="=")
        self.assertEqual(split_short_text("(1+2=3)", settings), ["1+23"])
        self.assertEqual(split_short_text("(0=0)", settings), ["00"])

    def test_strip_false_disables_custom_deletion_including_whole_atoms(self):
        settings = mode_settings("custom", strip_symbols=False, custom_remove_symbols="_🙂，")
        self.assertEqual(split_short_text("(^_^)🙂，乙！", settings), ["(^_^)🙂", "乙"])

    def test_strip_false_still_honors_custom_keep_before_splitting(self):
        settings = mode_settings("custom", strip_symbols=False, custom_keep_symbols="，",
                                 custom_remove_symbols="，🙂")
        self.assertEqual(split_short_text("甲，🙂乙！", settings), ["甲，🙂乙"])

    def test_deleted_atoms_do_not_consume_segment_length(self):
        settings = mode_settings("custom", max_segment_length=2, custom_remove_symbols="🙂")
        self.assertEqual(split_short_text("甲🙂乙", settings), ["甲乙"])


class MarkdownProbeTests(unittest.TestCase):
    def test_strict_probe_returns_the_original_text(self):
        source = "**粗体**\n" + " ".join(EMOTICONS)
        self.assertEqual(processing.markdown_probe(source, mode_settings("strict")), source)

    def test_natural_and_custom_probe_hide_all_recognized_emoticons(self):
        document = MarkdownDocument()
        for mode in ("natural", "custom"):
            for face in EMOTICONS:
                with self.subTest(mode=mode, face=face):
                    probe = processing.markdown_probe(face, mode_settings(mode))
                    self.assertIsInstance(probe, str)
                    self.assertNotEqual(probe, face)
                    self.assertFalse(document.contains_markdown(probe))
                    self.assertEqual(split_short_text(face, mode_settings(mode)), [face])

    def test_probe_does_not_replace_ordinary_parentheses_or_arithmetic(self):
        source = "(普通中文) (1+2=3) (0+0) (0=0) (0*0)"
        for mode in ("natural", "custom"):
            with self.subTest(mode=mode):
                self.assertEqual(processing.markdown_probe(source, mode_settings(mode)), source)

    def test_real_markdown_is_still_detected_next_to_an_emoticon(self):
        sources = (
            "**粗体**",
            "```python\nprint(1)\n```",
            "- 第一项\n- 第二项",
            "> 真正的引用",
            "# 标题",
            "`inline_code`",
            "[链接](https://example.com)",
        )
        document = MarkdownDocument()
        for mode in ("natural", "custom"):
            for source in sources:
                with self.subTest(mode=mode, source=source):
                    probe = processing.markdown_probe(source + "\n>_<", mode_settings(mode))
                    self.assertTrue(document.contains_markdown(probe))

    def test_emoticon_like_link_destinations_do_not_hide_real_markdown(self):
        document = MarkdownDocument()
        for source in ("[链接](^_^)", "[链接](T_T)", "![图片](T_T)"):
            self.assertTrue(document.contains_markdown(source))
            for mode in ("natural", "custom"):
                with self.subTest(source=source, mode=mode):
                    self.assertTrue(document.contains_markdown(processing.markdown_probe(
                        source, mode_settings(mode))))

    def test_probe_leaves_face_free_markdown_unchanged(self):
        source = "**粗体**\n\n> 引用\n\n- 条目\n\n```python\nx = 1 + 2\n```"
        for mode in ("natural", "custom"):
            with self.subTest(mode=mode):
                self.assertEqual(processing.markdown_probe(source, mode_settings(mode)), source)

    def test_custom_removal_does_not_turn_an_emoticon_into_markdown(self):
        settings = mode_settings("custom", custom_remove_symbols="_>")
        self.assertFalse(MarkdownDocument().contains_markdown(processing.markdown_probe(">_<", settings)))


class SymbolModePluginTests(plugin_stubs.PluginFixtures, unittest.IsolatedAsyncioTestCase):
    """Nine core-only formatting cases, not simulated or real core sending."""

    def make_plugin(self, **config):
        config.setdefault("symbol_filter_mode", "natural")
        return super().make_plugin(**config)

    async def test_short_emoticons_stay_plain_in_natural_and_custom(self):
        for mode in ("natural", "custom"):
            for face in EMOTICONS:
                with self.subTest(mode=mode, face=face):
                    plugin = self.make_plugin(symbol_filter_mode=mode)
                    event = self.event(face)
                    original, chain = event.result, event.result.chain
                    await plugin.filter_reply(event)
                    plugin.renderer.render.assert_not_awaited()
                    self.assertEqual(chain, [plugin_stubs.Plain(face)])
                    self.assert_in_place(event, original, chain)

    async def test_natural_chat_symbols_survive_hook_formatting(self):
        plugin = self.make_plugin()
        source = "好呀~🙂稍等…马上来！"
        event = self.event(source)
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        plugin.renderer.render.assert_not_awaited()
        self.assertEqual(chain, [plugin_stubs.Plain("好呀~🙂稍等…马上来")])
        self.assert_in_place(event, original, chain)

    async def test_custom_keep_and_remove_are_used_by_hook_formatting(self):
        plugin = self.make_plugin(symbol_filter_mode="custom", custom_keep_symbols="，",
                                  custom_remove_symbols="🙂")
        event = self.event("甲，🙂乙！")
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        plugin.renderer.render.assert_not_awaited()
        self.assertEqual(chain, [plugin_stubs.Plain("甲，乙")])
        self.assert_in_place(event, original, chain)

    async def test_real_markdown_renders_original_despite_custom_deletion(self):
        for source in ("**粗体** >_<", "```python\nprint('(^_^)')\n```",
                       "- 条目\n- 条目", "> 引用\n>_<", "[链接](T_T)"):
            with self.subTest(source=source):
                plugin = self.make_plugin(symbol_filter_mode="custom", custom_remove_symbols="*_>")
                event = self.event(source)
                original, chain = event.result, event.result.chain
                await plugin.filter_reply(event)
                plugin.renderer.render.assert_awaited_once_with(source, "image")
                self.assertIsInstance(chain[0], plugin_stubs.Image)
                self.assertEqual(chain[0].data, plugin_stubs.PNG)
                self.assert_in_place(event, original, chain)

    async def test_long_plain_reply_renders_original_not_filtered_or_probe_text(self):
        source = "长文本(^_^)🙂，保留全部原文！"
        plugin = self.make_plugin(symbol_filter_mode="custom", long_text_threshold=5,
                                  custom_remove_symbols="_🙂，！")
        event = self.event(source)
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        plugin.renderer.render.assert_awaited_once_with(source, "image")
        self.assertIsInstance(chain[0], plugin_stubs.Image)
        self.assert_in_place(event, original, chain)

    async def test_strict_keeps_existing_markdown_detection_for_blockquote_like_face(self):
        plugin = self.make_plugin(symbol_filter_mode="strict")
        event = self.event(">_<")
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        plugin.renderer.render.assert_awaited_once_with(">_<", "image")
        self.assertIsInstance(chain[0], plugin_stubs.Image)
        self.assert_in_place(event, original, chain)

    async def test_strip_false_disables_custom_deletion_without_false_markdown(self):
        plugin = self.make_plugin(symbol_filter_mode="custom", strip_symbols=False,
                                  custom_remove_symbols="_🙂")
        event = self.event(">_<🙂，乙！")
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        plugin.renderer.render.assert_not_awaited()
        self.assertEqual(chain, [plugin_stubs.Plain(">_<🙂\n"), plugin_stubs.Plain("乙")])
        self.assert_in_place(event, original, chain)

    async def test_adjacent_plain_components_merge_before_emoticon_probe(self):
        plugin = self.make_plugin()
        event = plugin_stubs.FakeEvent(plugin_stubs.FakeResult([
            plugin_stubs.Plain(">"), plugin_stubs.Plain("_<"),
        ]))
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        plugin.renderer.render.assert_not_awaited()
        self.assertEqual(chain, [plugin_stubs.Plain(">_<")])
        self.assert_in_place(event, original, chain)

    async def test_render_failure_keeps_original_markdown_in_custom_mode(self):
        source = "**粗体** >_< 🙂！"
        plugin = self.make_plugin(symbol_filter_mode="custom", custom_remove_symbols="*_>🙂！",
                                  pdf_on_image_error=False)
        plugin.renderer.render.side_effect = RuntimeError("render unavailable")
        event = self.event(source)
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        plugin.renderer.render.assert_awaited_once_with(source, "image")
        self.assertEqual(chain, [plugin_stubs.Plain(source)])
        self.assert_in_place(event, original, chain)


if __name__ == "__main__":
    unittest.main()
