"""copy_member writes exactly what writestr would, without decoding stored members."""
import copy
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import audit_maps
from audit_maps import copy_member, open_entry

MEMBERS = {"Libs/a.xml": b"<a/>", "Libs/š/empty.xml": b"", "Objects/big.cgf": bytes(range(256)) * 40, "Libs/b.xml": b"second"}
CHANGES = {"Libs/b.xml": b"changed", "Libs/new.xml": b"added"}


def archive_bytes(members, compression=zipfile.ZIP_STORED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as out:
        for name, data in members.items():
            out.writestr(name, data)
    return buffer.getvalue()


def cryengine_archive(local_name):
    """An archive whose local header spells the first name differently, as CryEngine paks do."""
    data = archive_bytes({"Libs/a.xml": b"first", "Libs/b.xml": b"second"})
    start = data.index(b"Libs/a.xml")
    return data[:start] + local_name + data[start + len(local_name):]


def legacy(source, entry, destination):
    """What the rewrites did before copy_member: decode the member, then write it again."""
    with open_entry(source, entry) as stream:
        destination.writestr(copy.copy(entry), stream.read())


def rebuilt(data, replacements, copier, remove=lambda name: False):
    """The package rewrite loop with a chosen member copier; returns the new archive's bytes."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_STORED, allowZip64=True) as dest:
        for entry in source.infolist():
            if remove(entry.filename): continue
            if entry.filename in replacements: dest.writestr(entry, replacements[entry.filename])
            else: copier(source, entry, dest)
        for name in sorted(set(replacements) - set(source.namelist())):
            dest.writestr(name, replacements[name])
    return out.getvalue()


def frozen_clock():
    """New members are stamped with the current time; hold it still while comparing archives."""
    return patch("time.time", return_value=1_700_000_000.0)


class CopyMemberTests(unittest.TestCase):
    def test_stored_members_match_writestr_byte_for_byte(self):
        data = archive_bytes(MEMBERS)
        for limit in (zipfile.ZIP64_LIMIT, 1024):
            with self.subTest(zip64_limit=limit), frozen_clock(), patch.object(zipfile, "ZIP64_LIMIT", limit), patch.object(audit_maps, "COPY_CHUNK", 1000):
                fast, slow = rebuilt(data, CHANGES, copy_member), rebuilt(data, CHANGES, legacy)
                self.assertEqual(fast, slow)
                with zipfile.ZipFile(io.BytesIO(fast)) as result:
                    self.assertIsNone(result.testzip())
                    self.assertEqual(result.read("Objects/big.cgf"), MEMBERS["Objects/big.cgf"])
                    self.assertEqual(result.read("Libs/š/empty.xml"), b"")
                    self.assertEqual((result.read("Libs/b.xml"), result.read("Libs/new.xml")), (b"changed", b"added"))

    def test_removed_members_are_skipped(self):
        data = archive_bytes(MEMBERS)
        gone = lambda name: name == "Objects/big.cgf"
        with frozen_clock():
            self.assertEqual(rebuilt(data, {}, copy_member, gone), rebuilt(data, {}, legacy, gone))

    def test_cryengine_local_names_copy_and_normalize(self):
        data = cryengine_archive(b"Libs\\a.xml")
        with frozen_clock():
            fast = rebuilt(data, {}, copy_member)
            self.assertEqual(fast, rebuilt(data, {}, legacy))
        with zipfile.ZipFile(io.BytesIO(fast)) as result:
            self.assertEqual(result.read("Libs/a.xml"), b"first")

    def test_names_differing_beyond_separators_are_rejected(self):
        with zipfile.ZipFile(io.BytesIO(cryengine_archive(b"Libs\\c.xml"))) as source, zipfile.ZipFile(io.BytesIO(), "w") as dest:
            with self.assertRaisesRegex(ValueError, "disagree"):
                copy_member(source, source.getinfo("Libs/a.xml"), dest)

    def test_damaged_bytes_fail_the_crc_check(self):
        data = bytearray(archive_bytes(MEMBERS))
        data[data.index(b"second")] ^= 0xFF
        with zipfile.ZipFile(io.BytesIO(bytes(data))) as source, zipfile.ZipFile(io.BytesIO(), "w") as dest:
            with self.assertRaisesRegex(ValueError, "changed while copying"):
                copy_member(source, source.getinfo("Libs/b.xml"), dest)

    def test_compressed_members_are_decoded_and_written_again(self):
        data = archive_bytes(MEMBERS, zipfile.ZIP_DEFLATED)
        with frozen_clock():
            fast = rebuilt(data, {}, copy_member)
            self.assertEqual(fast, rebuilt(data, {}, legacy))
        with zipfile.ZipFile(io.BytesIO(fast)) as result:
            self.assertEqual(result.getinfo("Objects/big.cgf").compress_type, zipfile.ZIP_DEFLATED)
            self.assertEqual(result.read("Objects/big.cgf"), MEMBERS["Objects/big.cgf"])

    def test_the_source_directory_is_left_as_read(self):
        with zipfile.ZipFile(io.BytesIO(archive_bytes(MEMBERS))) as source, zipfile.ZipFile(io.BytesIO(), "w") as dest:
            entry = source.getinfo("Objects/big.cgf")
            before = (entry.header_offset, entry.CRC, entry.file_size, entry.compress_size, entry.flag_bits)
            copy_member(source, entry, dest)
            self.assertEqual((entry.header_offset, entry.CRC, entry.file_size, entry.compress_size, entry.flag_bits), before)
            with open_entry(source, entry) as stream:
                self.assertEqual(stream.read(), MEMBERS["Objects/big.cgf"])

    def test_package_rewrites_keep_their_archives_identical(self):
        from setup_package_upgrade import rewrite as upgrade_rewrite
        from build_horse_trader_test import rewrite as recipe_rewrite
        data = archive_bytes(MEMBERS)
        dropped = lambda name: name == "Libs/a.xml"
        with tempfile.TemporaryDirectory() as tmp, frozen_clock():
            package = Path(tmp)
            (package / "level.pak").write_bytes(data)
            upgrade_rewrite(package, "level.pak", CHANGES, remove=dropped)
            self.assertEqual((package / "level.pak").read_bytes(), rebuilt(data, CHANGES, legacy, dropped))
            (package / "source.pak").write_bytes(data)
            recipe_rewrite(package / "source.pak", package / "out" / "target.pak", CHANGES)
            expected = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(expected, "w", zipfile.ZIP_STORED, allowZip64=True) as dest:
                for entry in source.infolist():
                    if entry.filename in CHANGES: dest.writestr(entry, CHANGES[entry.filename])
                    else: legacy(source, entry, dest)
                for name, blob in CHANGES.items():
                    if name not in source.namelist(): dest.writestr(name, blob)
            self.assertEqual((package / "out" / "target.pak").read_bytes(), expected.getvalue())


if __name__ == "__main__":
    unittest.main()
