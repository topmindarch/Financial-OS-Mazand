import asyncio
import logging
from decimal import Decimal
from io import BytesIO

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app.config import load_settings
from app.excel_import import make_template, parse_workbook
from app.keyboards import import_keyboard, main_menu, moderation_keyboard
from app.price_engine import calculate_price

settings = load_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("mazandasht")
bot = Bot(token=settings.bot_token)
dp = Dispatcher()
pool: asyncpg.Pool | None = None
pending_imports: dict[int, list[dict]] = {}


def is_admin(telegram_id: int) -> bool:
    return telegram_id in settings.admin_ids


def parse_side(value: str) -> str:
    value = value.strip().lower()
    if value in {"buy", "b", "خرید", "خري"}:
        return "buy"
    if value in {"sell", "s", "فروش", "فرو"}:
        return "sell"
    return ""


def side_fa(side: str) -> str:
    return "خرید" if side == "buy" else "فروش"


def fmt_price(value: int) -> str:
    return f"{value:,} ریال"


async def get_user(telegram_id: int):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM app_users WHERE telegram_id=$1", telegram_id)


async def can_see_prices(telegram_id: int, side: str):
    user = await get_user(telegram_id)
    if not user or user["status"] != "approved":
        return None, "حساب شما هنوز توسط مدیریت تأیید نشده است."
    if side == "buy" and not user["can_buy"]:
        return None, "دسترسی مشاهده قیمت خرید برای حساب شما فعال نیست."
    if side == "sell" and not user["can_sell"]:
        return None, "دسترسی مشاهده قیمت فروش برای حساب شما فعال نیست."
    group_id = user["buy_group_id"] if side == "buy" else user["sell_group_id"]
    if not group_id:
        return None, f"گروه قیمت {side_fa(side)} برای حساب شما تنظیم نشده است. با مدیریت تماس بگیرید."
    return (user, group_id), None


async def show_prices(message: Message, side: str):
    state, error = await can_see_prices(message.from_user.id, side)
    if error:
        await message.answer(error, reply_markup=main_menu())
        return
    user, group_id = state
    async with pool.acquire() as conn:
        group = await conn.fetchrow(
            "SELECT id, mode, factor, fixed_delta FROM price_groups WHERE id=$1 AND side=$2 AND active=TRUE",
            group_id, side,
        )
        if not group:
            await message.answer("گروه قیمت حساب شما معتبر یا فعال نیست؛ با مدیریت تماس بگیرید.")
            return
        rows = await conn.fetch(
            """
            SELECT p.name, bp.amount AS base_price, gp.direct_price
            FROM products p
            LEFT JOIN base_prices bp ON bp.product_id=p.id AND bp.side=$2
            LEFT JOIN group_prices gp ON gp.product_id=p.id AND gp.group_id=$1
            WHERE p.active=TRUE
            ORDER BY p.name
            """,
            group_id, side,
        )
    result = []
    for row in rows:
        price = calculate_price(
            group["mode"], row["base_price"], row["direct_price"],
            group["factor"], group["fixed_delta"],
        )
        if price is not None:
            result.append((row["name"], price))
    if not result:
        await message.answer(f"فعلاً قیمت قابل نمایش برای {side_fa(side)} ثبت نشده است.", reply_markup=main_menu())
        return
    # Internal group names and IDs are intentionally never exposed to end users.
    lines = [f"📌 فهرست قیمت {side_fa(side)}", "بر اساس شرایط همکاری ثبت‌شده برای حساب شما", ""]
    lines.extend(f"• {name}: {fmt_price(price)}" for name, price in result)
    await message.answer("\n".join(lines), reply_markup=main_menu())


