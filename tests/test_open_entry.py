"""open_entry reads local headers through the archive's own handle."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from audit_maps import open_entry


def cryengine_archive(local_name):
    """An archive whose local header spells the first name differently, as CryEngine paks do."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Libs/a.xml", b"first")
        archive.writestr("Libs/b.xml", b"second")
    data = buffer.getvalue()
    start = data.index(b"Libs/a.xml")  # the first occurrence is the local header
    return data[:start] + local_name + data[start + len(local_name):]


class OpenEntryTests(unittest.TestCase):
    def test_backslash_local_name_reads_through_open_archive(self):
        with zipfile.ZipFile(io.BytesIO(cryengine_archive(b"Libs\\a.xml"))) as archive:
            with self.assertRaises(zipfile.BadZipFile):
                archive.read("Libs/a.xml")
            for _ in range(3):
                with open_entry(archive, archive.getinfo("Libs/a.xml")) as stream:
                    self.assertEqual(stream.read(), b"first")
            with open_entry(archive, archive.getinfo("Libs/b.xml")) as stream:
                self.assertEqual(stream.read(), b"second")

    def test_names_differing_beyond_separators_are_rejected(self):
        with zipfile.ZipFile(io.BytesIO(cryengine_archive(b"Libs\\c.xml"))) as archive:
            with self.assertRaisesRegex(ValueError, "disagree"):
                open_entry(archive, archive.getinfo("Libs/a.xml"))

    def test_reading_does_not_move_an_appending_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "level.pak"
            path.write_bytes(cryengine_archive(b"Libs\\a.xml"))
            with zipfile.ZipFile(path, "a") as archive:
                with open_entry(archive, archive.getinfo("Libs/a.xml")) as stream:
                    self.assertEqual(stream.read(), b"first")
                archive.writestr("Libs/c.xml", b"third")
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(archive.namelist(), ["Libs/a.xml", "Libs/b.xml", "Libs/c.xml"])
                self.assertEqual(archive.read("Libs/c.xml"), b"third")
                with open_entry(archive, archive.getinfo("Libs/a.xml")) as stream:
                    self.assertEqual(stream.read(), b"first")


if __name__ == "__main__":
    unittest.main()
