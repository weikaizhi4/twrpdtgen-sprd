#
# Copyright (C) 2022 The Android Open Source Project
#
# SPDX-License-Identifier: Apache-2.0
#

from datetime import datetime
from git import Repo
from os import chmod
from pathlib import Path
from sebaubuntu_libs.libaik import AIKManager
from sebaubuntu_libs.libandroid.device_info import DeviceInfo
from sebaubuntu_libs.libandroid.fstab import Fstab
from sebaubuntu_libs.libandroid.props import BuildProp
from sebaubuntu_libs.liblogging import LOGD
from shutil import copyfile, copytree, rmtree
from stat import S_IRWXU, S_IRGRP, S_IROTH, S_IXGRP, S_IXOTH
from re import sub
from twrpdtgen import __version__ as version
from twrpdtgen.sprd import SprdBuildProfile
from twrpdtgen.source_patches import selected_source_patches, source_patch_root
from twrpdtgen.templates import render_template
from typing import List
from twrpdtgen.vendor_boot import VendorBootImage

BUILDPROP_LOCATIONS = [Path() / "default.prop",
                       Path() / "prop.default",]
BUILDPROP_LOCATIONS += [Path() / dir / "build.prop"
                        for dir in ["system", "vendor"]]
BUILDPROP_LOCATIONS += [Path() / dir / "etc" / "build.prop"
                        for dir in ["system", "vendor"]]

FSTAB_LOCATIONS = [Path() / "etc" / "recovery.fstab"]
FSTAB_LOCATIONS += [Path() / dir / "etc" / "recovery.fstab"
                    for dir in ["system", "vendor"]]

INIT_RC_LOCATIONS = [Path()]
INIT_RC_LOCATIONS += [Path() / dir / "etc" / "init"
                      for dir in ["system", "vendor"]]

