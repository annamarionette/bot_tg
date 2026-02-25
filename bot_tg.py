import asyncio
import logging
import re
from typing import Optional
from dataclasses import dataclass

import aiohttp
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from cachetools import TTLCache

# ══════════════════════════════════════════════════════════════════════════════
#                              КОНФИГУРАЦИЯ
# ══════════════════════════════════════════════════════════════════════════════

BOT_TOKEN = "8330328134:AAGddNy1kYjdVZ3_JX7HUS3V6m2gJSgKNu8"  # Получите у @BotFather

# API эндпоинты (бесплатные, без ключей)
COINGECKO_API = "https://api.coingecko.com/api/v3"
FRANKFURTER_API = "https://api.frankfurter.app"

# Фиатные валюты
FIAT = {
    "USD": ("Доллар США", "🇺🇸"),
    "EUR": ("Евро", "🇪🇺"),
    "RUB": ("Рубль", "🇷🇺"),
    "UAH": ("Гривна", "🇺🇦"),
    "KZT": ("Тенге", "🇰🇿"),
    "BYN": ("Бел. рубль", "🇧🇾"),
    "GBP": ("Фунт", "🇬🇧"),
    "CNY": ("Юань", "🇨🇳"),
    "TRY": ("Лира", "🇹🇷"),
    "GEL": ("Лари", "🇬🇪"),
    "PLN": ("Злотый", "🇵🇱"),
    "CHF": ("Франк", "🇨🇭"),
}

# Криптовалюты (код: (название, эмодзи, coingecko_id))
CRYPTO = {
    "BTC": ("Bitcoin", "₿", "bitcoin"),
    "ETH": ("Ethereum", "⟠", "ethereum"),
    "USDT": ("Tether", "💲", "tether"),
    "BNB": ("BNB", "🔶", "binancecoin"),
    "SOL": ("Solana", "◎", "solana"),
    "XRP": ("Ripple", "💧", "ripple"),
    "TON": ("Toncoin", "💎", "the-open-network"),
    "DOGE": ("Dogecoin", "🐕", "dogecoin"),
    "ADA": ("Cardano", "🔵", "cardano"),
    "TRX": ("TRON", "⚡", "tron"),
    "LTC": ("Litecoin", "Ł", "litecoin"),
    "MATIC": ("Polygon", "🟣", "matic-network"),
}

ALL_CURRENCIES = {**{k: (v[0], v[1]) for k, v in FIAT.items()},
                  **{k: (v[0], v[1]) for k, v in CRYPTO.items()}}

# ══════════════════════════════════════════════════════════════════════════════
#                              API СЕРВИС
# ══════════════════════════════════════════════════════════════════════════════

cache = TTLCache(maxsize=100, ttl=60)  # Кэш на 60 секунд


@dataclass
class ConversionResult:
    """Результат конвертации"""
    amount: float
    from_code: str
    to_code: str
    result: float
    rate: float
    from_usd: float
    to_usd: float


