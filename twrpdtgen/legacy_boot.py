#
# Copyright (C) 2026 The Android Open Source Project
#
# SPDX-License-Identifier: Apache-2.0
#
"""Read layout fields from traditional Android boot/recovery images."""

from dataclasses import dataclass
from pathlib import Path
from struct import unpack_from


BOOT_MAGIC = b"ANDROID!"
LEGACY_HEADER_MIN_SIZE = 1660


@dataclass(frozen=True)
class LegacyBootInfo:
	"""Header values needed to rebuild a v0-v2 recovery image."""
	image: Path
	header_version: int
	page_size: int
	kernel_addr: int
	ramdisk_addr: int
	tags_addr: int
	dtb_addr: int
	kernel_size: int
	ramdisk_size: int
	recovery_dtbo_size: int
	dtb_size: int
	cmdline: str

	@property
	def image_size(self) -> int:
		return self.image.stat().st_size

	def address_hex(self, value: int) -> str:
		return f"0x{value:08x}"

	@property
	def kernel_addr_hex(self) -> str:
		return self.address_hex(self.kernel_addr)

	@property
	def ramdisk_addr_hex(self) -> str:
		return self.address_hex(self.ramdisk_addr)

	@property
	def tags_addr_hex(self) -> str:
		return self.address_hex(self.tags_addr)

	@property
	def dtb_addr_hex(self) -> str:
		return self.address_hex(self.dtb_addr)


class LegacyBootImage:
	"""Parse the stable v0-v2 Android boot image header without unpacking it."""

	def __init__(self, image: Path):
		self.image = image
		self.info = self._read_header()

	@staticmethod
	def is_legacy_boot(image: Path) -> bool:
		with image.open("rb") as image_file:
			data = image_file.read(44)
		if len(data) < 44 or data[:len(BOOT_MAGIC)] != BOOT_MAGIC:
			return False
		return unpack_from("<I", data, 40)[0] <= 2

	def _read_header(self) -> LegacyBootInfo:
		data = self.image.read_bytes()[:LEGACY_HEADER_MIN_SIZE]
		if len(data) < LEGACY_HEADER_MIN_SIZE or data[:len(BOOT_MAGIC)] != BOOT_MAGIC:
			raise ValueError("not a traditional Android boot/recovery image")

		kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, _, _, tags_addr, page_size, \
			header_version, _ = unpack_from("<10I", data, 8)
		if header_version > 2:
			raise ValueError(f"unsupported traditional boot header version: {header_version}")
		if page_size == 0 or page_size & (page_size - 1):
			raise ValueError(f"invalid traditional boot page size: {page_size}")

		recovery_dtbo_size = unpack_from("<I", data, 1632)[0] if header_version >= 1 else 0
		dtb_size = unpack_from("<I", data, 1648)[0] if header_version >= 2 else 0
		dtb_addr = unpack_from("<Q", data, 1652)[0] if header_version >= 2 else 0
		cmdline = b" ".join(
			part.split(b"\0", 1)[0]
			for part in (data[64:576], data[608:1632])
			if part.split(b"\0", 1)[0]
		)

		return LegacyBootInfo(
			image=self.image,
			header_version=header_version,
			page_size=page_size,
			kernel_addr=kernel_addr,
			ramdisk_addr=ramdisk_addr,
			tags_addr=tags_addr,
			dtb_addr=dtb_addr,
			kernel_size=kernel_size,
			ramdisk_size=ramdisk_size,
			recovery_dtbo_size=recovery_dtbo_size,
			dtb_size=dtb_size,
			cmdline=cmdline.decode("utf-8", errors="replace"),
		)
