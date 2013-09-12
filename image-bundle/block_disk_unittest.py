#!/usr/bin/python
# Copyright 2013 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unittest for block_disk.py module."""


__pychecker__ = 'no-local'  # for unittest


import logging
import os
import random
import subprocess
import tempfile
import unittest

import block_disk
import exclude_spec
import image_bundle_test_base
import utils


class FsRawDiskTest(image_bundle_test_base.ImageBundleTest):
  """FsRawDisk Unit Test."""

  _MEGABYTE = 1024*1024
  _GIGABYTE = 1024*_MEGABYTE

  def setUp(self):
    super(FsRawDiskTest, self).setUp()
    self._fs_size = 10* FsRawDiskTest._MEGABYTE
    self._bundle = block_disk.FsRawDisk(self._fs_size)
    self._tar_path = self.tmp_path + '/image.tar.gz'
    self._bundle.SetTarfile(self._tar_path)
    self._bundle.AppendExcludes([exclude_spec.ExcludeSpec(self._tar_path)])
    self._bundle.SetKey('key')

  def _SetupMbrDisk(self, partition_start, partition_end, fs_uuid):
    """Creates a disk with a fake MBR.

    Args:
      partition_start: The byte offset where the partition starts.
      partition_end: The byte offset where the partition ends.
      fs_uuid: The UUID of the filesystem to create on the partition.

    Returns:
      The path where the disk is located.
    """
    # Create the disk file with the size specified.
    disk_path = os.path.join(self.tmp_root, 'mbrdisk.raw')
    disk_size = partition_end + FsRawDiskTest._MEGABYTE
    with open(disk_path, 'wb') as disk_file:
      disk_file.truncate(disk_size)

    # Create a partition table
    utils.MakePartitionTable(disk_path)

    # Create the partition
    utils.MakePartition(disk_path, 'primary', 'ext2',
                        partition_start, partition_end)

    # Create the file system
    with utils.LoadDiskImage(disk_path) as devices:
      utils.MakeFileSystem(devices[0], 'ext4', fs_uuid)

    # Write some data after the MBR but before the first partition
    with open(disk_path, 'r+b') as disk_file:
      # Seek to last two bytes of first sector
      disk_file.seek(510)
      # Write MBR signature
      disk_file.write(chr(0x55))
      disk_file.write(chr(0xAA))
      # Write random data on the disk till the point first partition starts
      for _ in range(partition_start - 512):
        # Write a byte
        disk_file.write(chr(random.randint(0, 127)))

    return disk_path

  def tearDown(self):
    super(FsRawDiskTest, self).tearDown()

  def testDiskBundle(self):
    """Tests bundle command when a disk is specified.

    Creates a 20Gb source disk to start with and verifies that creating
    a 10MB file off it works.
    """
    # Create a 20GB disk with first partition starting at 1MB
    self._TestDiskBundleHelper(FsRawDiskTest._MEGABYTE,
                               FsRawDiskTest._GIGABYTE*20,
                               utils.RunCommand(['uuidgen']).strip())

  def testDiskBundlePartitionAt2MB(self):
    """Tests bundle command when a disk is specified.

    Creates the first partition at 2MB and verifies all data prior to that is
    copied.
    """
    # Create a 20GB disk with first partition starting at 2MB
    self._TestDiskBundleHelper(FsRawDiskTest._MEGABYTE*2,
                               FsRawDiskTest._GIGABYTE*20,
                               utils.RunCommand(['uuidgen']).strip())

  def _TestDiskBundleHelper(self, partition_start, partition_end, fs_uuid):
    disk_path = self._SetupMbrDisk(partition_start, partition_end, fs_uuid)

    with utils.LoadDiskImage(disk_path) as devices:
      # Get the path to do the disk.
      # devices will have something which is like /dev/mapper/loop0p1
      # We need to get loop0 out of it.
      disk_loop_back_path = '/dev/' + devices[0].split('/')[3][:-2]

      # Create a symlinks to the disk and loopback paths
      # This is required because of the code where we assume first
      # partition is device path appended by 1. Will remove it once we
      # update that part of the code.
      symlink_disk = os.path.join(self.tmp_root, 'disk')
      symlink_partition = self.tmp_root + '/disk1'
      utils.RunCommand(['ln', '-s', disk_loop_back_path, symlink_disk])
      utils.RunCommand(['ln', '-s', devices[0], symlink_partition])

      # Bundle up
      self._bundle.AddDisk(symlink_disk)
      self._bundle.AddSource(self.tmp_path)
      self._bundle.Verify()
      (_, _) = self._bundle.Bundleup()
    self._VerifyImageHas(self._tar_path,
                         ['lost+found', 'test1', 'test2', 'dir1/',
                          '/dir1/dir11/', '/dir1/sl1', '/dir1/hl2', 'dir2/',
                          '/dir2/dir1', '/dir2/sl2', '/dir2/hl1'])
    self._VerifyNumberOfHardLinksInRawDisk(self._tar_path, 'test1', 2)
    self._VerifyNumberOfHardLinksInRawDisk(self._tar_path, 'test2', 2)
    self._VerifyDiskSize(self._tar_path, self._fs_size)
    self._VerifyNonPartitionContents(self._tar_path,
                                     disk_path,
                                     partition_start)
    self._VerifyFilesystemUUID(self._tar_path, fs_uuid)

  def testRawDisk(self):
    """Tests the regular operation. No expected error."""
    self._bundle.AddSource(self.tmp_path)
    self._bundle.Verify()
    (_, digest) = self._bundle.Bundleup()
    if not digest:
      self.fail('raw disk failed')
    self._VerifyTarHas(self._tar_path, ['disk.raw'])
    self._VerifyImageHas(self._tar_path,
                         ['lost+found', 'test1', 'test2', 'dir1/',
                          '/dir1/dir11/', '/dir1/sl1', '/dir1/hl2', 'dir2/',
                          '/dir2/dir1', '/dir2/sl2', '/dir2/hl1'])
    self._VerifyNumberOfHardLinksInRawDisk(self._tar_path, 'test1', 2)
    self._VerifyNumberOfHardLinksInRawDisk(self._tar_path, 'test2', 2)

  def testRawDiskIgnoresHardlinks(self):
    """Tests if the raw disk ignores hard links if asked."""
    self._bundle.AddSource(self.tmp_path)
    self._bundle.IgnoreHardLinks()
    self._bundle.Verify()
    (_, digest) = self._bundle.Bundleup()
    if not digest:
      self.fail('raw disk failed')
    self._VerifyTarHas(self._tar_path, ['disk.raw'])
    self._VerifyImageHas(self._tar_path,
                         ['lost+found', 'test1', 'test2', 'dir1/',
                          '/dir1/dir11/', '/dir1/sl1', '/dir1/hl2', 'dir2/',
                          '/dir2/dir1', '/dir2/sl2', '/dir2/hl1'])
    self._VerifyNumberOfHardLinksInRawDisk(self._tar_path, 'test1', 1)
    self._VerifyNumberOfHardLinksInRawDisk(self._tar_path, 'test2', 1)

  def testRawDiskIgnoresExcludes(self):
    """Tests if the raw disk ignores specified excludes files."""
    self._bundle.AddSource(self.tmp_path)
    self._bundle.AppendExcludes(
        [exclude_spec.ExcludeSpec(self.tmp_path + '/dir1')])
    self._bundle.Verify()
    (_, digest) = self._bundle.Bundleup()
    if not digest:
      self.fail('raw disk failed')
    self._VerifyTarHas(self._tar_path, ['disk.raw'])
    self._VerifyImageHas(self._tar_path,
                         ['lost+found', 'test1', 'test2', 'dir2/', '/dir2/dir1',
                          '/dir2/sl2', '/dir2/hl1'])

  def testRawDiskExcludePreservesSubdirs(self):
    """Tests if excludes preserves subdirs underneath if asked."""
    self._bundle.AddSource(self.tmp_path)
    self._bundle.AppendExcludes(
        [exclude_spec.ExcludeSpec(self.tmp_path + '/dir1',
                                  preserve_dir=True,
                                  preserve_subdir=True)])
    self._bundle.Verify()
    (_, digest) = self._bundle.Bundleup()
    if not digest:
      self.fail('raw disk failed')
    self._VerifyTarHas(self._tar_path, ['disk.raw'])
    self._VerifyImageHas(self._tar_path,
                         ['lost+found', 'test1', 'test2', 'dir1/',
                          '/dir1/dir11', 'dir2/', '/dir2/dir1',
                          '/dir2/sl2', '/dir2/hl1'])

  def testRawDiskExcludePreservesFiles(self):
    """Tests if excludes preserves the files underneath if asked."""
    self._bundle.AddSource(self.tmp_path)
    self._bundle.AppendExcludes(
        [exclude_spec.ExcludeSpec(self.tmp_path + '/dir1',
                                  preserve_dir=True,
                                  preserve_file=True)])
    self._bundle.Verify()
    (_, digest) = self._bundle.Bundleup()
    if not digest:
      self.fail('raw disk failed')
    self._VerifyTarHas(self._tar_path, ['disk.raw'])
    self._VerifyImageHas(self._tar_path,
                         ['lost+found', 'test1', 'test2', 'dir1/', '/dir1/hl2',
                          '/dir1/sl1', 'dir2/', '/dir2/dir1', '/dir2/sl2',
                          '/dir2/hl1'])

  def testRawDiskUsesModifiedFiles(self):
    """Tests if the raw disk uses modified files."""
    self._bundle.AddSource(self.tmp_path)
    self._bundle.AppendExcludes(
        [exclude_spec.ExcludeSpec(self.tmp_path + '/dir1')])
    self._bundle.SetPlatform(image_bundle_test_base.MockPlatform(self.tmp_root))
    self._bundle.Verify()
    (_, digest) = self._bundle.Bundleup()
    if not digest:
      self.fail('raw disk failed')
    self._VerifyTarHas(self._tar_path, ['disk.raw'])
    self._VerifyImageHas(self._tar_path,
                         ['lost+found', 'test1', 'test2', 'dir2/',
                          '/dir2/dir1', '/dir2/sl2', '/dir2/hl1'])
    self._VerifyFileInRawDiskEndsWith(self._tar_path, 'test1',
                                      'something extra.')

  def testRawDiskGeneratesCorrectDigest(self):
    """Tests if the SHA1 digest generated is accurate."""
    self._bundle.AddSource(self.tmp_path)
    self._bundle.Verify()
    (_, digest) = self._bundle.Bundleup()
    if not digest:
      self.fail('raw disk failed')
    p = subprocess.Popen(['/usr/bin/openssl dgst -sha1 ' + self._tar_path],
                         stdout=subprocess.PIPE, shell=True)
    file_digest = p.communicate()[0].split('=')[1].strip()
    self.assertEqual(digest, file_digest)

  def testRawDiskHonorsRecursiveOff(self):
    """Tests if raw disk handles recursive off."""
    self._bundle.AppendExcludes([exclude_spec.ExcludeSpec(self._tar_path)])
    self._bundle.AddSource(self.tmp_path + '/dir1',
                           arcname='dir1', recursive=False)
    self._bundle.AddSource(self.tmp_path + '/dir2', arcname='dir2')
    self._bundle.Verify()
    (_, digest) = self._bundle.Bundleup()
    if not digest:
      self.fail('raw disk failed')
    self._VerifyTarHas(self._tar_path, ['disk.raw'])
    self._VerifyImageHas(self._tar_path,
                         ['lost+found', 'dir1/', 'dir2/', '/dir2/dir1',
                          '/dir2/sl2', '/dir2/hl1'])

  def _VerifyFilesystemUUID(self, tar, expected_uuid):
    """Verifies UUID of the first partition on disk matches the value."""
    tmp_dir = tempfile.mkdtemp(dir=self.tmp_root)
    tar_cmd = ['tar', '-xzf', tar, '-C', tmp_dir]
    self.assertEqual(subprocess.call(tar_cmd), 0)

    created_disk_path = os.path.join(tmp_dir, 'disk.raw')
    with utils.LoadDiskImage(created_disk_path) as devices:
      self.assertEqual(1, len(devices))
      self.assertEqual(expected_uuid, utils.GetUUID(devices[0]))

  def _VerifyNonPartitionContents(self, tar, disk_path, partition_start):
    """Verifies that bytes outside the partition are preserved."""
    tmp_dir = tempfile.mkdtemp(dir=self.tmp_root)
    tar_cmd = ['tar', '-xzf', tar, '-C', tmp_dir]
    self.assertEqual(subprocess.call(tar_cmd), 0)
    created_disk_path = os.path.join(tmp_dir, 'disk.raw')

    # Verify first parition in both disks starts at the same offset
    self.assertEqual(partition_start,
                     utils.GetPartitionStart(disk_path, 1))
    self.assertEqual(partition_start,
                     utils.GetPartitionStart(created_disk_path, 1))
    with open(disk_path, 'r') as source_file:
      with open(created_disk_path, 'r') as created_file:
        # Seek to 510'th byte in both streams and verify rest of the
        # bytes until the partition start are the same
        source_file.seek(510)
        created_file.seek(510)
        for i in range(partition_start - 510):
          self.assertEqual(source_file.read(1),
                           created_file.read(1),
                           'byte at position %s not equal' % (i + 510))

  def _VerifyDiskSize(self, tar, expected_size):
    """Verifies that the disk file has the same size as expected."""
    tmp_dir = tempfile.mkdtemp(dir=self.tmp_root)
    tar_cmd = ['tar', '-xzf', tar, '-C', tmp_dir]
    self.assertEqual(subprocess.call(tar_cmd), 0)
    disk_path = os.path.join(tmp_dir, 'disk.raw')
    statinfo = os.stat(disk_path)
    self.assertEqual(expected_size, statinfo.st_size)

  def _VerifyImageHas(self, tar, expected):
    """Tests if raw disk contains an expected list of files/directories."""
    tmp_dir = tempfile.mkdtemp(dir=self.tmp_root)
    tar_cmd = ['tar', '-xzf', tar, '-C', tmp_dir]
    self.assertEqual(subprocess.call(tar_cmd), 0)
    disk_path = os.path.join(tmp_dir, 'disk.raw')
    with utils.LoadDiskImage(disk_path) as devices:
      self.assertEqual(len(devices), 1)
      mnt_dir = tempfile.mkdtemp(dir=self.tmp_root)
      with utils.MountFileSystem(devices[0], mnt_dir):
        found = []
        for root, dirs, files in os.walk(mnt_dir):
          root = root.replace(mnt_dir, '')
          for f in files:
            found.append(os.path.join(root, f))
          for d in dirs:
            found.append(os.path.join(root, d))
    self._AssertListEqual(expected, found)

  def _VerifyFileInRawDiskEndsWith(self, tar, filename, text):
    """Tests if a file on raw disk contains ends with a specified text."""
    tmp_dir = tempfile.mkdtemp(dir=self.tmp_root)
    tar_cmd = ['tar', '-xzf', tar, '-C', tmp_dir]
    self.assertEqual(subprocess.call(tar_cmd), 0)
    disk_path = os.path.join(tmp_dir, 'disk.raw')
    with utils.LoadDiskImage(disk_path) as devices:
      self.assertEqual(len(devices), 1)
      mnt_dir = tempfile.mkdtemp(dir=self.tmp_root)
      with utils.MountFileSystem(devices[0], mnt_dir):
        f = open(os.path.join(mnt_dir, filename), 'r')
        file_content = f.read()
        f.close()
        self.assertTrue(file_content.endswith(text))

  def _VerifyNumberOfHardLinksInRawDisk(self, tar, filename, count):
    """Tests if a file on raw disk has a specified number of hard links."""
    tmp_dir = tempfile.mkdtemp(dir=self.tmp_root)
    tar_cmd = ['tar', '-xzf', tar, '-C', tmp_dir]
    self.assertEqual(subprocess.call(tar_cmd), 0)
    disk_path = os.path.join(tmp_dir, 'disk.raw')
    with utils.LoadDiskImage(disk_path) as devices:
      self.assertEqual(len(devices), 1)
      mnt_dir = tempfile.mkdtemp(dir=self.tmp_root)
      with utils.MountFileSystem(devices[0], mnt_dir):
        self.assertEqual(os.stat(os.path.join(mnt_dir, filename)).st_nlink,
                         count)