class CurrencyAPI:
    """Работа с API курсов валют"""

    @staticmethod
    async def _fetch(url: str) -> Optional[dict]:
        """HTTP GET запрос"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    return await r.json() if r.status == 200 else None
        except Exception as e:
            logging.error(f"API error: {e}")
            return None

    @staticmethod
    async def get_crypto_prices() -> dict[str, float]:
        """Получить цены крипты в USD"""
        if "crypto" in cache:
            return cache["crypto"]

        ids = ",".join(v[2] for v in CRYPTO.values())
        data = await CurrencyAPI._fetch(f"{COINGECKO_API}/simple/price?ids={ids}&vs_currencies=usd")

        if data:
            result = {code: data[info[2]]["usd"] for code, info in CRYPTO.items() if info[2] in data}
            cache["crypto"] = result
            return result
        return {}

    @staticmethod
    async def get_fiat_rates() -> dict[str, float]:
        """Получить курсы фиата к USD"""
        if "fiat" in cache:
            return cache["fiat"]

        data = await CurrencyAPI._fetch(f"{FRANKFURTER_API}/latest?from=USD")

        if data and "rates" in data:
            rates = data["rates"]
            rates["USD"] = 1.0
            cache["fiat"] = rates
            return rates
        return {}

    @staticmethod
    async def convert(amount: float, from_code: str, to_code: str) -> Optional[ConversionResult]:
        """Конвертировать валюту"""
        from_code, to_code = from_code.upper(), to_code.upper()

        crypto = await CurrencyAPI.get_crypto_prices()
        fiat = await CurrencyAPI.get_fiat_rates()

        def get_usd_price(code: str) -> Optional[float]:
            if code in crypto:
                return crypto[code]
            if code == "USD":
                return 1.0
            if code in fiat:
                return 1.0 / fiat[code]
            return None

        from_usd = get_usd_price(from_code)
        to_usd = get_usd_price(to_code)

        if from_usd is None or to_usd is None:
            return None

        rate = from_usd / to_usd
        result = amount * rate

        return ConversionResult(
            amount=amount,
            from_code=from_code,
            to_code=to_code,
            result=result,
            rate=rate,
            from_usd=from_usd,
            to_usd=to_usd
        )


# ══════════════════════════════════════════════════════════════════════════════
#                              УТИЛИТЫ
# ══════════════════════════════════════════════════════════════════════════════

def fmt_num(n: float) -> str:
    """Форматирование числа"""
    if n == 0:
        return "0"
    if n >= 1_000_000:
        return f"{n:,.2f}"
    if n >= 1:
        return f"{n:,.4f}".rstrip('0').rstrip('.')
    if n >= 0.0001:
        return f"{n:.6f}".rstrip('0').rstrip('.')
    return f"{n:.10f}".rstrip('0').rstrip('.')


def get_emoji(code: str) -> str:
    """Получить эмодзи валюты"""
    if code in FIAT:
        return FIAT[code][1]
    if code in CRYPTO:
        return CRYPTO[code][1]
    return "💰"


def get_name(code: str) -> str:
    """Получить название валюты"""
    if code in FIAT:
        return FIAT[code][0]
    if code in CRYPTO:
        return CRYPTO[code][0]
    return code


# ══════════════════════════════════════════════════════════════════════════════
#                              КЛАВИАТУРЫ
# ══════════════════════════════════════════════════════════════════════════════

def kb_main() -> InlineKeyboardMarkup:
    """Главное меню"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💱 Конвертировать", callback_data="convert")],
        [InlineKeyboardButton(text="📈 Курсы крипто", callback_data="rates:crypto"),
         InlineKeyboardButton(text="💵 Курсы фиат", callback_data="rates:fiat")],
        [InlineKeyboardButton(text="⭐ Быстрые пары", callback_data="popular")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")],
    ])


def kb_currencies(currencies: dict, action: str, switch_to: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора валют"""
    builder = InlineKeyboardBuilder()

    for code in currencies:
        emoji = get_emoji(code)
        builder.button(text=f"{emoji} {code}", callback_data=f"c:{action}:{code}")

    builder.adjust(4)  # 4 кнопки в ряд

    switch_text = "🪙 Крипто" if switch_to == "crypto" else "💵 Фиат"
    builder.row(InlineKeyboardButton(text=switch_text, callback_data=f"switch:{action}:{switch_to}"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="menu"))

    return builder.as_markup()


def kb_amounts() -> InlineKeyboardMarkup:
    """Клавиатура сумм"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=str(n), callback_data=f"a:{n}") for n in [1, 10, 100]],
        [InlineKeyboardButton(text=str(n), callback_data=f"a:{n}") for n in [1000, 10000, 100000]],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="convert")],
    ])


