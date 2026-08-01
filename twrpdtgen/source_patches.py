#
# Copyright (C) 2026 The Android Open Source Project
#
# SPDX-License-Identifier: Apache-2.0
#
"""Source overlay selection for generated Unisoc TWRP device trees."""

from pathlib import Path
from typing import Tuple

from twrpdtgen import module_path
from twrpdtgen.sprd import SprdBuildProfile


# These changes keep TWRP's FBE/decryption path compatible with the supplied
# Unisoc vendor ramdisks and are needed for every generated Unisoc tree.
DECRYPTION_PATCHES = (
	"bootable/recovery/Android.mk",
	"bootable/recovery/data.cpp",
	"bootable/recovery/etc/init.rc",
	"bootable/recovery/partition.cpp",
	"bootable/recovery/partitionmanager.cpp",
	"system/core/fastboot/device/fastboot_device.cpp",
	"system/vold/KeyUtil.cpp",
	"system/vold/Keymaster.cpp",
	"system/vold/MetadataCrypt.cpp",
)

SC27XX_HAPTICS_PATCHES = (
	"bootable/recovery/minuitwrp/events.cpp",
	"bootable/recovery/minuitwrp/libminuitwrp_defaults.go",
	"vendor/twrp/config/BoardConfigSoong.mk",
)

LEGACY_DRM_PATCHES = (
	"bootable/recovery/minuitwrp/graphics_drm.cpp",
	"bootable/recovery/minuitwrp/libminuitwrp_defaults.go",
	"vendor/twrp/config/BoardConfigSoong.mk",
)


def source_patch_root() -> Path:
	"""Locate SourcePatches in a source checkout or installed package."""
	for candidate in (module_path.parent / "SourcePatches", module_path / "SourcePatches"):
		if candidate.is_dir():
			return candidate
	raise FileNotFoundError("Bundled SourcePatches directory was not found")


def selected_source_patches(profile: SprdBuildProfile) -> Tuple[str, ...]:
	"""Return the deduplicated overlay paths required by a build profile."""
	patches = list(DECRYPTION_PATCHES)
	if profile.uses_sc27xx_haptics:
		patches.extend(SC27XX_HAPTICS_PATCHES)
	if profile.needs_legacy_drm:
		patches.extend(LEGACY_DRM_PATCHES)
	return tuple(dict.fromkeys(patches))
