"""Built-in Chinese names for spec service/property identifiers.

Why this exists: the official i18n table (``/instance/v2/multiLanguage``) is
incomplete -- a Yeelight bulb ships 843 bytes of it, leaving "Switch Status" and
"Brightness" in English on a Chinese UI.  The spec *identifier* is stable across
models, so a small dictionary covers the common cases and the i18n table wins
whenever it actually has a translation.
"""
import re

__all__ = ["zh_name", "zh_value", "zh_action", "zh_status", "is_cjk"]

_CJK = re.compile("[\u4e00-\u9fff]")

NAME_ZH = {
    # device-information
    "manufacturer": "\u5382\u5546",
    "model": "\u578b\u53f7",
    "serial-number": "\u5e8f\u5217\u53f7",
    "firmware-revision": "\u56fa\u4ef6\u7248\u672c",
    "serial-no": "SN\u53f7",
    # common controls
    "on": "\u5f00\u5173",
    "status": "\u5de5\u4f5c\u72b6\u6001",
    "mode": "\u6a21\u5f0f",
    "fault": "\u6545\u969c",
    "brightness": "\u4eae\u5ea6",
    "color-temperature": "\u8272\u6e29",
    "target-temperature": "\u76ee\u6807\u6e29\u5ea6",
    "temperature": "\u6e29\u5ea6",
    "relative-humidity": "\u6e7f\u5ea6",
    "pm2.5-density": "PM2.5",
    "air-quality": "\u7a7a\u6c14\u8d28\u91cf",
    "left-time": "\u5269\u4f59\u65f6\u95f4",
    "fan-level": "\u98ce\u91cf\u6863\u4f4d",
    "horizontal-swing": "\u5de6\u53f3\u6446\u98ce",
    "vertical-swing": "\u4e0a\u4e0b\u6446\u98ce",
    "heat-level": "\u52a0\u70ed\u6863\u4f4d",
    "target-water-level": "\u6c34\u4f4d",
    "spin-speed": "\u8131\u6c34\u8f6c\u901f",
    "rinsh-times": "\u6f02\u6d17\u6b21\u6570",
    "drying-level": "\u5e72\u71e5\u7a0b\u5ea6",
    "physical-controls-locked": "\u7ae5\u9501",
    "current-physical-control-lock": "\u7ae5\u9501\u72b6\u6001",
    "alarm": "\u62a5\u8b66",
    "volume": "\u97f3\u91cf",
    "mute": "\u9759\u97f3",
    "power-consumption": "\u529f\u8017",
    "delay-off": "\u5ef6\u65f6\u5173\u706f",
    "smart-switch": "\u667a\u80fd\u5f00\u5173",
    "toggle": "\u5207\u6362",
    "color": "\u989c\u8272",
    "saturation": "\u9971\u548c\u5ea6",
    "heat": "\u52a0\u70ed",
    "dry": "\u9664\u6e7f",
    "auto": "\u81ea\u52a8",
    "sleep-mode": "\u7761\u7720\u6a21\u5f0f",
    "eco": "\u8282\u80fd",
    "heater": "\u8f85\u70ed",
    "dryer": "\u5e72\u71e5",
    "door-state": "\u4ed3\u95e8\u72b6\u6001",
    "water-softener-left-level": "\u8f6f\u6c34\u76d0\u5269\u4f59\u91cf",
    "recipe-id": "\u98df\u8c31",
    "auto-keep-warm": "\u81ea\u52a8\u4fdd\u6e29",
    "keep-warm-time": "\u4fdd\u6e29\u65f6\u95f4",
    "cook-time": "\u70f9\u996a\u65f6\u95f4",
    "appoint-time": "\u9884\u7ea6\u65f6\u95f4",
    "bright-scene": "\u4eae\u5ea6\u573a\u666f",
    "ct-scene": "\u8272\u6e29\u573a\u666f",
    "color-scene": "\u5f69\u8272\u573a\u666f",
    "delay-scene": "\u5ef6\u65f6\u573a\u666f",
    "bright-adjust": "\u4eae\u5ea6\u8c03\u8282",
    "ct-adjust": "\u8272\u6e29\u8c03\u8282",
}

# service identifiers that show up as section headers
SERVICE_ZH = {
    "light": "\u706f\u5149",
    "washer": "\u6d17\u8863\u673a",
    "dishwasher": "\u6d17\u7897\u673a",
    "air-conditioner": "\u7a7a\u8c03",
    "air-fryer": "\u7a7a\u6c14\u70b8\u9505",
    "heater": "\u53d6\u6696\u5668",
    "health-pot": "\u517b\u751f\u58f6",
    "fan-control": "\u98ce\u626c\u63a7\u5236",
    "device-information": "\u8bbe\u5907\u4fe1\u606f",
    "environment": "\u73af\u5883",
    "physical-controls-locked": "\u7ae5\u9501",
    "yeelight-scene": "\u9884\u8bbe\u573a\u666f",
    "custom-dishwasher": "\u6d17\u7897\u673a\u8bbe\u7f6e",
    "water-softener-management": "\u8f6f\u6c34\u76d0\u7ba1\u7406",
}


