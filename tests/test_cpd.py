import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parents[1]
CPD = SCRIPT_DIR / "bin" / "cpd"


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _read(path: Path) -> bytes:
    return path.read_bytes()


def _run_cpd(args: list[str], env: Optional[dict[str, str]] = None) -> subprocess.CompletedProcess:
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run([str(CPD), *args], env=e, check=True, capture_output=True, text=True)


class TestCpd(unittest.TestCase):
    def test_prints_plan_and_copy_status_with_md5_ok(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "a.txt"
            dst = root / "b.txt"
            _write(src, b"hello")

            p = _run_cpd([str(src), str(dst)], env={"CPD_VERIFY_MD5": "1"})

            self.assertIn("复制计划", p.stderr)
            self.assertIn("源:", p.stderr)
            self.assertIn("已复制 文件", p.stderr)
            # MD5 verification runs silently on success (no "md5" banner);
            # a successful copy without a verification error implies md5 matched.

    def test_prints_skipped_status_on_second_run(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "a.txt"
            dst = root / "b.txt"
            _write(src, b"hello")

            _run_cpd([str(src), str(dst)], env={"CPD_VERIFY_MD5": "1", "CPD_LOG": "all"})
            p2 = _run_cpd([str(src), str(dst)], env={"CPD_VERIFY_MD5": "1", "CPD_LOG": "all"})

            self.assertIn("已跳过 文件", p2.stderr)

    def test_md5_verification_can_be_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "a.txt"
            dst = root / "b.txt"
            _write(src, b"hello")

            p = _run_cpd([str(src), str(dst)], env={"CPD_VERIFY_MD5": "0"})

            self.assertIn("复制后MD5校验: 关闭", p.stderr)
            self.assertNotIn("md5一致", p.stderr)

    def test_copy_file_to_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "a.txt"
            dst = root / "b.txt"
            _write(src, b"hello")

            _run_cpd([str(src), str(dst)])

            self.assertTrue(dst.exists())
            self.assertEqual(_read(dst), b"hello")

    def test_copy_dir_into_dir_nesting_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_dir = root / "src"
            dst_dir = root / "dst"
            _write(src_dir / "x.txt", b"x")
            dst_dir.mkdir(parents=True, exist_ok=True)

            _run_cpd([str(src_dir), str(dst_dir) + os.sep])

            self.assertTrue((dst_dir / "src" / "x.txt").exists())
            self.assertEqual(_read(dst_dir / "src" / "x.txt"), b"x")

    def test_copy_dir_contents_with_trailing_slash(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_dir = root / "src"
            dst_dir = root / "dst"
            _write(src_dir / "x.txt", b"x")
            dst_dir.mkdir(parents=True, exist_ok=True)

            _run_cpd([str(src_dir) + os.sep, str(dst_dir) + os.sep])

            self.assertTrue((dst_dir / "x.txt").exists())
            self.assertFalse((dst_dir / "src" / "x.txt").exists())
            self.assertEqual(_read(dst_dir / "x.txt"), b"x")

    def test_glob_multiple_sources_into_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_dir = root / "src"
            dst_dir = root / "dst"
            _write(src_dir / "a.txt", b"a")
            _write(src_dir / "b.txt", b"b")

            _run_cpd([str(src_dir / "*"), str(dst_dir) + os.sep])

            self.assertEqual(_read(dst_dir / "a.txt"), b"a")
            self.assertEqual(_read(dst_dir / "b.txt"), b"b")

    def test_shell_expanded_multiple_sources_into_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_dir = root / "src"
            dst_dir = root / "dst"
            _write(src_dir / "a.txt", b"a")
            _write(src_dir / "b.txt", b"b")

            _run_cpd([str(src_dir / "a.txt"), str(src_dir / "b.txt"), str(dst_dir) + os.sep])

            self.assertEqual(_read(dst_dir / "a.txt"), b"a")
            self.assertEqual(_read(dst_dir / "b.txt"), b"b")

    def test_hidden_files_are_copied_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_dir = root / "src"
            dst_dir1 = root / "dst1"
            dst_dir2 = root / "dst2"
            _write(src_dir / "a.txt", b"a")
            _write(src_dir / "b.txt", b"b")
            _write(src_dir / ".hidden", b"h")

            _run_cpd([str(src_dir / "*"), str(dst_dir1) + os.sep])
            self.assertEqual(_read(dst_dir1 / "a.txt"), b"a")
            self.assertEqual(_read(dst_dir1 / "b.txt"), b"b")
            self.assertEqual(_read(dst_dir1 / ".hidden"), b"h")

            _run_cpd([str(src_dir / "a.txt"), str(src_dir / "b.txt"), str(dst_dir2) + os.sep])
            self.assertEqual(_read(dst_dir2 / "a.txt"), b"a")
            self.assertEqual(_read(dst_dir2 / "b.txt"), b"b")
            self.assertEqual(_read(dst_dir2 / ".hidden"), b"h")

    def test_hidden_files_can_be_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_dir = root / "src"
            dst_dir = root / "dst"
            _write(src_dir / "a.txt", b"a")
            _write(src_dir / ".hidden", b"h")

            _run_cpd([str(src_dir / "*"), str(dst_dir) + os.sep], env={"CPD_INCLUDE_HIDDEN": "0"})

            self.assertEqual(_read(dst_dir / "a.txt"), b"a")
            self.assertFalse((dst_dir / ".hidden").exists())

    def test_does_not_delete_extra_files_in_dest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_dir = root / "src"
            dst_dir = root / "dst"
            _write(src_dir / "a.txt", b"a")
            _write(dst_dir / "extra.txt", b"keep")

            _run_cpd([str(src_dir) + os.sep, str(dst_dir) + os.sep])

            self.assertEqual(_read(dst_dir / "a.txt"), b"a")
            self.assertEqual(_read(dst_dir / "extra.txt"), b"keep")

    def test_force_mode_deletes_extra_files_and_dirs_in_dest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_dir = root / "src"
            dst_dir = root / "dst"
            _write(src_dir / "a.txt", b"a")
            _write(dst_dir / "extra.txt", b"delete-me")
            _write(dst_dir / "extra_dir" / "x.txt", b"x")

            _run_cpd(["-f", str(src_dir) + os.sep, str(dst_dir) + os.sep])

            self.assertEqual(_read(dst_dir / "a.txt"), b"a")
            self.assertFalse((dst_dir / "extra.txt").exists())
            self.assertFalse((dst_dir / "extra_dir").exists())

    def test_force_mode_only_cleans_the_computed_sync_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_dir = root / "src"
            dst_dir = root / "dst"
            _write(src_dir / "a.txt", b"a")
            _write(dst_dir / "keep_root.txt", b"keep")
            _write(dst_dir / "src" / "old.txt", b"old")

            _run_cpd(["-f", str(src_dir), str(dst_dir) + os.sep])

            self.assertEqual(_read(dst_dir / "keep_root.txt"), b"keep")
            self.assertEqual(_read(dst_dir / "src" / "a.txt"), b"a")
            self.assertFalse((dst_dir / "src" / "old.txt").exists())

    def test_force_mode_rejects_non_dir_source(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "a.txt"
            dst_dir = root / "dst"
            _write(src, b"hello")

            with self.assertRaises(subprocess.CalledProcessError):
                _run_cpd(["-f", str(src), str(dst_dir) + os.sep])

    def test_force_mode_supports_glob_dir_star_contents(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_dir = root / "src"
            dst_dir = root / "dst"
            _write(src_dir / "a.txt", b"a")
            _write(src_dir / "sub" / "b.txt", b"b")
            _write(dst_dir / "extra.txt", b"delete-me")

            _run_cpd(["-f", str(src_dir / "*"), str(dst_dir) + os.sep])

            self.assertEqual(_read(dst_dir / "a.txt"), b"a")
            self.assertEqual(_read(dst_dir / "sub" / "b.txt"), b"b")
            self.assertFalse((dst_dir / "extra.txt").exists())

    def test_force_mode_rejects_subset_glob(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_dir = root / "src"
            dst_dir = root / "dst"
            _write(src_dir / "a.txt", b"a")
            _write(src_dir / "b.bin", b"b")
            _write(dst_dir / "b.bin", b"old")

            with self.assertRaises(subprocess.CalledProcessError):
                _run_cpd(["-f", str(src_dir / "*.txt"), str(dst_dir) + os.sep])

    def test_checksum_skips_copy_when_content_same_but_mtime_diff(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "a.txt"
            dst = root / "dst.txt"
            _write(src, b"same")

            _run_cpd([str(src), str(dst)])

            original_mtime_ns = dst.stat().st_mtime_ns
            os.utime(dst, ns=(dst.stat().st_atime_ns, original_mtime_ns + 10_000_000))
            bumped_mtime_ns = dst.stat().st_mtime_ns
            self.assertNotEqual(original_mtime_ns, bumped_mtime_ns)

            _run_cpd([str(src), str(dst)], env={"CPD_CHECKSUM": "1"})

            self.assertEqual(dst.stat().st_mtime_ns, bumped_mtime_ns)
            self.assertEqual(_read(dst), b"same")


if __name__ == "__main__":
    unittest.main()
