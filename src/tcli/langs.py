"""Language registry for the 33 languages supported by Hy-MT2.

Each language carries an English name (used in the English translation prompt)
and a Chinese name (used in the Chinese translation prompt). A generous set of
aliases — ISO codes plus common English and Chinese names — lets the user write
`t en ...`, `t english ...`, `t 英文 ...`, `t 中文 ...`, `t 普通话 ...` etc.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str
    en_name: str
    zh_name: str
    chinese_family: bool = False  # use the Chinese prompt template when targeting this


# (code, en_name, zh_name, aliases, chinese_family)
_TABLE = [
    ("zh", "Chinese", "中文", ["zh", "zh-cn", "zh_cn", "zh-hans", "cn", "chinese", "mandarin",
                                "简体", "简体中文", "中文简体", "普通话", "国语", "汉语", "华语"], True),
    ("zh-Hant", "Traditional Chinese", "繁体中文", ["zh-hant", "zh-tw", "zh-hk", "zht", "traditional",
                                                    "traditionalchinese", "繁体", "繁體", "繁体中文",
                                                    "繁中", "正体中文"], True),
    ("yue", "Cantonese", "粤语", ["yue", "cantonese", "粤语", "粵語", "广东话", "廣東話", "白话"], True),
    ("en", "English", "英语", ["en", "eng", "english", "英文", "英语"], False),
    ("fr", "French", "法语", ["fr", "fra", "french", "法语", "法文"], False),
    ("pt", "Portuguese", "葡萄牙语", ["pt", "por", "portuguese", "葡萄牙语", "葡语"], False),
    ("es", "Spanish", "西班牙语", ["es", "spa", "spanish", "西班牙语", "西语"], False),
    ("ja", "Japanese", "日语", ["ja", "jp", "jpn", "japanese", "日语", "日文", "日本语"], False),
    ("tr", "Turkish", "土耳其语", ["tr", "tur", "turkish", "土耳其语", "土耳其文"], False),
    ("ru", "Russian", "俄语", ["ru", "rus", "russian", "俄语", "俄文", "俄罗斯语"], False),
    ("ar", "Arabic", "阿拉伯语", ["ar", "ara", "arabic", "阿拉伯语", "阿语"], False),
    ("ko", "Korean", "韩语", ["ko", "kr", "kor", "korean", "韩语", "韩文", "朝鲜语"], False),
    ("th", "Thai", "泰语", ["th", "tha", "thai", "泰语", "泰文"], False),
    ("it", "Italian", "意大利语", ["it", "ita", "italian", "意大利语", "意语"], False),
    ("de", "German", "德语", ["de", "ger", "deu", "german", "德语", "德文"], False),
    ("vi", "Vietnamese", "越南语", ["vi", "vie", "vietnamese", "越南语", "越语"], False),
    ("ms", "Malay", "马来语", ["ms", "msa", "malay", "马来语", "马来西亚语"], False),
    ("id", "Indonesian", "印尼语", ["id", "ind", "indonesian", "印尼语", "印度尼西亚语"], False),
    ("tl", "Filipino", "菲律宾语", ["tl", "fil", "filipino", "tagalog", "菲律宾语", "他加禄语"], False),
    ("hi", "Hindi", "印地语", ["hi", "hin", "hindi", "印地语", "印度语"], False),
    ("pl", "Polish", "波兰语", ["pl", "pol", "polish", "波兰语", "波兰文"], False),
    ("cs", "Czech", "捷克语", ["cs", "ces", "czech", "捷克语", "捷克文"], False),
    ("nl", "Dutch", "荷兰语", ["nl", "nld", "dutch", "荷兰语", "荷兰文"], False),
    ("km", "Khmer", "高棉语", ["km", "khm", "khmer", "高棉语", "柬埔寨语"], False),
    ("my", "Burmese", "缅甸语", ["my", "mya", "burmese", "缅甸语", "缅语"], False),
    ("fa", "Persian", "波斯语", ["fa", "fas", "persian", "farsi", "波斯语", "法尔西语"], False),
    ("gu", "Gujarati", "古吉拉特语", ["gu", "guj", "gujarati", "古吉拉特语"], False),
    ("ur", "Urdu", "乌尔都语", ["ur", "urd", "urdu", "乌尔都语"], False),
    ("te", "Telugu", "泰卢固语", ["te", "tel", "telugu", "泰卢固语"], False),
    ("mr", "Marathi", "马拉地语", ["mr", "mar", "marathi", "马拉地语", "马拉提语"], False),
    ("he", "Hebrew", "希伯来语", ["he", "heb", "hebrew", "希伯来语", "希伯来文"], False),
    ("bn", "Bengali", "孟加拉语", ["bn", "ben", "bengali", "孟加拉语"], False),
    ("ta", "Tamil", "泰米尔语", ["ta", "tam", "tamil", "泰米尔语"], False),
    ("uk", "Ukrainian", "乌克兰语", ["uk", "ukr", "ukrainian", "乌克兰语"], False),
    ("bo", "Tibetan", "藏语", ["bo", "tib", "tibetan", "藏语", "藏文"], False),
    ("kk", "Kazakh", "哈萨克语", ["kk", "kaz", "kazakh", "哈萨克语"], False),
    ("mn", "Mongolian", "蒙古语", ["mn", "mon", "mongolian", "蒙古语", "蒙语"], False),
    ("ug", "Uyghur", "维吾尔语", ["ug", "uig", "uyghur", "uighur", "维吾尔语", "维语"], False),
]

LANGUAGES: dict[str, Language] = {}
ALIASES: dict[str, Language] = {}

for _code, _en, _zh, _aliases, _cf in _TABLE:
    _lang = Language(_code, _en, _zh, _cf)
    LANGUAGES[_code] = _lang
    for _a in [_code, _en, _zh, *_aliases]:
        ALIASES[_a.strip().lower()] = _lang


def resolve(token: str | None) -> Language | None:
    """Return the Language for an alias token, or None if not recognized."""
    if not token:
        return None
    return ALIASES.get(token.strip().lower())