class RootFsRawTest(image_bundle_test_base.ImageBundleTest):
  """RootFsRaw Unit Test."""

  def setUp(self):
    super(RootFsRawTest, self).setUp()
    self._bundle = block_disk.RootFsRaw(10*1024*1024)
    self._tar_path = self.tmp_path + '/image.tar.gz'
    self._bundle.SetTarfile(self._tar_path)
    self._bundle.AppendExcludes([exclude_spec.ExcludeSpec(self._tar_path)])

  def tearDown(self):
    super(RootFsRawTest, self).tearDown()

  def testRootRawDiskVerifiesOneSource(self):
    """Tests that only one root directory is allowed."""
    self._bundle.AddSource(self.tmp_path)
    self._bundle.AddSource(self.tmp_path + '/dir1')
    self._bundle.SetKey('key')
    try:
      self._bundle.Verify()
    except block_disk.InvalidRawDiskError:
      return
    self.fail()

  def testRootRawDiskVerifiesRootDestination(self):
    """Tests that destination directory must be /."""
    self._bundle.AddSource(self.tmp_path, arcname='/tmp')
    self._bundle.SetKey('key')
    try:
      self._bundle.Verify()
    except block_disk.InvalidRawDiskError:
      return
    self.fail()


def main():
  logging.basicConfig(level=logging.DEBUG)
  unittest.main()


if __name__ == '__main__':
  main()
