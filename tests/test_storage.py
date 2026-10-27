"""PDF cache regression tests using isolated small temporary directories."""

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from astrbot_plugin_reply_filter import storage


class PdfStorageTests(unittest.TestCase):
    # Keep filesystem-created mtimes near our frozen clock, not a future epoch.
    NOW = float(int(time.time()))
    PDF = b"%PDF-1.7\nminimal test payload\n%%EOF\n"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="reply-filter-cache-test-", dir=Path(__file__).resolve().parent,
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "cache"

    def put_file(self, name, data=b"%PDF-existing", age=0):
        self.cache.mkdir(parents=True, exist_ok=True)
        path = self.cache / name
        path.write_bytes(data)
        timestamp = self.NOW - age
        os.utime(path, (timestamp, timestamp))
        return path

    def save(self, data=None, retention=24):
        with patch.object(storage.time, "time", return_value=self.NOW):
            return storage.save_pdf(self.PDF if data is None else data, self.cache, retention)

    def test_size_limits_match_the_documented_mib_contract(self):
        self.assertEqual(storage.MAX_PDF_BYTES, 32 * 1024 * 1024)
        self.assertEqual(storage.MAX_CACHE_BYTES, 256 * 1024 * 1024)

    def test_valid_pdf_is_saved_byte_for_byte_with_a_unique_absolute_name(self):
        result = self.save()
        self.assertTrue(result.is_absolute())
        self.assertEqual(result.parent, self.cache.resolve())
        self.assertRegex(result.name, r"^reply-[0-9a-f]{32}\.pdf$")
        self.assertEqual(result.read_bytes(), self.PDF)

    def test_nested_cache_directory_is_created_on_demand(self):
        self.cache = self.root / "nested" / "pdf" / "cache"
        result = self.save()
        self.assertTrue(self.cache.is_dir())
        self.assertTrue(result.is_file())

    def test_invalid_pdf_headers_are_rejected_before_creating_a_directory(self):
        for data in (b"", b"PNG", b"%PD", b"%PDF", b" %PDF-1.7", b"\xef\xbb\xbf%PDF-1.7", b"<html>error</html>"):
            with self.subTest(data=data):
                with self.assertRaisesRegex(ValueError, "PDF 文件头"):
                    self.save(data)
                self.assertFalse(self.cache.exists())

    def test_invalid_pdf_does_not_trigger_expired_file_cleanup(self):
        old = self.put_file("reply-old.pdf", age=25 * 3600)
        with self.assertRaisesRegex(ValueError, "PDF 文件头"):
            self.save(b"not a PDF")
        self.assertTrue(old.exists())

    def test_per_pdf_limit_allows_exact_boundary(self):
        data = b"%PDF-" + b"x" * 11
        with patch.object(storage, "MAX_PDF_BYTES", 16):
            result = self.save(data)
        self.assertEqual(result.read_bytes(), data)

    def test_per_pdf_limit_rejects_one_byte_above_before_creating_a_directory(self):
        with patch.object(storage, "MAX_PDF_BYTES", 16):
            with self.assertRaisesRegex(ValueError, "32 MiB"):
                self.save(b"%PDF-" + b"x" * 12)
        self.assertFalse(self.cache.exists())

    def test_expired_generated_pdfs_are_removed_and_fresh_pdfs_are_preserved(self):
        old = self.put_file("reply-old.pdf", age=24 * 3600 + 1)
        fresh = self.put_file("reply-fresh.pdf", age=24 * 3600 - 1)
        result = self.save()
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        self.assertEqual(fresh.read_bytes(), b"%PDF-existing")
        self.assertTrue(result.exists())

    def test_file_exactly_at_retention_cutoff_is_not_expired(self):
        boundary = self.put_file("reply-boundary.pdf", age=24 * 3600)
        self.save()
        self.assertTrue(boundary.exists())

    def test_custom_retention_hours_are_applied(self):
        expired = self.put_file("reply-old.pdf", age=3601)
        fresh = self.put_file("reply-fresh.pdf", age=3599)
        self.save(retention=1)
        self.assertFalse(expired.exists())
        self.assertTrue(fresh.exists())

    def test_only_generated_pdf_names_in_this_directory_are_cleaned(self):
        unrelated = [
            self.put_file("user-document.pdf", age=99 * 3600),
            self.put_file("reply-old.txt", age=99 * 3600),
            self.put_file("reply-partial.pdf.tmp", age=99 * 3600),
        ]
        nested = self.cache / "nested"
        nested.mkdir()
        nested_file = nested / "reply-old.pdf"
        nested_file.write_bytes(self.PDF)
        os.utime(nested_file, (self.NOW - 99 * 3600,) * 2)
        self.save()
        for path in unrelated + [nested_file]:
            with self.subTest(path=path.name):
                self.assertTrue(path.exists())

    def test_expired_files_free_space_before_the_cache_size_check(self):
        expired = self.put_file("reply-expired.pdf", data=b"x" * 30, age=25 * 3600)
        with patch.object(storage, "MAX_CACHE_BYTES", len(self.PDF)):
            result = self.save()
        self.assertFalse(expired.exists())
        self.assertEqual(result.read_bytes(), self.PDF)

    def test_cache_limit_includes_new_pdf_and_all_unexpired_generated_pdfs(self):
        first = self.put_file("reply-first.pdf", data=b"x" * 7)
        second = self.put_file("reply-second.pdf", data=b"x" * 9)
        with patch.object(storage, "MAX_CACHE_BYTES", 16 + len(self.PDF) - 1):
            with self.assertRaisesRegex(ValueError, "缓存达到 256 MiB"):
                self.save()
        self.assertEqual(set(self.cache.iterdir()), {first, second})
        self.assertEqual(first.read_bytes(), b"x" * 7)
        self.assertEqual(second.read_bytes(), b"x" * 9)

    def test_cache_limit_allows_exact_boundary(self):
        existing = self.put_file("reply-current.pdf", data=b"x" * 10)
        with patch.object(storage, "MAX_CACHE_BYTES", 10 + len(self.PDF)):
            result = self.save()
        self.assertEqual(set(self.cache.iterdir()), {existing, result})

    def test_new_pdf_alone_over_cache_limit_is_rejected_without_temporary_files(self):
        with patch.object(storage, "MAX_CACHE_BYTES", len(self.PDF) - 1):
            with self.assertRaisesRegex(ValueError, "缓存达到"):
                self.save()
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_fresh_pdfs_are_not_evicted_to_make_room_for_an_inflight_upload(self):
        fresh = self.put_file("reply-uploading.pdf", data=b"%PDF-uploading", age=1)
        with patch.object(storage, "MAX_CACHE_BYTES", len(self.PDF)):
            with self.assertRaisesRegex(ValueError, "缓存达到"):
                self.save()
        self.assertEqual(fresh.read_bytes(), b"%PDF-uploading")
        self.assertEqual(list(self.cache.iterdir()), [fresh])

    def test_unrelated_files_do_not_consume_the_generated_pdf_cache_budget(self):
        unrelated = self.put_file("my-document.pdf", data=b"x" * 100)
        temporary = self.put_file("reply-old.pdf.tmp", data=b"x" * 100)
        with patch.object(storage, "MAX_CACHE_BYTES", len(self.PDF)):
            result = self.save()
        self.assertTrue(unrelated.exists())
        self.assertTrue(temporary.exists())
        self.assertEqual(result.read_bytes(), self.PDF)

    def test_many_saves_at_the_same_time_do_not_overwrite_each_other(self):
        results = [self.save(self.PDF + str(index).encode("ascii")) for index in range(25)]
        self.assertEqual(len(set(results)), 25)
        self.assertEqual(len(list(self.cache.glob("reply-*.pdf"))), 25)
        for index, result in enumerate(results):
            with self.subTest(index=index):
                self.assertEqual(result.read_bytes(), self.PDF + str(index).encode("ascii"))
        self.assertEqual(list(self.cache.glob("*.tmp")), [])

    def test_successful_save_uses_atomic_replace_and_removes_temporary_name(self):
        original_replace = storage.os.replace
        with patch.object(storage.os, "replace", wraps=original_replace) as replace:
            result = self.save()
        replace.assert_called_once()
        source, destination = replace.call_args.args
        self.assertEqual(source, Path(str(result) + ".tmp"))
        self.assertEqual(destination, result)
        self.assertFalse(source.exists())
        self.assertEqual(result.read_bytes(), self.PDF)

    def test_failed_atomic_replace_propagates_and_cleans_temporary_file(self):
        with patch.object(storage.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                self.save()
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_symlink_like_entries_are_not_deleted_or_counted(self):
        # Mock just the symlink predicate: Windows often lacks symlink privileges.
        linked = self.put_file("reply-link.pdf", data=b"x" * 100, age=25 * 3600)
        original_is_symlink = Path.is_symlink

        def is_symlink(path):
            return path == linked or original_is_symlink(path)

        with patch.object(Path, "is_symlink", is_symlink):
            with patch.object(storage, "MAX_CACHE_BYTES", len(self.PDF)):
                result = self.save()
        self.assertTrue(linked.exists())
        self.assertTrue(result.exists())

    def test_a_pdf_disappearing_during_stat_does_not_abort_the_write(self):
        disappearing = self.put_file("reply-disappearing.pdf")
        original_stat = Path.stat

        def stat(path, *args, **kwargs):
            if path == disappearing:
                raise FileNotFoundError("a concurrent cleanup removed this PDF")
            return original_stat(path, *args, **kwargs)

        with patch.object(Path, "stat", stat):
            result = self.save()
        self.assertEqual(result.read_bytes(), self.PDF)


if __name__ == "__main__":
    unittest.main()