@dp.message(CommandStart())
async def start(message: Message):
    tg = message.from_user
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_users (telegram_id, username, first_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (telegram_id) DO UPDATE SET
              username=EXCLUDED.username, first_name=EXCLUDED.first_name
            """,
            tg.id, tg.username, tg.first_name or "",
        )
        user = await conn.fetchrow("SELECT status FROM app_users WHERE telegram_id=$1", tg.id)
    if is_admin(tg.id):
        await message.answer("پنل مدیریتی مازندشت فعال است. دستور /admin_help را بزنید.", reply_markup=main_menu())
    elif user["status"] == "approved":
        await message.answer("به مازندشت | باغدشت خوش آمدید.", reply_markup=main_menu())
    elif user["status"] == "rejected":
        await message.answer("درخواست حساب شما تأیید نشده است. برای بررسی بیشتر با مدیریت تماس بگیرید.")
    else:
        await message.answer("درخواست شما ثبت شد و پس از بررسی مدیریت اطلاع‌رسانی می‌شود.")


@dp.message(Command("status"))
@dp.message(F.text == "وضعیت حساب")
async def status(message: Message):
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("برای ثبت‌نام ابتدا /start را بزنید.")
        return
    labels = {"pending": "در انتظار بررسی", "approved": "تأیید شده", "rejected": "تأیید نشده"}
    await message.answer(f"وضعیت حساب: {labels[user['status']]}", reply_markup=main_menu())


@dp.message(Command("buy"))
@dp.message(F.text == "قیمت خرید")
async def buy_prices(message: Message):
    await show_prices(message, "buy")


@dp.message(Command("sell"))
@dp.message(F.text == "قیمت فروش")
async def sell_prices(message: Message):
    await show_prices(message, "sell")


@dp.message(Command("help"))
@dp.message(F.text == "راهنمای قیمت‌ها")
async def help_user(message: Message):
    await message.answer(
        "برای دریافت قیمت، از دکمه «قیمت خرید» یا «قیمت فروش» استفاده کنید. "
        "نمایش قیمت منوط به تأیید حساب، فعال‌بودن مجوز مربوط و تخصیص گروه قیمت توسط مدیریت است.",
        reply_markup=main_menu(),
    )


@dp.message(Command("admin_help"))
async def admin_help(message: Message):
    if not is_admin(message.from_user.id):
        return
    text = (
        "دستورهای مدیریت:\n"
        "/pending — فهرست درخواست‌های در انتظار\n"
        "/groups — نمایش شناسه و نام گروه‌ها\n"
        "/products — فهرست محصولات\n"
        "/grant ID buy|sell|both|none — تنظیم مجوزها\n"
        "/assign ID buy|sell GROUP_ID — تخصیص گروه قیمت\n"
        "/settype ID regular|special — نوع حساب\n"
        "/newproduct NAME — افزودن محصول\n"
        "/newgroup buy|نام گروه|direct یا multiplier|ضریب|تعدیل ثابت — ساخت/تنظیم گروه\n"
        "/setbase نام محصول|buy|مبلغ — تنظیم قیمت پایه\n"
        "/setprice نام محصول|GROUP_ID|مبلغ — تنظیم قیمت مستقیم گروه\n"
        "/template — دریافت قالب Excel\n"
        "برای ورود Excel، فایل .xlsx را ارسال کنید؛ ابتدا پیش‌نمایش و سپس تأیید می‌شود."
    )
    await message.answer(text)


@dp.message(Command("pending"))
async def pending(message: Message):
    if not is_admin(message.from_user.id):
        return
    async with pool.acquire() as conn:
        users = await conn.fetch(
            "SELECT telegram_id, username, first_name, account_type, created_at FROM app_users WHERE status='pending' ORDER BY created_at LIMIT 30"
        )
    if not users:
        await message.answer("درخواست در انتظار وجود ندارد.")
        return
    for user in users:
        label = f"{user['first_name']} (@{user['username'] or 'بدون‌نام'}) — ID: {user['telegram_id']} — {user['account_type']}"
        await message.answer(label, reply_markup=moderation_keyboard(user["telegram_id"]))


@dp.callback_query(F.data.startswith("approve:"))
async def approve_user(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی مدیریت ندارید.", show_alert=True)
        return
    telegram_id = int(callback.data.split(":", 1)[1])
    async with pool.acquire() as conn:
        await conn.execute("UPDATE app_users SET status='approved' WHERE telegram_id=$1", telegram_id)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("حساب تأیید شد.")
    try:
        await bot.send_message(telegram_id, "حساب شما در مازندشت تأیید شد. مجوزها و گروه قیمت مطابق تنظیم مدیریت فعال می‌شوند.")
    except Exception:
        log.info("Could not notify approved user %s", telegram_id)


@dp.callback_query(F.data.startswith("reject:"))
async def reject_user(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی مدیریت ندارید.", show_alert=True)
        return
    telegram_id = int(callback.data.split(":", 1)[1])
    async with pool.acquire() as conn:
        await conn.execute("UPDATE app_users SET status='rejected', can_buy=FALSE, can_sell=FALSE WHERE telegram_id=$1", telegram_id)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("درخواست رد شد.")
    try:
        await bot.send_message(telegram_id, "درخواست عضویت شما در حال حاضر تأیید نشد. برای بررسی بیشتر با مدیریت تماس بگیرید.")
    except Exception:
        log.info("Could not notify rejected user %s", telegram_id)


@dp.message(Command("groups"))
async def groups(message: Message):
    if not is_admin(message.from_user.id):
        return
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name, side, mode, factor, fixed_delta, active FROM price_groups ORDER BY side, id")
    if not rows:
        await message.answer("گروهی تعریف نشده است.")
        return
    lines = ["گروه‌های داخلی (فقط برای مدیریت):"]
    for row in rows:
        lines.append(f"ID={row['id']} | {side_fa(row['side'])} | {row['name']} | {row['mode']} | ضریب={row['factor']} | تعدیل={row['fixed_delta']} | فعال={row['active']}")
    await message.answer("\n".join(lines))


@dp.message(Command("products"))
async def products(message: Message):
    if not is_admin(message.from_user.id):
        return
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name, active FROM products ORDER BY name")
    await message.answer("\n".join([f"{r['id']}: {r['name']} (فعال={r['active']})" for r in rows]) or "محصولی ثبت نشده است.")


@dp.message(Command("grant"))
async def grant(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("فرمت: /grant ID buy|sell|both|none")
        return
    try:
        telegram_id = int(parts[1])
    except ValueError:
        await message.answer("شناسه تلگرام باید عددی باشد.")
        return
    permission = parts[2].lower()
    mapping = {"buy": (True, False), "sell": (False, True), "both": (True, True), "none": (False, False)}
    if permission not in mapping:
        await message.answer("مقدار مجاز: buy، sell، both یا none")
        return
    buy, sell = mapping[permission]
    async with pool.acquire() as conn:
        result = await conn.execute("UPDATE app_users SET can_buy=$2, can_sell=$3 WHERE telegram_id=$1", telegram_id, buy, sell)
    await message.answer("مجوزها به‌روزرسانی شد." if not result.endswith(" 0") else "کاربر پیدا نشد؛ ابتدا باید /start را زده باشد.")


@dp.message(Command("assign"))
async def assign(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 4:
        await message.answer("فرمت: /assign ID buy|sell GROUP_ID")
        return
    try:
        telegram_id, group_id = int(parts[1]), int(parts[3])
    except ValueError:
        await message.answer("شناسه کاربر و گروه باید عددی باشند.")
        return
    side = parse_side(parts[2])
    if not side:
        await message.answer("جهت باید buy یا sell باشد.")
        return
    async with pool.acquire() as conn:
        group = await conn.fetchrow("SELECT id FROM price_groups WHERE id=$1 AND side=$2 AND active=TRUE", group_id, side)
        user = await conn.fetchrow("SELECT telegram_id FROM app_users WHERE telegram_id=$1", telegram_id)
        if not group or not user:
            await message.answer("کاربر یا گروه فعال متناظر با این جهت پیدا نشد.")
            return
        column = "buy_group_id" if side == "buy" else "sell_group_id"
        await conn.execute(f"UPDATE app_users SET {column}=$2 WHERE telegram_id=$1", telegram_id, group_id)
    await message.answer(f"گروه قیمت {side_fa(side)} برای کاربر تنظیم شد.")


@dp.message(Command("settype"))
async def set_type(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or parts[2].lower() not in {"regular", "special"}:
        await message.answer("فرمت: /settype ID regular|special")
        return
    try:
        telegram_id = int(parts[1])
    except ValueError:
        await message.answer("شناسه تلگرام باید عددی باشد.")
        return
    async with pool.acquire() as conn:
        result = await conn.execute("UPDATE app_users SET account_type=$2 WHERE telegram_id=$1", telegram_id, parts[2].lower())
    await message.answer("نوع حساب تغییر کرد." if not result.endswith(" 0") else "کاربر پیدا نشد.")


@dp.message(Command("newproduct"))
async def new_product(message: Message):
    if not is_admin(message.from_user.id):
        return
    name = (message.text or "").partition(" ")[2].strip()
    if not name:
        await message.answer("فرمت: /newproduct نام محصول")
        return
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO products(name) VALUES($1) ON CONFLICT(name) DO UPDATE SET active=TRUE", name)
    await message.answer("محصول ثبت یا فعال شد.")


@dp.message(Command("newgroup"))
async def new_group(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = [p.strip() for p in (message.text or "").partition(" ")[2].split("|")]
    if len(parts) != 5:
        await message.answer("فرمت: /newgroup buy|نام گروه|direct یا multiplier|ضریب|تعدیل ثابت")
        return
    side, name, mode = parse_side(parts[0]), parts[1], parts[2].lower()
    try:
        factor, fixed_delta = Decimal(parts[3]), int(parts[4].replace(",", ""))
    except Exception:
        await message.answer("ضریب باید عدد مثبت و تعدیل ثابت عدد صحیح باشد.")
        return
    if not side or not name or mode not in {"direct", "multiplier"} or factor <= 0:
        await message.answer("جهت یا نام یا روش یا ضریب معتبر نیست.")
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO price_groups(name,side,mode,factor,fixed_delta)
            VALUES($1,$2,$3,$4,$5)
            ON CONFLICT(side,name) DO UPDATE SET mode=EXCLUDED.mode,factor=EXCLUDED.factor,
                fixed_delta=EXCLUDED.fixed_delta,active=TRUE
            """,
            name, side, mode, factor, fixed_delta,
        )
        row = await conn.fetchrow("SELECT id FROM price_groups WHERE side=$1 AND name=$2", side, name)
    await message.answer(f"گروه تنظیم شد؛ شناسه داخلی: {row['id']}")