def kb_result(from_c: str, to_c: str) -> InlineKeyboardMarkup:
    """Клавиатура результата"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Поменять местами", callback_data=f"swap:{from_c}:{to_c}")],
        [InlineKeyboardButton(text="💱 Новая конвертация", callback_data="convert"),
         InlineKeyboardButton(text="🔢 Другая сумма", callback_data=f"amt:{from_c}:{to_c}")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")],
    ])


def kb_popular() -> InlineKeyboardMarkup:
    """Популярные пары"""
    pairs = [("BTC", "USD"), ("ETH", "USD"), ("USD", "RUB"), ("BTC", "RUB"),
             ("EUR", "USD"), ("TON", "USD"), ("USD", "UAH"), ("SOL", "USD")]

    builder = InlineKeyboardBuilder()
    for f, t in pairs:
        builder.button(text=f"{get_emoji(f)} {f}→{t} {get_emoji(t)}", callback_data=f"p:{f}:{t}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🏠 Меню", callback_data="menu"))
    return builder.as_markup()


# ══════════════════════════════════════════════════════════════════════════════
#                              FSM СОСТОЯНИЯ
# ══════════════════════════════════════════════════════════════════════════════

class States(StatesGroup):
    select_from = State()
    select_to = State()
    enter_amount = State()


# ══════════════════════════════════════════════════════════════════════════════
#                              ХЕНДЛЕРЫ
# ══════════════════════════════════════════════════════════════════════════════

router = Router()


# ─────────────────────────── Старт и меню ───────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    text = """
✨ <b>Currency Converter Bot</b>

Конвертация валют в реальном времени:
• 💵 12 фиатных валют (USD, EUR, RUB...)
• 🪙 12 криптовалют (BTC, ETH, TON...)

📡 Данные обновляются каждую минуту
"""
    await message.answer(text.strip(), reply_markup=kb_main())


@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "✨ <b>Currency Converter</b>\n\nВыберите действие:",
        reply_markup=kb_main()
    )


@router.callback_query(F.data == "help")
async def cb_help(callback: CallbackQuery):
    text = """
<b>❓ Как пользоваться</b>

<b>Способ 1:</b> Через меню
• Нажмите «💱 Конвертировать»
• Выберите валюты
• Введите сумму

<b>Способ 2:</b> Быстрый ввод
Просто напишите:
<code>100 USD RUB</code>
<code>0.5 BTC EUR</code>
<code>1000 RUB TON</code>