# Enumerated value labels.  Specs publish these in English and the official
# i18n table is often a stub (the washer ships 45 bytes: one English line), so
# this table is what actually turns "Daily Wash" into 日常洗 on a Chinese UI.
VALUE_ZH = {
    # washer programs
    "daily wash": "\u65e5\u5e38\u6d17",
    "quick wash": "\u5feb\u6d17",
    "delicate wash": "\u8f7b\u67d4\u6d17",
    "down coat": "\u7fbd\u7ed2\u670d",
    "heavy wash": "\u5927\u4ef6\u6d17",
    "user define": "\u81ea\u5b9a\u4e49",
    "rinse": "\u6f02\u6d17",
    "spin": "\u5355\u8131\u6c34",
    "cotton": "\u68c9\u9ebb",
    "synthetic": "\u5316\u7ea4",
    "shirt": "\u886c\u886b",
    "boiling": "\u716e\u6d17",
    "wool": "\u7f8a\u6bdb",
    "drum clean": "\u7b52\u81ea\u6d01",
    "dry air wash": "\u7a7a\u6c14\u6d17",
    "baby care": "\u5a74\u7ae5",
    "intensive": "\u5f3a\u529b\u6d17",
    "jacket": "\u5916\u5957",
    "underwear": "\u5185\u8863",
    "alltimemode": "\u5e38\u7528",
    # spin speed
    "no spin": "\u4e0d\u8131\u6c34",
    "pirmode": "\u611f\u5e94\u6a21\u5f0f",
    "intermittentmode": "\u95f4\u6b47\u6a21\u5f0f",
    "mode-change": "\u6a21\u5f0f\u5207\u6362",
    "water-out": "\u51fa\u6c34",
    "water-out-mode": "\u51fa\u6c34\u6a21\u5f0f",
    "common": "\u5e38\u7528",
    "battery-low": "\u7535\u91cf\u4e0d\u8db3",
    "cold": "\u51b7\u6c34",
    "warm": "\u6e29\u6c34",
    "hot": "\u70ed\u6c34",
    "room temperature": "\u5e38\u6e29",
    # water level
    "high": "\u9ad8",
    "middle": "\u4e2d",
    "low": "\u4f4e",
    # drying
    "moist": "\u5fae\u5e72",
    "normal": "\u6807\u51c6",
    "extra": "\u7279\u5e72",
    "none": "\u4e0d\u70d8\u5e72",
    # ac modes
    "cool": "\u5236\u51b7",
    "dry": "\u9664\u6e7f",
    "fan": "\u9001\u98ce",
    "heat": "\u5236\u70ed",
    "auto": "\u81ea\u52a8",
    # working states
    "off": "\u5173\u673a",
    "idle": "\u5f85\u673a",
    "busy": "\u8fd0\u884c\u4e2d",
    "delay": "\u9884\u7ea6",
    "pause": "\u6682\u505c",
    "paused": "\u5df2\u6682\u505c",
    "fault": "\u6545\u969c",
    "eoc": "\u7ed3\u675f",
    "completed": "\u5df2\u5b8c\u6210",
    "preservation": "\u4fdd\u6e29\u4e2d",
    # generic
    "default": "\u9ed8\u8ba4",
    "custom": "\u81ea\u5b9a\u4e49",
    "basic": "\u57fa\u7840",
    "both": "\u53cc\u5411",
    "cloud recommendation": "\u4e91\u7aef\u63a8\u8350",
    "automatic": "\u81ea\u52a8",
    "manual": "\u624b\u52a8",
    "on": "\u5f00",
    "open": "\u6253\u5f00",
    "closed": "\u5173\u95ed",
    "opened": "\u6253\u5f00",
    "unlocked": "\u672a\u9501\u5b9a",
    "locked": "\u5df2\u9501\u5b9a",
    "yes": "\u662f",
    "no": "\u5426",
    "true": "\u5f00",
    "false": "\u5173",
}


# Action (RPC) identifiers.  A washing machine exposes "Start Wash" in English
# even on a Chinese UI because the spec carries no translation for it.
ACTION_ZH = {
    "start-wash": "\u5f00\u59cb\u6d17\u6da4",
    "cancel-cooking": "\u505c\u6b62\u70f9\u996a",
    "start-cook": "\u5f00\u59cb\u70f9\u996a",
    "recipe-cooking": "\u98df\u8c31\u70f9\u996a",
    "pause": "\u6682\u505c",
    "play": "\u64ad\u653e",
    "resume": "\u7ee7\u7eed",
    "stop": "\u505c\u6b62",
    "turn-off": "\u5173\u673a",
    "turn-on": "\u5f00\u673a",
    "post": "\u6d88\u606f\u4f20\u9012",
    "start": "\u542f\u52a8",
    "cancel": "\u53d6\u6d88",
    "reset": "\u91cd\u7f6e",
    "continue": "\u7ee7\u7eed",
}


