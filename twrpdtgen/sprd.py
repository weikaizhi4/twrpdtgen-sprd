#
# Copyright (C) 2026 The Android Open Source Project
#
# SPDX-License-Identifier: Apache-2.0
#
"""SPRD/Unisoc-specific build settings derived from stock properties."""

from dataclasses import dataclass
from re import search
from typing import Optional


def _first_prop(build_prop, *names: str) -> Optional[str]:
	for name in names:
		value = build_prop.get_prop(name)
		if value:
			return value
	return None


def _android_major(release: str) -> int:
	match = search(r"\d+", release)
	if not match:
		raise ValueError(f"cannot determine Android version from {release!r}")
	return int(match.group())


@dataclass(frozen=True)
class SprdBuildProfile:
	"""A TWRP source target selected from the factory vendor ramdisk."""
	android_release: str
	android_sdk: str
	security_patch: str
	vendor_security_patch: str
	shipping_api_level: str
	recovery_branch: str
	lunch_platform: str
	copy_stock_selinux: bool

	@property
	def lunch_suffix(self) -> str:
		return f"-{self.lunch_platform}" if self.lunch_platform else ""

	@classmethod
	def from_build_prop(cls, build_prop) -> "SprdBuildProfile":
		android_release = _first_prop(
			build_prop,
			"ro.system.build.version.release",
			"ro.build.version.release",
			"ro.vendor.build.version.release",
		)
		if android_release is None:
			raise ValueError("stock properties do not contain an Android release")

		android_sdk = _first_prop(
			build_prop,
			"ro.system.build.version.sdk",
			"ro.build.version.sdk",
			"ro.vendor.build.version.sdk",
		) or ""
		security_patch = _first_prop(
			build_prop,
			"ro.system.build.version.security_patch",
			"ro.build.version.security_patch",
		) or ""
		vendor_security_patch = _first_prop(
			build_prop,
			"ro.vendor.build.version.security_patch",
		) or security_patch
		shipping_api_level = _first_prop(
			build_prop,
			"ro.vendor.build.version.sdk",
			"ro.product.first_api_level",
			"ro.board.first_api_level",
			"ro.vendor.api_level",
		) or android_sdk

		major = _android_major(android_release)
		if major >= 14:
			return cls(
				android_release=android_release,
				android_sdk=android_sdk,
				security_patch=security_patch,
				vendor_security_patch=vendor_security_patch,
				shipping_api_level=shipping_api_level,
				recovery_branch="twrp-14.1",
				lunch_platform="ap2a",
				copy_stock_selinux=False,
			)
		return cls(
			android_release=android_release,
			android_sdk=android_sdk,
			security_patch=security_patch,
			vendor_security_patch=vendor_security_patch,
			shipping_api_level=shipping_api_level,
			recovery_branch="twrp-12.1",
			lunch_platform="",
			copy_stock_selinux=True,
		)
