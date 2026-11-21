"""Generate mihome_pc/assets/mihome.ico from the app logo (no Pillow)."""
import sys

sys.path.insert(0, ".")

from mihome_pc import tray  # noqa: E402


def main():
    source = "mihome_pc/static/logo.png"
    target = "mihome_pc/assets/mihome.ico"
    with open(source, "rb") as handle:
        png = handle.read()
    icon = tray.ico_from_png(png)
    with open(target, "wb") as handle:
        handle.write(icon)
    print("wrote %s (%d bytes from %d px png)" % (target, len(icon), len(png)))


if __name__ == "__main__":
    main()