def zh_action(name, fallback=""):
    """Chinese label for an action identifier."""
    hit = ACTION_ZH.get((name or "").lower())
    if hit:
        return hit
    return fallback or name or ""


# Some English labels mean different things per device family.  A washing
# machine's "Dry" is 烘干, an air conditioner's "Dry" is 除湿.
VALUE_ZH_CONTEXT = {
    ("washer", "dry"): "\u70d8\u5e72",
    ("washer", "dryer"): "\u70d8\u5e72",
    ("washer", "wash dry"): "\u6d17+\u70d8",
    ("washer", "quick wash dry"): "\u5feb\u6d17\u70d8",
    ("dishwasher", "dry"): "\u70d8\u5e72",
    ("dryer", "dry"): "\u70d8\u5e72",
}


def zh_value(label, device_type=""):
    """Chinese label for an enumerated value, or the original text.

    ``device_type`` disambiguates labels like ``Dry`` (washer: 烘干,
    air-conditioner: 除湿).
    """
    if not label:
        return label
    key = " ".join(str(label).split()).lower()
    scoped = VALUE_ZH_CONTEXT.get(((device_type or "").lower(), key))
    if scoped:
        return scoped
    if key in VALUE_ZH:
        return VALUE_ZH[key]
    # "600rpm" -> "600 转/分"; "20 Celsius" -> "20°C"
    suffix = key[-3:]
    if suffix == "rpm" and key[:-3].isdigit():
        return "%s \u8f6c/\u5206" % key[:-3]
    if key.endswith(" celsius") and key[:-8].isdigit():
        return "%s\u00b0C" % key[:-8]
    return label


def zh_value_legacy(label):
    """Chinese label for an enumerated value, or the original text."""
    if not label:
        return label
    key = " ".join(str(label).split()).lower()
    if key in VALUE_ZH:
        return VALUE_ZH[key]
    # "600rpm" -> "600 转/分"; "20 Celsius" -> "20°C"
    suffix = key[-3:]
    if suffix == "rpm" and key[:-3].isdigit():
        return "%s \u8f6c/\u5206" % key[:-3]
    if key.endswith(" celsius") and key[:-8].isdigit():
        return "%s\u00b0C" % key[:-8]
    return label


# Home status popup categories.  Names come from StatusItemModule in the APK
# (NAME_FAULT / NAME_WORKING / NAME_SECURITY / NAME_ENV / NAME_CONSUMABLE ...);
# the server sends one module per (category, device type) pair.
STATUS_MODULE_ZH = {
    "fault": "\u6545\u969c",
    "working": "\u8fd0\u884c\u4e2d",
    "existing": "\u5f85\u673a",
    "security": "\u5b89\u9632",
    "env": "\u73af\u5883",
    "consumable": "\u8017\u6750",
    "speaker": "\u97f3\u7bb1",
    "speaker_exist": "\u97f3\u7bb1",
    "hmind": "\u667a\u80fd",
}

STATUS_TYPE_ZH = {
    "air-conditioner": "\u7a7a\u8c03",
    "washer": "\u6d17\u8863\u673a",
    "dishwasher": "\u6d17\u7897\u673a",
    "juicer": "\u7834\u58c1\u673a",
    "heater": "\u53d6\u6696\u5668",
    "air-fryer": "\u7a7a\u6c14\u70b8\u9505",
    "pressure-cooker": "\u7535\u538b\u529b\u9505",
    "health-pot": "\u517b\u751f\u58f6",
    "light": "\u706f\u5177",
    "curtain": "\u5e18\u5e03",
    "fan": "\u98ce\u6247",
    "humidifier": "\u52a0\u6e7f\u5668",
    "vacuum": "\u626b\u5730\u673a\u5668\u4eba",
    "pet-waterer": "\u5ba0\u7269\u996e\u6c34\u673a",
    "television": "\u7535\u89c6",
    "purifier": "\u51c0\u5316\u5668",
    "water-heater": "\u70ed\u6c34\u5668",
    "cooker": "\u7535\u996d\u7172",
    "kettle": "\u7535\u6c34\u58f6",
}


def zh_status(name, fallback=""):
    """Chinese label for a status module name."""
    hit = STATUS_MODULE_ZH.get((name or "").lower())
    return hit or fallback or name or ""


def zh_type(name, fallback=""):
    """Chinese label for a device type / category."""
    hit = STATUS_TYPE_ZH.get((name or "").lower())
    return hit or fallback or name or ""


def is_cjk(text):
    return bool(text and _CJK.search(text))


def zh_name(identifier, fallback="", service=False):
    """Chinese name for a spec identifier, or ``fallback`` untouched."""
    table = SERVICE_ZH if service else NAME_ZH
    hit = table.get((identifier or "").lower())
    if hit:
        return hit
    return fallback or identifier or ""
