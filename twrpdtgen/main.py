#
# Copyright (C) 2022 The Android Open Source Project
#
# SPDX-License-Identifier: Apache-2.0
#

from argparse import ArgumentParser
from pathlib import Path
from sebaubuntu_libs.liblogging import setup_logging
from twrpdtgen import __version__ as version, current_path
from twrpdtgen.device_tree import DeviceTree

def main():
	print(f"TWRP device tree generator\n"
	      f"Version {version}\n")

	parser = ArgumentParser(prog='python3 -m twrpdtgen')

	# Main DeviceTree arguments
	parser.add_argument("image", type=Path,
						help="path to a recovery, boot, or vendor_boot image")
	parser.add_argument("-o", "--output", type=Path, default=current_path / "output",
						help="custom output folder")
	parser.add_argument("--codename",
						help="override the codename read from stock properties")
	parser.add_argument("--manufacturer",
						help="override the manufacturer read from stock properties")

	# Optional DeviceTree arguments
	parser.add_argument("--git", action='store_true',
						help="create a git repo after the generation")

	# Logging
	parser.add_argument("-d", "--debug", action='store_true',
						help="enable debugging features")

	args = parser.parse_args()

	setup_logging(args.debug)

	device_tree = DeviceTree(image=args.image, codename=args.codename,
						 manufacturer=args.manufacturer)
	folder = device_tree.dump_to_folder(args.output, git=args.git)

	print(f"\nDone! You can find the device tree in {folder}")
