from aiogram.utils.keyboard import InlineKeyboardBuilder


def build_buttons(buttons: list | None):
    if not buttons:
        return None

    builder = InlineKeyboardBuilder()
    for item in buttons:
        text = item.get("text")
        url = item.get("url")
        if not text or not url:
            continue
        builder.button(text=text, url=url)

    builder.adjust(1)
    return builder.as_markup()