<b>Команды:</b>
/start — главное меню
/btc /eth /ton — текущий курс
"""
    await callback.message.edit_text(
        text.strip(),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
        ])
    )


# ─────────────────────────── Курсы валют ───────────────────────────

@router.callback_query(F.data.startswith("rates:"))
async def cb_rates(callback: CallbackQuery):
    rate_type = callback.data.split(":")[1]
    await callback.answer("⏳ Загрузка...")

    if rate_type == "crypto":
        prices = await CurrencyAPI.get_crypto_prices()
        if not prices:
            await callback.message.edit_text("❌ Ошибка загрузки", reply_markup=kb_main())
            return

        lines = ["<b>📈 Курсы криптовалют</b>\n"]
        for code, info in CRYPTO.items():
            if code in prices:
                p = prices[code]
                formatted = f"${p:,.2f}" if p >= 1 else f"${p:.6f}"
                lines.append(f"{info[1]} <b>{code}</b>: {formatted}")

    else:
        rates = await CurrencyAPI.get_fiat_rates()
        if not rates:
            await callback.message.edit_text("❌ Ошибка загрузки", reply_markup=kb_main())
            return

        lines = ["<b>💵 Курсы к USD</b>\n"]
        for code, info in FIAT.items():
            if code in rates and code != "USD":
                lines.append(f"{info[1]} <b>{code}</b>: {rates[code]:.4f}")

    lines.append("\n<i>🔄 Обновлено сейчас</i>")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=callback.data)],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
        ])
    )


# ─────────────────────────── Конвертация ───────────────────────────

@router.callback_query(F.data == "convert")
async def cb_convert(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(States.select_from)
    await callback.message.edit_text(
        "💱 <b>Конвертация</b>\n\n"
        "<b>Шаг 1/3:</b> Выберите исходную валюту",
        reply_markup=kb_currencies(FIAT, "from", "crypto")
    )


@router.callback_query(F.data.startswith("switch:"))
async def cb_switch(callback: CallbackQuery):
    _, action, to_type = callback.data.split(":")
    currencies = CRYPTO if to_type == "crypto" else FIAT
    switch_to = "fiat" if to_type == "crypto" else "crypto"
    title = "🪙 Криптовалюты" if to_type == "crypto" else "💵 Фиатные валюты"

    await callback.message.edit_text(
        f"💱 <b>Конвертация</b>\n\n{title}:",
        reply_markup=kb_currencies(currencies, action, switch_to)
    )


@router.callback_query(F.data.startswith("c:from:"))
async def cb_select_from(callback: CallbackQuery, state: FSMContext):
    code = callback.data.split(":")[2]
    await state.update_data(from_code=code)
    await state.set_state(States.select_to)

    await callback.message.edit_text(
        f"💱 <b>Конвертация</b>\n\n"
        f"✅ Из: {get_emoji(code)} <b>{code}</b>\n\n"
        f"<b>Шаг 2/3:</b> Выберите целевую валюту",
        reply_markup=kb_currencies(FIAT, "to", "crypto")
    )


@router.callback_query(F.data.startswith("c:to:"))
async def cb_select_to(callback: CallbackQuery, state: FSMContext):
    code = callback.data.split(":")[2]
    data = await state.get_data()
    from_code = data.get("from_code")

    if code == from_code:
        await callback.answer("❌ Выберите другую валюту!", show_alert=True)
        return

    await state.update_data(to_code=code)
    await state.set_state(States.enter_amount)

    await callback.message.edit_text(
        f"💱 <b>Конвертация</b>\n\n"
        f"{get_emoji(from_code)} <b>{from_code}</b> ➜ <b>{code}</b> {get_emoji(code)}\n\n"
        f"<b>Шаг 3/3:</b> Введите сумму или выберите:",
        reply_markup=kb_amounts()
    )


@router.callback_query(F.data.startswith("a:"))
async def cb_amount(callback: CallbackQuery, state: FSMContext):
    amount = float(callback.data.split(":")[1])
    await process_conversion(callback.message, state, amount, edit=True)


@router.message(States.enter_amount)
async def msg_amount(message: Message, state: FSMContext):
    try:
        text = message.text.replace(",", ".").replace(" ", "")
        amount = float(text)
        if amount <= 0:
            raise ValueError
        await process_conversion(message, state, amount, edit=False)
    except ValueError:
        await message.answer("❌ Введите корректное число\nПример: <code>100</code> или <code>0.5</code>")


async def process_conversion(message: Message, state: FSMContext, amount: float, edit: bool):
    """Выполнение конвертации и вывод результата"""
    data = await state.get_data()
    from_code = data.get("from_code")
    to_code = data.get("to_code")

    if not from_code or not to_code:
        await message.answer("❌ Ошибка. Начните заново /start")
        return

    result = await CurrencyAPI.convert(amount, from_code, to_code)

    if not result:
        text = "❌ Не удалось получить курс. Попробуйте позже."
        if edit:
            await message.edit_text(text, reply_markup=kb_main())
        else:
            await message.answer(text, reply_markup=kb_main())
        return

    # Красивый вывод результата
    text = f"""
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃      💱 <b>КОНВЕРТАЦИЯ</b>
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━┛

  {get_emoji(from_code)}  <b>{fmt_num(result.amount)} {from_code}</b>
              ⬇️
  {get_emoji(to_code)}  <b>{fmt_num(result.result)} {to_code}</b>

┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈

📊 <b>Курс обмена:</b>
   1 {from_code} = {fmt_num(result.rate)} {to_code}

💵 <b>Цена в USD:</b>
   1 {from_code} = ${fmt_num(result.from_usd)}
   1 {to_code} = ${fmt_num(result.to_usd)}

┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈
⏱ <i>Актуальный курс</i>
"""

    kb = kb_result(from_code, to_code)

    if edit:
        await message.edit_text(text.strip(), reply_markup=kb)
    else:
        await message.answer(text.strip(), reply_markup=kb)


# ─────────────────────────── Доп. действия ───────────────────────────

@router.callback_query(F.data.startswith("swap:"))
async def cb_swap(callback: CallbackQuery, state: FSMContext):
    _, from_c, to_c = callback.data.split(":")
    await state.update_data(from_code=to_c, to_code=from_c)
    await state.set_state(States.enter_amount)

    await callback.message.edit_text(
        f"🔄 <b>Поменяли местами!</b>\n\n"
        f"{get_emoji(to_c)} <b>{to_c}</b> ➜ <b>{from_c}</b> {get_emoji(from_c)}\n\n"
        f"Введите сумму:",
        reply_markup=kb_amounts()
    )


@router.callback_query(F.data.startswith("amt:"))
async def cb_new_amount(callback: CallbackQuery, state: FSMContext):
    _, from_c, to_c = callback.data.split(":")
    await state.update_data(from_code=from_c, to_code=to_c)
    await state.set_state(States.enter_amount)

    await callback.message.edit_text(
        f"💱 <b>{from_c} ➜ {to_c}</b>\n\nВведите сумму:",
        reply_markup=kb_amounts()
    )


@router.callback_query(F.data == "popular")
async def cb_popular(callback: CallbackQuery):
    await callback.message.edit_text(
        "⭐ <b>Популярные пары</b>\n\nВыберите для конвертации:",
        reply_markup=kb_popular()
    )


@router.callback_query(F.data.startswith("p:"))
async def cb_pair(callback: CallbackQuery, state: FSMContext):
    _, from_c, to_c = callback.data.split(":")
    await state.update_data(from_code=from_c, to_code=to_c)
    await state.set_state(States.enter_amount)

    await callback.message.edit_text(
        f"💱 {get_emoji(from_c)} <b>{from_c}</b> ➜ <b>{to_c}</b> {get_emoji(to_c)}\n\n"
        f"Введите сумму:",
        reply_markup=kb_amounts()
    )


# ─────────────────────────── Быстрый ввод ───────────────────────────

@router.message(F.text.regexp(r"^[\d\s,\.]+\s+[A-Za-z]{2,6}\s+[A-Za-z]{2,6}$", flags=re.I))
async def quick_convert(message: Message, state: FSMContext):
    """Быстрая конвертация: 100 USD RUB"""
    try:
        parts = message.text.upper().split()
        amount = float(parts[0].replace(",", ".").replace(" ", ""))
        from_c, to_c = parts[1], parts[2]

        if from_c not in ALL_CURRENCIES or to_c not in ALL_CURRENCIES:
            return

        await state.update_data(from_code=from_c, to_code=to_c)
        await process_conversion(message, state, amount, edit=False)
    except:
        pass


# ─────────────────────────── Быстрые команды ───────────────────────────

@router.message(Command("btc", "eth", "ton", "sol", "bnb"))
async def cmd_crypto_price(message: Message):
    """Быстрый курс крипты"""
    code = message.text[1:].upper()
    prices = await CurrencyAPI.get_crypto_prices()

    if code in prices:
        p = prices[code]
        info = CRYPTO.get(code, (code, "🪙", ""))
        formatted = f"${p:,.2f}" if p >= 1 else f"${p:.6f}"
        await message.answer(f"{info[1]} <b>{info[0]}</b>\n\n💵 {formatted}")
    else:
        await message.answer("❌ Не удалось получить курс")


@router.message(Command("rates"))
async def cmd_rates(message: Message):
    await message.answer(
        "📊 <b>Выберите тип:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📈 Крипто", callback_data="rates:crypto"),
             InlineKeyboardButton(text="💵 Фиат", callback_data="rates:fiat")]
        ])
    )


# ══════════════════════════════════════════════════════════════════════════════
#                              ЗАПУСК
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    if BOT_TOKEN == "ВАШ_ТОКЕН_ЗДЕСЬ":
        print("❌ Вставьте токен бота в переменную BOT_TOKEN!")
        print("   Получить токен: @BotFather в Telegram")
        return

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    print("✅ Бот запущен!")
    print("📡 API: CoinGecko (крипто) + Frankfurter (фиат)")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())