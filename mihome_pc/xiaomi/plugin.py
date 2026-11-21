"""Download device plugin packages (MPK / RN bundle) from the Mi Home cloud.

Reverse-engineered from ``DeviceRenderer.java:256-310`` (the caller) and the
duplicate check in ``mr2.java:194-208``: the request body is *nested*, and a flat
``{"model": ...}`` body makes the server answer all-null.

Shape that works (verified 2026-11-20 against a real account):

    POST /v2/plugin/fetch_plugin
    {
      "is_car_mihome": false,
      "latest_req": {"app_type": 10119, "app_platform": "android", "region": "cn",
                     "plugins": [{"model": "minij.washer.v5"}], "package_type": ""},
      "backup_req": {"plugins": [{"model": "minij.washer.v5"}],
                     "api_level": 111, "app_platform": "android"}
    }

The server answers ``backup_info`` with ``plugin_id``, ``version``,
``package_name``, ``length``, ``type`` ("MPK") and a signed CDN
``download_url``.  ``plugin_id == 0`` means the model has **no plugin** and is
rendered by the generic spec UI instead -- that is the normal case for simple
devices (a light bulb, an air fryer).

Packages are Android-style zips: ``classes.dex`` + ``res/`` + ``assets/``; the
React Native code lives in ``assets/index.android.bundle`` (plain JavaScript,
2 MB+ for the washing machine).  Anything that needs firmware-specific UI rules
lives in there, not in the APK.
"""
import json
import os
import time
import zipfile

__all__ = [
    "APP_TYPE", "API_LEVEL",
    "fetch_plugin_info", "download_package", "bundle_text", "extract",
    "PLUGIN_BUNDLES", "ensure", "sync_all",
]

APP_TYPE = 10119          # e.l in DeviceRenderer.java:266
API_LEVEL = 111           # DeviceRenderer.java:290
CDN_HOSTS = ("iot-cdn0.io.mi.com", "iot-cdn1.io.mi.com")
PLUGIN_BUNDLES = ("assets/index.android.bundle", "assets/main.bundle")


def _request_body(model, platform="android", region="cn"):
    plugin = {"model": model}
    return {
        "is_car_mihome": False,
        "latest_req": {"app_type": APP_TYPE, "app_platform": platform,
                       "region": region, "plugins": [plugin], "package_type": ""},
        "backup_req": {"plugins": [plugin], "api_level": API_LEVEL,
                       "app_platform": platform},
    }


def fetch_plugin_info(client, model, platform="android", region="cn"):
    """``{"plugin_id", "version", "package_name", "download_url", ...}``.

    Returns ``None`` when the model has no plugin package at all.
    """
    payload = client.post("/v2/plugin/fetch_plugin",
                          _request_body(model, platform, region)) or {}
    for key in ("latest_info", "backup_info"):
        entries = payload.get(key) or []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("model") and entry.get("model") != model:
                continue
            if entry.get("plugin_id"):
                return entry
    return None


def download_package(info, dest_dir, filename=None):
    """Download ``info["download_url"]`` to ``dest_dir``; returns the path."""
    url = (info or {}).get("download_url") or (info or {}).get("safe_url")
    if not url:
        raise ValueError("plugin info has no download_url")
    import urllib.request                      # local: keeps import cost off the CLI
    name = filename or "%s.plugin" % (info.get("package_name") or info.get("model"))
    if not os.path.isdir(dest_dir):
        os.makedirs(dest_dir)
    path = os.path.join(dest_dir, name)
    urllib.request.urlretrieve(url, path)
    return path


def extract(package_path, dest_dir):
    """Unpack a plugin zip; returns the list of entry names."""
    if not os.path.isdir(dest_dir):
        os.makedirs(dest_dir)
    with zipfile.ZipFile(package_path) as archive:
        names = archive.namelist()
        archive.extractall(dest_dir)
    return names


def bundle_text(package_path):
    """Read the React Native bundle out of a plugin package, or ``None``.

    Plugins ship as Android packages, so the RN code is
    ``assets/index.android.bundle`` (plain JS in every package we checked).
    """
    with zipfile.ZipFile(package_path) as archive:
        for candidate in PLUGIN_BUNDLES:
            try:
                return archive.read(candidate).decode("utf-8", "ignore")
            except KeyError:
                continue
    return None


def search_rules(text, keywords=("\u4e0a\u9650", "\u65f6\u957f", "\u6e29\u5ea6")):
    """Very small helper: return the windows around each keyword."""
    found = []
    for keyword in keywords:
        start = 0
        while True:
            index = text.find(keyword, start)
            if index < 0:
                break
            window = text[max(0, index - 120):index + 120]
            found.append((keyword, " ".join(window.split())))
            start = index + 1
            if len(found) > 40:
                return found
    return found


