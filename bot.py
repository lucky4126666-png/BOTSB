import os
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

BOT_TOKEN = os.getenv("BOT_TOKEN")

SUPER_ADMINS = {8572604188}  # ID của bạn

groups = {}

# ================= DATA =================
def get_group(gid):
    if gid not in groups:
        groups[gid] = {
            "wallet": None,
            "wallet_img": None,
            "bank": None,
            "lang": "VN",
            "admins": set()
        }
    return groups[gid]

def is_admin(uid, gid):
    g = get_group(gid)
    return uid in SUPER_ADMINS or uid in g["admins"]

# ================= TEXT =================
TEXT = {
    "VN": {
        "menu": "🤖 BOT TRỢ LÝ THANH TOÁN",
        "no_wallet": "⚠️ Chưa có ví USDT.\nVui lòng liên hệ admin.",
        "no_bank": "⚠️ Chưa có số tài khoản.\nVui lòng liên hệ admin.",
        "ask_wallet": "💳 Mời bạn nhập ví USDT mới + hình ảnh",
        "ask_bank": "🏦 Nhập STK | Tên | Ngân hàng",
        "wallet_ok": "✅ Đã cập nhật ví USDT (TRC20)",
        "bank_ok": "✅ Đã cập nhật số tài khoản"
    },
    "CN": {
        "menu": "🤖 支付助理机器人",
        "no_wallet": "⚠️ 尚未设置 USDT 钱包",
        "no_bank": "⚠️ 尚未设置银行账户",
        "ask_wallet": "💳 请输入新的 USDT 钱包 + 图片",
        "ask_bank": "🏦 输入 账户 | 姓名 | 银行",
        "wallet_ok": "✅ 已更新 USDT 钱包",
        "bank_ok": "✅ 已更新银行账户"
    }
}

# ================= MENU =================
def main_menu(lang):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Ví USDT (TRC20)", callback_data="view_wallet")],
        [InlineKeyboardButton("🏦 Số tài khoản", callback_data="view_bank")],
        [
            InlineKeyboardButton("🇻🇳 VN", callback_data="lang_vn"),
            InlineKeyboardButton("🇨🇳 CN", callback_data="lang_cn")
        ]
    ])

def admin_edit_menu(wallet=False):
    btns = []
    if wallet:
        btns.append([InlineKeyboardButton("✏️ Sửa ví", callback_data="edit_wallet")])
    else:
        btns.append([InlineKeyboardButton("✏️ Sửa STK", callback_data="edit_bank")])
    return InlineKeyboardMarkup(btns)

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    g = get_group(gid)
    await update.message.reply_text(
        TEXT[g["lang"]]["menu"],
        reply_markup=main_menu(g["lang"])
    )

# ================= CALLBACK =================
async def cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    gid = q.message.chat.id
    uid = q.from_user.id
    g = get_group(gid)
    lang = g["lang"]

    if q.data == "lang_vn":
        g["lang"] = "VN"
        await q.edit_message_text(TEXT["VN"]["menu"], reply_markup=main_menu("VN"))

    elif q.data == "lang_cn":
        g["lang"] = "CN"
        await q.edit_message_text(TEXT["CN"]["menu"], reply_markup=main_menu("CN"))

    elif q.data == "view_wallet":
        if g["wallet"]:
            if is_admin(uid, gid):
                await q.message.reply_photo(
                    photo=g["wallet_img"],
                    caption=g["wallet"],
                    reply_markup=admin_edit_menu(wallet=True)
                )
            else:
                await q.message.reply_photo(
                    photo=g["wallet_img"],
                    caption=g["wallet"]
                )
        else:
            await q.message.reply_text(TEXT[lang]["no_wallet"])

    elif q.data == "view_bank":
        if g["bank"]:
            if is_admin(uid, gid):
                await q.message.reply_text(
                    g["bank"],
                    reply_markup=admin_edit_menu(wallet=False)
                )
            else:
                await q.message.reply_text(g["bank"])
        else:
            await q.message.reply_text(TEXT[lang]["no_bank"])

    elif q.data == "edit_wallet" and is_admin(uid, gid):
        context.user_data["await_wallet"] = True
        await q.message.reply_text(TEXT[lang]["ask_wallet"])

    elif q.data == "edit_bank" and is_admin(uid, gid):
        context.user_data["await_bank"] = True
        await q.message.reply_text(TEXT[lang]["ask_bank"])

# ================= MESSAGE =================
async def msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    gid = msg.chat.id
    uid = msg.from_user.id
    g = get_group(gid)
    text = (msg.text or "").lower()

    # auto reply
    if any(k in text for k in ["ví", "trc20"]) and g["wallet"]:
        await msg.reply_photo(photo=g["wallet_img"], caption=g["wallet"])
        return

    if any(k in text for k in ["stk", "thanh toán"]) and g["bank"]:
        await msg.reply_text(g["bank"])
        return

    # set wallet
    if context.user_data.get("await_wallet") and is_admin(uid, gid):
        g["wallet"] = msg.text
        g["wallet_img"] = msg.photo[-1].file_id if msg.photo else g["wallet_img"]
        context.user_data.clear()
        await msg.reply_text(TEXT[g["lang"]]["wallet_ok"])
        return

    # set bank
    if context.user_data.get("await_bank") and is_admin(uid, gid):
        g["bank"] = msg.text
        context.user_data.clear()
        await msg.reply_text(TEXT[g["lang"]]["bank_ok"])

# ================= RUN =================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(cb))
    app.add_handler(MessageHandler(filters.ALL, msg_handler))
    print("🤖 Bot trợ lý ví đang chạy")
    app.run_polling()

if __name__ == "__main__":
    main()
