"""Hard distribution boundaries shared by every packaging backend.

.gitignore is not a release guard: locally generated files can still be copied
by shutil.copytree.  These checks run both before and after package creation.
"""
import os
import shutil

RESTRICTED_NAMES = {
    "catalog.json",
    "catalog_categories.json",
    "card_layouts.json",
    "plugin_config_cn-zh_cn",
    "configdes.json",
    "resources.arsc",
    "classes.dex",
    "androidmanifest.xml",
}
RESTRICTED_DIRECTORIES = {"reverse", "jadx", "smali"}
RESTRICTED_SUFFIXES = (".apk", ".apks", ".xapk", ".aab")


def restricted_members(names):
    """Return normalized archive/path names that must not be distributed."""
    found = []
    for raw in names:
        name = raw.replace("\\", "/").lower().lstrip("./")
        parts = [part for part in name.split("/") if part]
        base = parts[-1] if parts else ""
        if (base in RESTRICTED_NAMES or base.endswith(RESTRICTED_SUFFIXES)
                or any(part in RESTRICTED_DIRECTORIES for part in parts[:-1])):
            found.append(raw)
    return sorted(found)


def assert_tree_clean(root):
    names = []
    for base, _dirs, files in os.walk(root):
        for name in files:
            names.append(os.path.relpath(os.path.join(base, name), root))
    found = restricted_members(names)
    if found:
        raise SystemExit("restricted distribution content in %s: %s" % (
            root, ", ".join(found)))


def copy_project_package(root, target):
    """Copy mihome_pc while enforcing the boundaries on source and result."""
    source = os.path.join(root, "mihome_pc")
    assert_tree_clean(source)
    if os.path.isdir(target):
        shutil.rmtree(target)

    def ignore(_directory, names):
        ignored = set(restricted_members(names))
        ignored.update(name for name in names
                       if name in RESTRICTED_DIRECTORIES
                       or name == "__pycache__"
                       or name.endswith((".pyc", ".pyo")))
        return ignored

    shutil.copytree(source, target, ignore=ignore)
    assert_tree_clean(target)


def copy_legal_documents(root, destination):
    """Ship the current license, historical MIT and third-party boundaries."""
    os.makedirs(destination, exist_ok=True)
    for source_name, target_name in (
            ("LICENSE", "LICENSE.txt"),
            ("THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md")):
        shutil.copyfile(os.path.join(root, source_name),
                        os.path.join(destination, target_name))
    licenses = os.path.join(destination, "LICENSES")
    os.makedirs(licenses, exist_ok=True)
    shutil.copyfile(os.path.join(root, "LICENSES", "MIT.txt"),
                    os.path.join(licenses, "MIT.txt"))
