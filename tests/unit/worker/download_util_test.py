import tests.unit.azure_blob_mock  # noqa: F401
from codalab.worker.download_util import get_target_info, BundleTarget
import unittest
import random
import tarfile
from apache_beam.io.filesystem import CompressionTypes
from apache_beam.io.filesystems import FileSystems
from io import BytesIO
import tempfile
from ratarmount import SQLiteIndexedTar
import shutil


class AzureBlobGetTargetInfoTest(unittest.TestCase):
    def test_single_file(self):
        """Test getting target info of a single file on Azure Blob Storage."""
        bundle_uuid = str(random.random())
        bundle_path = f"azfs://storageclwsdev0/bundles/{bundle_uuid}/contents"
        with FileSystems.create(bundle_path, compression_type=CompressionTypes.UNCOMPRESSED) as f:
            f.write(b"a")
        target_info = get_target_info(bundle_path, BundleTarget(bundle_uuid, None), 0)
        target_info.pop("resolved_target")
        self.assertEqual(target_info, {'name': bundle_uuid, 'type': 'file', 'size': 1, 'perm': 511})

    def test_nested_directories(self):
        """Test getting target info of different files within a bundle that consists of nested directories, on Azure Blob Storage."""
        bundle_uuid = str(random.random())
        bundle_path = f"azfs://storageclwsdev0/bundles/{bundle_uuid}/contents.tar.gz"

        def writestr(tf, name, contents):
            tinfo = tarfile.TarInfo(name)
            tinfo.size = len(contents)
            tf.addfile(tinfo, BytesIO(contents.encode()))

        def writedir(tf, name):
            tinfo = tarfile.TarInfo(name)
            tinfo.type = tarfile.DIRTYPE
            tf.addfile(tinfo, BytesIO())

        # TODO: Unify this code with code in UploadManager.upload_to_bundle_store().
        with FileSystems.create(
            bundle_path, compression_type=CompressionTypes.UNCOMPRESSED
        ) as out, tempfile.NamedTemporaryFile(
            suffix=".tar.gz"
        ) as tmp_tar_file, tempfile.NamedTemporaryFile(
            suffix=".sqlite"
        ) as tmp_index_file:
            with tarfile.open(name=tmp_tar_file.name, mode="w:gz") as tf:
                # We need to create separate entries for each directory, as a regular
                # .tar.gz file would have.
                writestr(tf, "./README.md", "hello world")
                writedir(tf, "./src")
                writestr(tf, "./src/test.sh", "echo hi")
                writedir(tf, "./dist")
                writedir(tf, "./dist/a")
                writedir(tf, "./dist/a/b")
                writestr(tf, "./dist/a/b/test2.sh", "echo two")
            shutil.copyfileobj(tmp_tar_file, out)
            with open(tmp_tar_file.name, "rb") as ttf:
                SQLiteIndexedTar(
                    fileObject=ttf,
                    tarFileName=bundle_uuid,
                    writeIndex=True,
                    clearIndexCache=True,
                    indexFileName=tmp_index_file.name,
                )
            with FileSystems.create(
                bundle_path.replace("/contents.tar.gz", "/index.sqlite"),
                compression_type=CompressionTypes.UNCOMPRESSED,
            ) as out_index_file, open(tmp_index_file.name, "rb") as tif:
                shutil.copyfileobj(tif, out_index_file)

        target_info = get_target_info(bundle_path, BundleTarget(bundle_uuid, None), 0)
        target_info.pop("resolved_target")
        self.assertEqual(
            target_info, {'name': bundle_uuid, 'type': 'directory', 'size': 249, 'perm': 511}
        )

        target_info = get_target_info(bundle_path, BundleTarget(bundle_uuid, None), 1)
        target_info.pop("resolved_target")
        self.assertEqual(
            target_info,
            {
                'name': bundle_uuid,
                'type': 'directory',
                'size': 249,
                'perm': 511,
                'contents': [
                    {'name': 'README.md', 'type': 'file', 'size': 11, 'perm': 420},
                    {'name': 'dist', 'type': 'directory', 'size': 0, 'perm': 420},
                    {'name': 'src', 'type': 'directory', 'size': 0, 'perm': 420},
                ],
            },
        )

        target_info = get_target_info(bundle_path, BundleTarget(bundle_uuid, "README.md"), 1)
        target_info.pop("resolved_target")
        self.assertEqual(
            target_info, {'name': 'README.md', 'type': 'file', 'size': 11, 'perm': 420}
        )

        target_info = get_target_info(bundle_path, BundleTarget(bundle_uuid, "src/test.sh"), 1)
        target_info.pop("resolved_target")
        self.assertEqual(target_info, {'name': 'test.sh', 'type': 'file', 'size': 7, 'perm': 420})

        target_info = get_target_info(
            bundle_path, BundleTarget(bundle_uuid, "dist/a/b/test2.sh"), 1
        )
        target_info.pop("resolved_target")
        self.assertEqual(target_info, {'name': 'test2.sh', 'type': 'file', 'size': 8, 'perm': 420})

        target_info = get_target_info(bundle_path, BundleTarget(bundle_uuid, "src"), 1)
        target_info.pop("resolved_target")
        self.assertEqual(
            target_info,
            {
                'name': 'src',
                'type': 'directory',
                'size': 0,
                'perm': 420,
                'contents': [{'name': 'test.sh', 'type': 'file', 'size': 7, 'perm': 420}],
            },
        )

        # Return all depths
        target_info = get_target_info(bundle_path, BundleTarget(bundle_uuid, "dist/a"), 999)
        target_info.pop("resolved_target")

        self.assertEqual(
            target_info,
            {
                'name': 'a',
                'size': 0,
                'perm': 420,
                'type': 'directory',
                'contents': [
                    {
                        'name': 'b',
                        'size': 0,
                        'perm': 420,
                        'type': 'directory',
                        'contents': [{'name': 'test2.sh', 'size': 8, 'perm': 420, 'type': 'file'}],
                    }
                ],
            },
        )