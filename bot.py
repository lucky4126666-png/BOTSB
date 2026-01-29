import os
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

BOT_TOKEN = "YOUR_BOT_TOKEN"

# ================= DATA =================
wallets = {}   # gid -> wallet
banks = {}     # gid -> {stk, name, bank}
langs = {}     # gid -> VN / CN
group_admins = {}  # gid -> set(uid)

WALLET_IMAGE = "assets/wallet.jpg"
BANK_IMAGE = "assets/bank.jpg"

WALLET_KEYS = ["ví", "trc20"]
BANK_KEYS = ["stk", "thanh toán"]

# ================= TEXT =================
DISCLAIMER_VN = (
    "⚠️ LƯU Ý QUAN TRỌNG\n\n"
    "Chúng tôi CHỈ sử dụng DUY NHẤT:\n"
    "• 01 ví USDT (TRC20)\n"
    "• 01 số tài khoản ngân hàng\n\n"
    "Tất cả thông tin đều do admin @qitianlong777 xác nhận chính thức.\n\n"
    "❗ Nếu thông tin KHÁC với nội dung bot gửi:\n"
    "→ Chúng tôi KHÔNG chịu trách nhiệm\n"
    "→ Cảnh giác GIẢ MẠO / LỪA ĐẢO"
)

DISCLAIMER_CN = (
    "⚠️ 重要提示\n\n"
    "我们只使用【唯一】：\n"
    "• 一个 USDT 钱包（TRC20）\n"
    "• 一个银行账户\n\n"
    "所有信息均由管理员 @qitianlong777 官方确认。\n\n"
    "❗ 如信息与机器人发送内容不一致：\n"
    "→ 我们概不负责\n"
    "→ 请警惕诈骗与冒充行为"
)

# ================= UTILS =================
def get_lang(gid):
    return langs.get(gid, "VN")

def is_admin(uid, gid):
    return uid in group_admins.get(gid, set())

# ================= SEND =================
async def send_wallet(msg, wallet, lang):
    text = (
        f"💳 Ví USDT (TRC20)\n\n"
        f"📌 Quét QR bên trên hoặc sao chép địa chỉ bên dưới:\n\n"
        f"{wallet}\n\n"
        f"{DISCLAIMER_CN if lang=='CN' else DISCLAIMER_VN}"
    )
    await msg.reply_photo(photo=WALLET_IMAGE, caption=text)

async def send_bank(msg, bank, lang):
    text = (
        "🏦 Thông tin chuyển khoản\n\n"
        f"STK : {bank['stk']}\n"
        f"Tên : {bank['name']}\n"
        f"Ngân hàng : {bank['bank']}\n\n"
        f"{DISCLAIMER_CN if lang=='CN' else DISCLAIMER_VN}"
    )
    await msg.reply_photo(photo=BANK_IMAGE, caption=text)

# ================= MENU =================
def main_menu(lang):
    if lang == "CN":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 USDT 钱包", callback_data="wallet")],
            [InlineKeyboardButton("🏦 银行账户", callback_data="bank")],
            [InlineKeyboardButton("🌐 语言", callback_data="lang")],
            [InlineKeyboardButton("👑 管理员", callback_data="admin")],
            [InlineKeyboardButton("❌ 关闭", callback_data="close")]
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Ví USDT (TRC20)", callback_data="wallet")],
        [InlineKeyboardButton("🏦 Số tài khoản", callback_data="bank")],
        [InlineKeyboardButton("🌐 Ngôn ngữ", callback_data="lang")],
        [InlineKeyboardButton("👑 Phân quyền Admin", callback_data="admin")],
        [InlineKeyboardButton("❌ Đóng", callback_data="close")]
    ])

# ================= START =================
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    langs.setdefault(gid, "VN")
    group_admins.setdefault(gid, {update.effective_user.id})
    await update.message.reply_text(
        "🤖 BOT TRỢ LÝ THANH TOÁN",
        reply_markup=main_menu(get_lang(gid))
    )

# ================= CALLBACK =================
async def cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    gid = q.message.chat.id
    uid = q.from_user.id
    lang = get_lang(gid)

    if q.data == "wallet":
        if gid in wallets:
            await send_wallet(q.message, wallets[gid], lang)
        else:
            await q.message.reply_text("❌ Chưa có ví")

    elif q.data == "bank":
        if gid in banks:
            await send_bank(q.message, banks[gid], lang)
        else:
            await q.message.reply_text("❌ Chưa có tài khoản")

    elif q.data == "lang":
        langs[gid] = "CN" if lang == "VN" else "VN"
        await q.message.edit_reply_markup(reply_markup=main_menu(get_lang(gid)))

    elif q.data == "admin" and is_admin(uid, gid):
        await q.message.reply_text("👑 Bạn là admin")

    elif q.data == "close":
        await q.message.delete()

# ================= MESSAGE =================
async def handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    gid = update.effective_chat.id
    lang = get_lang(gid)

    if any(k in text for k in WALLET_KEYS) and gid in wallets:
        await send_wallet(update.message, wallets[gid], lang)

    if any(k in text for k in BANK_KEYS) and gid in banks:
        await send_bank(update.message, banks[gid], lang)

# ================= RUN =================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handler))
    print("🤖 Bot trợ lý ví đang chạy…")
    app.run_polling()

if __name__ == "__main__":
    main()