def dump(info, path):
    """Write the plugin descriptor as JSON (handy for fixtures)."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(info, handle, ensure_ascii=False, indent=2)

def parse_package(folder, path):
    """Parse an already-downloaded ``package.zip`` into strings/texts files.

    Split out of ``ensure`` so a package that was cached before this feature
    existed can be backfilled without touching the network.
    """
    strings = []
    texts = {}
    try:
        from . import arsc
        strings = arsc.chinese_strings(path)
        # name -> text: one resource ships ~95 locale chunks, and
        # ``arsc.LOCALE_PREFERENCE`` is what keeps this Simplified
        texts = {name: text for name, text in arsc.package_texts(path).items()
                 if arsc.has_cjk(text)}
    except Exception:                  # noqa: BLE001 - parsing must not abort a sync
        return 0, 0
    if not os.path.isdir(folder):
        os.makedirs(folder)
    with open(os.path.join(folder, "strings.json"), "w", encoding="utf-8") as handle:
        json.dump(strings, handle, ensure_ascii=False, indent=1)
    with open(os.path.join(folder, "texts.json"), "w", encoding="utf-8") as handle:
        json.dump(texts, handle, ensure_ascii=False, indent=1)
    return len(strings), len(texts)


def _backfill_if_needed(cached, folder):
    """Parse a cached package that predates ``texts.json`` (no network)."""
    path = os.path.join(folder, "package.zip")
    if cached.get("text_count") or not os.path.isfile(path):
        return cached
    string_count, text_count = parse_package(folder, path)
    if not text_count and not string_count:
        return cached
    updated = dict(cached)
    updated["string_count"] = string_count
    updated["text_count"] = text_count
    try:
        with open(os.path.join(folder, "info.json"), "w", encoding="utf-8") as handle:
            json.dump(updated, handle, ensure_ascii=False, indent=1)
    except OSError:
        pass
    return updated


def ensure(client, model, root_dir, force=False):
    """Make sure the plugin for ``model`` is cached and parsed.

    Layout::

        <root>/<model>/info.json      descriptor (plugin_id == 0 -> no plugin)
        <root>/<model>/package.zip    the MPK (only when a plugin exists)
        <root>/<model>/strings.json   every Chinese string from resources.arsc
        <root>/<model>/texts.json     resource name -> 简体中文文案

    Returns a summary dict; a model without a plugin is a normal result.
    """
    safe = model.replace("/", "_")
    folder = os.path.join(root_dir, safe)
    info_path = os.path.join(folder, "info.json")
    if os.path.isfile(info_path) and not force:
        try:
            with open(info_path, encoding="utf-8") as handle:
                cached = json.load(handle)
            if cached.get("plugin_id"):
                cached = _backfill_if_needed(cached, folder)
            return {"model": model, "cached": True, "folder": folder,
                    "plugin_id": cached.get("plugin_id") or 0,
                    "strings": cached.get("string_count") or 0,
                    "texts": cached.get("text_count") or 0}
        except (OSError, ValueError):
            pass

    if not os.path.isdir(folder):
        os.makedirs(folder)
    info = fetch_plugin_info(client, model)
    summary = {"model": model, "cached": False, "folder": folder,
               "plugin_id": (info or {}).get("plugin_id") or 0, "strings": 0,
               "texts": 0}
    if info is None:
        with open(info_path, "w", encoding="utf-8") as handle:
            json.dump({"model": model, "plugin_id": 0}, handle, ensure_ascii=False)
        return summary

    if not (info.get("download_url") or info.get("safe_url")):
        # 有 plugin_id 但没下载地址：服务端不给这个型号发，如实记下即可
        info = dict(info)
        info["no_url"] = True
        info["string_count"] = 0
        with open(info_path, "w", encoding="utf-8") as handle:
            json.dump(info, handle, ensure_ascii=False, indent=1)
        summary["no_url"] = True
        return summary
    path = download_package(info, folder, filename="package.zip")
    summary["size"] = os.path.getsize(path)
    string_count, text_count = parse_package(folder, path)
    summary["strings"] = string_count
    summary["texts"] = text_count
    info = dict(info)
    info["string_count"] = string_count
    info["text_count"] = text_count
    info["fetched_at"] = int(time.time())
    with open(info_path, "w", encoding="utf-8") as handle:
        json.dump(info, handle, ensure_ascii=False, indent=1)
    return summary


def sync_all(client, models, root_dir, force=False):
    """Ensure every model in ``models``; returns one summary per model."""
    out = []
    for model in models:
        if not model:
            continue
        try:
            out.append(ensure(client, model, root_dir, force=force))
        except Exception as exc:            # noqa: BLE001
            out.append({"model": model, "error": str(exc)[:120]})
    return out


def missing_models(models, root_dir):
    """Models whose cached plugin has no ``texts.json`` yet."""
    wanted = []
    for model in models:
        if not model:
            continue
        folder = os.path.join(root_dir, model.replace("/", "_"))
        if not os.path.isfile(os.path.join(folder, "texts.json")):
            wanted.append(model)
    return wanted
