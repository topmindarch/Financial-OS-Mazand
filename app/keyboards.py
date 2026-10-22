from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="قیمت خرید"), KeyboardButton(text="قیمت فروش")],[KeyboardButton(text="وضعیت حساب"), KeyboardButton(text="راهنمای قیمت‌ها")]], resize_keyboard=True)

def moderation_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="تأیید", callback_data=f"approve:{telegram_id}"),InlineKeyboardButton(text="رد", callback_data=f"reject:{telegram_id}")]])

def import_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="تأیید و ثبت", callback_data="import:confirm"),InlineKeyboardButton(text="لغو", callback_data="import:cancel")]])
