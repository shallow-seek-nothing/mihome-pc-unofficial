"""Regenerate the hicolor PNG icons that the deb / tarball install.

Dev-side only: this one needs Pillow.  The shipped client itself is
stdlib-only -- the generated PNGs are committed under
``mihome_pc/assets/icons/`` and the build scripts just copy them, so the
package build keeps zero third-party dependencies.

    python packaging/make_icons.py

Source: ``mihome_pc/static/logo.png`` (256x256).
Output: ``mihome_pc/assets/icons/mihome-<size>.png`` for 48 / 128 / 256.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SIZES = (48, 128, 256)


def main():
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit(
            "Pillow is required to regenerate icons (pip install pillow);\n"
            "the committed PNGs in mihome_pc/assets/icons/ are enough to build")
    source = os.path.join(ROOT, "mihome_pc", "static", "logo.png")
    target_dir = os.path.join(ROOT, "mihome_pc", "assets", "icons")
    if not os.path.isfile(source):
        raise SystemExit("missing source image: %s" % source)
    if not os.path.isdir(target_dir):
        os.makedirs(target_dir)
    image = Image.open(source).convert("RGBA")
    print("source: %s (%dx%d)" % (source, image.width, image.height))
    for size in SIZES:
        resized = image.resize((size, size), Image.LANCZOS)
        path = os.path.join(target_dir, "mihome-%d.png" % size)
        resized.save(path, "PNG", optimize=True)
        print("  wrote %s (%d bytes)" % (path, os.path.getsize(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