class DeviceTree:
	"""
	A class representing a device tree

	It initialize a basic device tree structure
	and save the location of some important files
	"""
	def __init__(self, image: Path, codename: str = None,
				 manufacturer: str = None):
		"""Initialize the device tree class."""
		self.image = image
		self.aik_manager = None
		self.vendor_boot = None
		self.sprd_profile = None

		self.current_year = str(datetime.now().year)

		# Check if the image exists
		if not self.image.is_file():
			raise FileNotFoundError("Specified file doesn't exist")

		# vendor_boot must be handled separately: AIK exposes it as a generic
		# ramdisk and loses the v4 fragment table and bootconfig information.
		if VendorBootImage.is_vendor_boot(image):
			self.vendor_boot = VendorBootImage(image)
			self.image_info = self.vendor_boot.info
		else:
			self.aik_manager = AIKManager()
			self.image_info = self.aik_manager.unpackimg(image)

		assert self.image_info.ramdisk, "Ramdisk not found"

		LOGD("Getting device infos...")
		self.build_prop = BuildProp()
		for build_prop in [self.image_info.ramdisk / location for location in BUILDPROP_LOCATIONS]:
			if not build_prop.is_file():
				continue

			self.build_prop.import_props(build_prop)

		self.device_info = DeviceInfo(self.build_prop)
		if codename:
			self.device_info.codename = codename
		if manufacturer:
			self.device_info.manufacturer = manufacturer
		if self._is_sprd_platform():
			self.sprd_profile = SprdBuildProfile.from_build_prop(
				self.build_prop,
				ramdisk=self.image_info.ramdisk,
				platform=self.device_info.platform,
			)

		# Generate fstab
		fstab = None
		for fstab_location in [self.image_info.ramdisk / location for location in FSTAB_LOCATIONS]:
			if not fstab_location.is_file():
				continue

			LOGD(f"Generating fstab using {fstab} as reference...")
			fstab = Fstab(fstab_location)
			break

		if fstab is None:
			raise AssertionError("fstab not found")

		self.fstab = fstab

		# Search for init rc files
		self.init_rcs: List[Path] = []
		for init_rc_path in [self.image_info.ramdisk / location for location in INIT_RC_LOCATIONS]:
			if not init_rc_path.is_dir():
				continue

			self.init_rcs += [init_rc for init_rc in init_rc_path.iterdir()
			                  if init_rc.name.endswith(".rc") and init_rc.name != "init.rc"]

	def dump_to_folder(self, output_path: Path, git: bool = False) -> Path:
		if self.vendor_boot is not None:
			return self._dump_sprd_vendor_boot(output_path, git)

		device_tree_folder = output_path / self.device_info.manufacturer / self.device_info.codename
		prebuilt_path = device_tree_folder / "prebuilt"
		recovery_root_path = device_tree_folder / "recovery" / "root"

		LOGD("Creating device tree folders...")
		if device_tree_folder.is_dir():
			rmtree(device_tree_folder, ignore_errors=True)
		device_tree_folder.mkdir(parents=True)
		prebuilt_path.mkdir(parents=True)
		recovery_root_path.mkdir(parents=True)

		LOGD("Writing makefiles/blueprints")
		self._render_template(device_tree_folder, "Android.bp", comment_prefix="//")
		self._render_template(device_tree_folder, "Android.mk")
		if self.sprd_profile is not None:
			self._render_template(device_tree_folder, "sprd_AndroidProducts.mk",
				out_file="AndroidProducts.mk")
		else:
			self._render_template(device_tree_folder, "AndroidProducts.mk")
		self._render_template(device_tree_folder, "BoardConfig.mk")
		self._render_template(device_tree_folder, "device.mk")
		self._render_template(device_tree_folder, "extract-files.sh")
		if self.sprd_profile is not None:
			self._render_template(device_tree_folder, "sprd_product.mk",
				out_file=f"twrp_{self.device_info.codename}.mk")
		else:
			self._render_template(device_tree_folder, "omni_device.mk",
				out_file=f"omni_{self.device_info.codename}.mk")
		self._render_template(device_tree_folder, "README.md")
		self._render_template(device_tree_folder, "setup-makefiles.sh")
		if self.sprd_profile is not None:
			self._render_template(device_tree_folder, "sprd_vendorsetup.sh",
				out_file="vendorsetup.sh")
		else:
			self._render_template(device_tree_folder, "vendorsetup.sh")

		# Set permissions
		chmod(device_tree_folder / "extract-files.sh", S_IRWXU | S_IRGRP | S_IROTH)
		chmod(device_tree_folder / "setup-makefiles.sh", S_IRWXU | S_IRGRP | S_IROTH)

		LOGD("Copying kernel...")
		if self.image_info.kernel is not None:
			copyfile(self.image_info.kernel, prebuilt_path / "kernel")
		if self.image_info.dt is not None:
			copyfile(self.image_info.dt, prebuilt_path / "dt.img")
		if self.image_info.dtb is not None:
			copyfile(self.image_info.dtb, prebuilt_path / "dtb.img")
		if self.image_info.dtbo is not None:
			copyfile(self.image_info.dtbo, prebuilt_path / "dtbo.img")

		LOGD("Copying fstab...")
		(device_tree_folder / "recovery.fstab").write_text(self.fstab.format(twrp=True))

		LOGD("Copying init scripts...")
		for init_rc in self.init_rcs:
			copyfile(init_rc, recovery_root_path / init_rc.name, follow_symlinks=True)

		if self.sprd_profile is not None:
			self._copy_sprd_source_patches(prebuilt_path / "sourcecode")

		if git:
			self._initialize_git_repo(device_tree_folder)

		return device_tree_folder

	def _dump_sprd_vendor_boot(self, output_path: Path, git: bool) -> Path:
		"""Generate a vendor_boot TWRP device tree for Unisoc devices."""
		device_tree_folder = output_path / self.device_info.manufacturer / self.device_info.codename
		prebuilt_path = device_tree_folder / "prebuilt"
		recovery_root_path = device_tree_folder / "recovery" / "root"

		LOGD("Creating SPRD vendor_boot device tree folders...")
		if device_tree_folder.is_dir():
			rmtree(device_tree_folder, ignore_errors=True)
		prebuilt_path.mkdir(parents=True)
		recovery_root_path.mkdir(parents=True)

		LOGD("Writing SPRD vendor_boot makefiles")
		self._render_template(device_tree_folder, "Android.bp", comment_prefix="//")
		self._render_template(device_tree_folder, "Android.mk")
		self._render_template(device_tree_folder, "sprd_AndroidProducts.mk", out_file="AndroidProducts.mk")
		self._render_template(device_tree_folder, "sprd_BoardConfig.mk", out_file="BoardConfig.mk")
		self._render_template(device_tree_folder, "sprd_device.mk", out_file="device.mk")
		self._render_template(device_tree_folder, "sprd_product.mk",
			out_file=f"twrp_{self.device_info.codename}.mk")
		self._render_template(device_tree_folder, "sprd_system.prop", out_file="system.prop")
		self._render_template(device_tree_folder, "sprd_README.md", out_file="README.md")

		LOGD("Copying vendor_boot DTB and ramdisk payload")
		copyfile(self.vendor_boot.info.dtb, prebuilt_path / "dtb.img")
		self._copy_sprd_vendor_ramdisk(recovery_root_path)
		self._write_sprd_twrp_fstab(recovery_root_path)
		self._copy_sprd_source_patches(prebuilt_path / "sourcecode")

		if git:
			self._initialize_git_repo(device_tree_folder)

		return device_tree_folder

	def _copy_sprd_vendor_ramdisk(self, recovery_root_path: Path):
		"""Keep only the factory payload TWRP must retain in vendor_ramdisk."""
		stock_root = self.vendor_boot.info.ramdisk

		first_stage = stock_root / "first_stage_ramdisk"
		if first_stage.is_dir():
			for source in first_stage.rglob("*"):
				if not source.is_file() or not source.name.startswith("fstab."):
					continue
				destination = recovery_root_path / source.relative_to(stock_root)
				self._copy_fstab_without_avb(source, destination)

		modules = stock_root / "lib" / "modules"
		if modules.is_dir():
			copytree(modules, recovery_root_path / "lib" / "modules")

		for relative_path in ("system/etc/recovery.fstab", "system/etc/ueventd.rc"):
			source = stock_root / relative_path
			if source.is_file():
				destination = recovery_root_path / relative_path
				if source.name.endswith("fstab"):
					self._copy_fstab_without_avb(source, destination)
				else:
					self._copy_file(source, destination)

		# Android 13-and-earlier vendor ramdisks use the TWRP 12.1 stock policy
		# path. Android 14 and newer use TWRP 14.1's compatible policy instead.
		if self.sprd_profile.copy_stock_selinux:
			for source in stock_root.iterdir():
				if source.is_file() and (
					source.name == "sepolicy" or (
						source.name.endswith("_contexts") and
						source.name.startswith(("odm_", "plat_", "product_"))
					)
				):
					self._copy_file(source, recovery_root_path / source.name)
			init_common = stock_root / "init.recovery.common.rc"
			if init_common.is_file():
				self._copy_file(init_common, recovery_root_path / init_common.name)

	def _write_sprd_twrp_fstab(self, recovery_root_path: Path):
		"""Create the TWRP partition table and expose the vendor_boot slot."""
		contents = self.fstab.format(twrp=True)
		if "/vendor_boot" not in contents:
			contents += (
				"/vendor_boot         emmc      /dev/block/by-name/vendor_boot"
				"   flags=slotselect;backup=1;flashimg=1;display=Vendor Boot\n"
			)
		(recovery_root_path / "system" / "etc").mkdir(parents=True, exist_ok=True)
		(recovery_root_path / "system" / "etc" / "twrp.fstab").write_text(
			contents, encoding="utf-8"
		)

	def _copy_sprd_source_patches(self, sourcecode_path: Path):
		"""Package the source overlay needed to build this generated tree."""
		patch_root = source_patch_root(self.sprd_profile)
		files_path = sourcecode_path / "files"
		patches = selected_source_patches(self.sprd_profile)

		LOGD("Copying SPRD TWRP source overlay")
		for relative_path in patches:
			source = patch_root / relative_path
			if not source.is_file():
				raise FileNotFoundError(f"Bundled source patch is missing: {source}")
			self._copy_file(source, files_path / relative_path)

		manifest = "# Paths are relative to the TWRP source root.\n"
		manifest += "\n".join(patches) + "\n"
		sourcecode_path.mkdir(parents=True, exist_ok=True)
		(sourcecode_path / "source-files.txt").write_text(manifest, encoding="utf-8")
		self._render_template(sourcecode_path, "sprd_patch.sh", out_file="patch.sh")
		self._render_template(sourcecode_path, "sprd_recovery.sh", out_file="recovery.sh")
		mode = S_IRWXU | S_IRGRP | S_IROTH | S_IXGRP | S_IXOTH
		chmod(sourcecode_path / "patch.sh", mode)
		chmod(sourcecode_path / "recovery.sh", mode)

	def _is_sprd_platform(self) -> bool:
		"""Recognize Unisoc UMS/SC platforms in both boot image layouts."""
		platform = (self.device_info.platform or "").lower()
		return platform.startswith(("ums", "sc986", "sc983", "sp986", "sp7731"))

	@staticmethod
	def _copy_file(source: Path, destination: Path):
		destination.parent.mkdir(parents=True, exist_ok=True)
		copyfile(source, destination, follow_symlinks=True)

	def _copy_fstab_without_avb(self, source: Path, destination: Path):
		# Both supplied trees remove the stock AVB key restrictions so TWRP can
		# mount dynamic partitions after bootloader verification has completed.
		contents = source.read_text(encoding="utf-8")
		contents = sub(r",avb_keys=[^,\s]+", "", contents)
		contents = sub(r",avb(?:=[^,\s]+)?", "", contents)
		destination.parent.mkdir(parents=True, exist_ok=True)
		destination.write_text(contents, encoding="utf-8")

	def _initialize_git_repo(self, device_tree_folder: Path):
		"""Create the optional generated repository with the historic defaults."""
		LOGD("Creating git repo...")

		git_repo = Repo.init(device_tree_folder)
		git_config_reader = git_repo.config_reader()
		git_config_writer = git_repo.config_writer()

		try:
			git_global_email = git_config_reader.get_value('user', 'email')
			git_global_name = git_config_reader.get_value('user', 'name')
		except Exception:
			git_global_email, git_global_name = None, None

		if git_global_email is None or git_global_name is None:
			git_config_writer.set_value('user', 'email', 'barezzisebastiano@gmail.com')
			git_config_writer.set_value('user', 'name', 'Sebastiano Barezzi')

		git_repo.index.add(["*"])
		commit_message = self._render_template(None, "commit_message", to_file=False)
		git_repo.index.commit(commit_message)

	def _render_template(self, *args, comment_prefix: str = "#", **kwargs):
		return render_template(*args,
		                       comment_prefix=comment_prefix,
		                       current_year=self.current_year,
		                       device_info=self.device_info,
		                       fstab=self.fstab,
		                       image_info=self.image_info,
		                       sprd_profile=self.sprd_profile,
		                       vendor_boot=self.vendor_boot.info if self.vendor_boot else None,
		                       version=version,
		                       **kwargs)

	def cleanup(self):
		# Cleanup
		if self.vendor_boot is not None:
			self.vendor_boot.cleanup()
		elif self.aik_manager is not None:
			self.aik_manager.cleanup()
