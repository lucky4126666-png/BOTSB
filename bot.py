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

# ================= CONFIG =================
BOT_TOKEN = "PUT_YOUR_BOT_TOKEN_HERE"

ADMIN_ID = {8572604188}  # <<< ID TELEGRAM CỦA BẠN (DUY NHẤT)
ADMIN_USERNAME = "@qitianlong777"

# ================= DATA =================
DATA = {
    "lang": "VN",
    "wallet": None,
    "wallet_img": None,
    "bank": None,
    "bank_img": None
}

# ================= TEXT =================
TEXT = {
    "VN": {
        "title": "🤖 BOT TRỢ LÝ THANH TOÁN",
        "no_wallet": "⚠️ Chưa có ví USDT.\nVui lòng liên hệ admin.",
        "no_bank": "⚠️ Chưa có số tài khoản.\nVui lòng liên hệ admin.",
        "edit_wallet": "💳 CẬP NHẬT VÍ USDT (TRC20)\n\n📌 Vui lòng gửi:\n• Địa chỉ ví\n• 01 hình ảnh QR",
        "edit_bank": "🏦 CẬP NHẬT SỐ TÀI KHOẢN\n\n📌 Vui lòng gửi:\n• STK\n• Tên\n• Ngân hàng\n• 01 hình ảnh",
        "missing": "⚠️ Thiếu thông tin hoặc hình ảnh.\nVui lòng gửi lại ĐẦY ĐỦ.",
        "saved": "✅ Đã cập nhật thành công.",
        "warning": (
            "⚠️ LƯU Ý QUAN TRỌNG\n\n"
            "Chúng tôi CHỈ sử dụng DUY NHẤT:\n"
            "• 01 ví USDT (TRC20)\n"
            "• 01 số tài khoản ngân hàng\n\n"
            f"Tất cả thông tin do admin {ADMIN_USERNAME} xác nhận.\n\n"
            "❗ Nếu KHÁC nội dung bot gửi:\n"
            "→ KHÔNG chịu trách nhiệm\n"
            "→ Cảnh giác GIẢ MẠO / LỪA ĐẢO"
        )
    },
    "CN": {
        "title": "🤖 支付助手机器人",
        "no_wallet": "⚠️ 尚未设置 USDT 钱包。\n请联系管理员。",
        "no_bank": "⚠️ 尚未设置银行卡。\n请联系管理员。",
        "edit_wallet": "💳 更新 USDT 钱包(TRC20)\n\n📌 请发送：\n• 钱包地址\n• 1 张二维码图片",
        "edit_bank": "🏦 更新银行卡信息\n\n📌 请发送：\n• 卡号\n• 姓名\n• 银行\n• 1 张图片",
        "missing": "⚠️ 信息或图片不完整，请重新发送。",
        "saved": "✅ 更新成功。",
        "warning": (
            "⚠️ 重要提示\n\n"
            "我们只使用唯一：\n"
            "• 01 个 USDT 钱包(TRC20)\n"
            "• 01 个银行账户\n\n"
            f"所有信息由管理员 {ADMIN_USERNAME} 确认。\n\n"
            "❗ 若信息与机器人不同：\n"
            "→ 概不负责\n"
            "→ 谨防诈骗"
        )
    }
}

# ================= KEYBOARD =================
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Ví USDT (TRC20)", callback_data="wallet")],
        [InlineKeyboardButton("🏦 Số tài khoản", callback_data="bank")],
        [
            InlineKeyboardButton("🇻🇳 VN", callback_data="lang_vn"),
            InlineKeyboardButton("🇨🇳 CN", callback_data="lang_cn")
        ],
        [InlineKeyboardButton("✏️ Admin", callback_data="admin")]
    ])

