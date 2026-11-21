"""Copy the demo specs into the shipped package.

The UI runs in "demo mode" before the user logs in, which means the demo specs
must ship with the app -- otherwise a fresh install shows an empty device list
and looks broken.  They are tiny (a few tens of KB) and double as a smoke test
of the spec parser in the packaged build.

Source of truth stays ``tests/fixtures/specs`` (used by the unit tests); this
tool copies them into ``mihome_pc/assets/demo/``.

Usage: python tools/make_demo_assets.py
"""
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "tests", "fixtures", "specs")
DST = os.path.join(ROOT, "mihome_pc", "assets", "demo")

# (fixture stem, model) -- the same five devices the tests pin.
DEMO = [
    ("dishwasher_s2", "xiaomi.dishwasher.s2"),
    ("airconditioner_ma1", "xiaomi.aircondition.ma1"),
    ("washer_v5", "minij.washer.v5"),
    ("fryer_maf04", "careli.fryer.maf04"),
    ("health_pot_p1v2", "xiaomi.health_pot.p1v2"),
    ("television_fsv1", "xiaomi.tv.fsv1"),
]


def main():
    if not os.path.isdir(SRC):
        raise SystemExit("missing %s" % SRC)
    if not os.path.isdir(DST):
        os.makedirs(DST)

    total = 0
    for stem, _model in DEMO:
        for suffix in (".json", ".i18n.json"):
            source = os.path.join(SRC, stem + suffix)
            if not os.path.exists(source):
                print("skip %s (missing)" % (stem + suffix))
                continue
            target = os.path.join(DST, stem + suffix)
            shutil.copyfile(source, target)
            total += os.path.getsize(target)

    with open(os.path.join(DST, "MAP.json"), "w", encoding="utf-8") as handle:
        import json
        json.dump([{"stem": stem, "model": model} for stem, model in DEMO],
                  handle, ensure_ascii=False, indent=1)

    print("wrote %s: %d files, %.1f KB"
          % (DST, len(os.listdir(DST)), total / 1024.0))


if __name__ == "__main__":
    main()
