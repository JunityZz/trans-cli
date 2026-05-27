"""Build Hunyuan-MT translation prompts.

The model card defines two prompt templates: an English one (default) and a
Chinese one used when the target language is Chinese. Per the model card, the
target language name must be written in the same language as the template.
"""

from .langs import Language

EN_TEMPLATE = (
    "Translate the following text into {lang}. Note that you should only output "
    "the translated result without any additional explanation:\n\n{text}"
)
ZH_TEMPLATE = "将以下文本翻译为{lang}，注意只需要输出翻译后的结果，不要额外解释：\n\n{text}"


def build_prompt(target: Language, text: str) -> str:
    if target.chinese_family:
        return ZH_TEMPLATE.format(lang=target.zh_name, text=text)
    return EN_TEMPLATE.format(lang=target.en_name, text=text)