def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Sửa ví USDT", callback_data="edit_wallet")],
        [InlineKeyboardButton("✏️ Sửa STK", callback_data="edit_bank")],
        [InlineKeyboardButton("👁 Xem trước", callback_data="preview")],
        [InlineKeyboardButton("⬅️ Quay lại", callback_data="back")]
    ])

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        TEXT[DATA["lang"]]["title"],
        reply_markup=main_menu()
    )

# ================= CALLBACK =================
async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    lang = DATA["lang"]

    if q.data == "wallet":
        if not DATA["wallet"]:
            await q.message.reply_text(TEXT[lang]["no_wallet"])
        else:
            await q.message.reply_photo(
                photo=DATA["wallet_img"],
                caption=f"💳 Ví USDT (TRC20)\n\n{DATA['wallet']}\n\n{TEXT[lang]['warning']}"
            )

    elif q.data == "bank":
        if not DATA["bank"]:
            await q.message.reply_text(TEXT[lang]["no_bank"])
        else:
            await q.message.reply_photo(
                photo=DATA["bank_img"],
                caption=f"🏦 Số tài khoản\n\n{DATA['bank']}\n\n{TEXT[lang]['warning']}"
            )

    elif q.data == "lang_vn":
        DATA["lang"] = "VN"
        await q.message.edit_text(TEXT["VN"]["title"], reply_markup=main_menu())

    elif q.data == "lang_cn":
        DATA["lang"] = "CN"
        await q.message.edit_text(TEXT["CN"]["title"], reply_markup=main_menu())

    elif q.data == "admin" and uid in ADMIN_ID:
        await q.message.edit_text("👑 ADMIN", reply_markup=admin_menu())

    elif q.data == "edit_wallet" and uid in ADMIN_ID:
        context.user_data["await_wallet"] = True
        await q.message.reply_text(TEXT[lang]["edit_wallet"])

    elif q.data == "edit_bank" and uid in ADMIN_ID:
        context.user_data["await_bank"] = True
        await q.message.reply_text(TEXT[lang]["edit_bank"])

    elif q.data == "preview":
        if DATA["wallet"]:
            await q.message.reply_photo(
                photo=DATA["wallet_img"],
                caption=f"{DATA['wallet']}\n\n{TEXT[lang]['warning']}"
            )

    elif q.data == "back":
        await q.message.edit_text(TEXT[lang]["title"], reply_markup=main_menu())

# ================= MESSAGE =================
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lang = DATA["lang"]

    # ADMIN UPDATE WALLET
    if context.user_data.get("await_wallet") and uid in ADMIN_ID:
        if update.message.text and update.message.photo:
            DATA["wallet"] = update.message.text.strip()
            DATA["wallet_img"] = update.message.photo[-1].file_id
            context.user_data.clear()
            await update.message.reply_text(TEXT[lang]["saved"])
        else:
            await update.message.reply_text(TEXT[lang]["missing"])

    # ADMIN UPDATE BANK
    elif context.user_data.get("await_bank") and uid in ADMIN_ID:
        if update.message.text and update.message.photo:
            DATA["bank"] = update.message.text.strip()
            DATA["bank_img"] = update.message.photo[-1].file_id
            context.user_data.clear()
            await update.message.reply_text(TEXT[lang]["saved"])
        else:
            await update.message.reply_text(TEXT[lang]["missing"])

    # AUTO KEYWORDS
    else:
        text = update.message.text.lower() if update.message.text else ""
        if any(k in text for k in ["ví", "trc20"]):
            if DATA["wallet"]:
                await update.message.reply_photo(
                    photo=DATA["wallet_img"],
                    caption=f"{DATA['wallet']}\n\n{TEXT[lang]['warning']}"
                )
        elif any(k in text for k in ["stk", "thanh toán"]):
            if DATA["bank"]:
                await update.message.reply_photo(
                    photo=DATA["bank_img"],
                    caption=f"{DATA['bank']}\n\n{TEXT[lang]['warning']}"
                )

# ================= RUN =================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.ALL, message_handler))
    print("🤖 Bot trợ lý thanh toán đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()