@dp.message(Command("setbase"))
async def set_base(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = [p.strip() for p in (message.text or "").partition(" ")[2].split("|")]
    if len(parts) != 3:
        await message.answer("فرمت: /setbase نام محصول|buy یا sell|مبلغ به ریال")
        return
    name, side = parts[0], parse_side(parts[1])
    try:
        amount = int(parts[2].replace(",", "").replace("٬", ""))
    except ValueError:
        amount = -1
    if not name or not side or amount < 0:
        await message.answer("نام، جهت یا مبلغ معتبر نیست.")
        return
    async with pool.acquire() as conn:
        async with conn.transaction():
            product = await conn.fetchrow("INSERT INTO products(name) VALUES($1) ON CONFLICT(name) DO UPDATE SET active=TRUE RETURNING id", name)
            await conn.execute(
                """
                INSERT INTO base_prices(product_id,side,amount) VALUES($1,$2,$3)
                ON CONFLICT(product_id,side) DO UPDATE SET amount=EXCLUDED.amount
                """, product["id"], side, amount,
            )
    await message.answer("قیمت پایه ذخیره شد؛ قیمت گروه‌های ضریبی هنگام نمایش با مقدار جدید محاسبه می‌شود.")


@dp.message(Command("setprice"))
async def set_price(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = [p.strip() for p in (message.text or "").partition(" ")[2].split("|")]
    if len(parts) != 3:
        await message.answer("فرمت: /setprice نام محصول|شناسه گروه|مبلغ به ریال")
        return
    name = parts[0]
    try:
        group_id, amount = int(parts[1]), int(parts[2].replace(",", "").replace("٬", ""))
    except ValueError:
        await message.answer("شناسه گروه و مبلغ باید عدد صحیح باشند.")
        return
    if not name or amount < 0:
        await message.answer("نام یا مبلغ معتبر نیست.")
        return
    async with pool.acquire() as conn:
        group = await conn.fetchrow("SELECT mode FROM price_groups WHERE id=$1 AND active=TRUE", group_id)
        if not group or group["mode"] != "direct":
            await message.answer("گروه پیدا نشد یا روش آن direct نیست؛ گروه ضریبی قیمت اختصاصی نمی‌گیرد.")
            return
        product = await conn.fetchrow("SELECT id FROM products WHERE name=$1", name)
        if not product:
            await message.answer("محصول پیدا نشد؛ ابتدا /newproduct را اجرا کنید.")
            return
        await conn.execute(
            """
            INSERT INTO group_prices(product_id,group_id,direct_price) VALUES($1,$2,$3)
            ON CONFLICT(product_id,group_id) DO UPDATE SET direct_price=EXCLUDED.direct_price
            """, product["id"], group_id, amount,
        )
    await message.answer("قیمت مستقیم گروه ذخیره شد.")


@dp.message(Command("template"))
async def excel_template(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer_document(
        BufferedInputFile(make_template().getvalue(), filename="mazandasht_price_template.xlsx"),
        caption="قالب ورود قیمت. ردیف‌های نمونه را با داده واقعی جایگزین یا حذف کنید.",
    )


async def apply_import(rows: list[dict]):
    async with pool.acquire() as conn:
        async with conn.transaction():
            for row in rows:
                product = await conn.fetchrow(
                    "INSERT INTO products(name) VALUES($1) ON CONFLICT(name) DO UPDATE SET active=TRUE RETURNING id",
                    row["product"],
                )
                if not row["group"]:
                    await conn.execute(
                        """
                        INSERT INTO base_prices(product_id,side,amount) VALUES($1,$2,$3)
                        ON CONFLICT(product_id,side) DO UPDATE SET amount=EXCLUDED.amount
                        """, product["id"], row["side"], row["price"],
                    )
                    continue
                group = await conn.fetchrow(
                    """
                    INSERT INTO price_groups(name,side,mode,factor,fixed_delta)
                    VALUES($1,$2,$3,$4,$5)
                    ON CONFLICT(side,name) DO UPDATE SET
                      mode=EXCLUDED.mode,factor=EXCLUDED.factor,fixed_delta=EXCLUDED.fixed_delta,active=TRUE
                    RETURNING id
                    """,
                    row["group"], row["side"], row["mode"], Decimal(str(row["factor"])), row["fixed_delta"],
                )
                direct_price = row["price"] if row["mode"] == "direct" else None
                await conn.execute(
                    """
                    INSERT INTO group_prices(product_id,group_id,direct_price) VALUES($1,$2,$3)
                    ON CONFLICT(product_id,group_id) DO UPDATE SET direct_price=EXCLUDED.direct_price
                    """, product["id"], group["id"], direct_price,
                )


@dp.message(F.document)
async def import_excel(message: Message):
    if not is_admin(message.from_user.id):
        return
    document = message.document
    if not document.file_name or not document.file_name.lower().endswith(".xlsx"):
        await message.answer("فقط فایل Excel با پسوند .xlsx پشتیبانی می‌شود.")
        return
    if document.file_size and document.file_size > 5 * 1024 * 1024:
        await message.answer("حجم فایل باید کمتر از ۵ مگابایت باشد.")
        return
    payload = BytesIO()
    await bot.download(document, destination=payload)
    rows, errors = parse_workbook(payload.getvalue())
    if errors:
        await message.answer("خطاهای فایل:\n" + "\n".join(errors[:20]) + ("\nموارد بیشتر در فایل وجود دارد." if len(errors) > 20 else ""))
    if not rows:
        await message.answer("هیچ ردیف معتبری برای ثبت باقی نمانده است.")
        return
    pending_imports[message.from_user.id] = rows
    preview = [f"پیش‌نمایش: {len(rows)} ردیف معتبر" + (f"، {len(errors)} خطا" if errors else "، بدون خطا")]
    for row in rows[:12]:
        group_label = row["group"] or "قیمت پایه"
        price_label = fmt_price(row["price"]) if row["price"] is not None else "محاسبه از پایه"
        preview.append(f"ردیف {row['excel_row']}: {row['product']} | {side_fa(row['side'])} | {group_label} | {row['mode'] or 'base'} | {price_label}")
    if len(rows) > 12:
        preview.append(f"… و {len(rows) - 12} ردیف دیگر")
    preview.append("ثبت فقط پس از فشردن «تأیید و ثبت» انجام می‌شود.")
    await message.answer("\n".join(preview), reply_markup=import_keyboard())


@dp.callback_query(F.data.startswith("import:"))
async def import_decision(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی مدیریت ندارید.", show_alert=True)
        return
    action = callback.data.split(":", 1)[1]
    rows = pending_imports.get(callback.from_user.id)
    if action == "cancel":
        pending_imports.pop(callback.from_user.id, None)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("ورود لغو شد.")
        return
    if not rows:
        await callback.answer("پیش‌نمایش منقضی شده؛ فایل را دوباره ارسال کنید.", show_alert=True)
        return
    try:
        await apply_import(rows)
    except Exception:
        log.exception("Excel import failed")
        await callback.answer("ثبت ناموفق بود؛ دیتابیس را بررسی کنید.", show_alert=True)
        return
    pending_imports.pop(callback.from_user.id, None)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"ورود Excel با موفقیت ثبت شد: {len(rows)} ردیف.")
    await callback.answer("ثبت شد.")


async def main():
    global pool
    if not settings.database_url:
        raise RuntimeError("Set DATABASE_URL in .env.")
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10, command_timeout=30)
    async with pool.acquire() as conn:
        await conn.execute("SELECT 1")
    log.info("Starting Mazandasht bot")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await pool.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
