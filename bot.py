from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

# ================== CONFIG ==================
BOT_TOKEN = "8464183339:AAEUNadM4MOUt_dFhpeLDlfp1JlYBqNJZ4E"
ADMIN_ID = 8572604188  # ID của BẠN (duy nhất)
ADMIN_USERNAME = "@qitianlong777"

# ================== DATA (RAM) ==================
DATA = {
    "wallet": None,
    "wallet_img": None,
    "bank": None,
    "bank_img": None
}

# dữ liệu chờ xem trước
PREVIEW = {}

# ================== LANGUAGE ==================
LANG = {
    "vn": {
        "title": "🤖 BOT TRỢ LÝ THANH TOÁN",
        "wallet": "💳 Ví USDT (TRC20)",
        "bank": "🏦 Số tài khoản",
        "admin": "👑 Admin",
        "edit_wallet": "✏️ Sửa ví USDT",
        "edit_bank": "✏️ Sửa STK",
        "preview": "👁 Xem trước",
        "back": "⬅️ Quay lại",

        "ask_wallet": (
            "💳 CẬP NHẬT VÍ USDT (TRC20)\n\n"
            "📌 Vui lòng gửi:\n"
            "• ĐỊA CHỈ VÍ\n"
            "• 01 ẢNH QR\n\n"
            "➡️ Gửi ảnh + nhập địa chỉ vào CAPTION."
        ),
        "ask_bank": (
            "🏦 CẬP NHẬT SỐ TÀI KHOẢN\n\n"
            "📌 Vui lòng gửi:\n"
            "• STK\n• Tên\n• Ngân hàng\n"
            "• 01 ẢNH\n\n"
            "➡️ Gửi ảnh + nhập nội dung vào CAPTION."
        ),
        "missing": "⚠️ Thiếu nội dung hoặc hình ảnh.\nVui lòng gửi lại ĐẦY ĐỦ.",
        "saved": "✅ Đã cập nhật thành công.",
        "no_wallet": "⚠️ Chưa có ví USDT.\nVui lòng liên hệ admin.",
        "no_bank": "⚠️ Chưa có số tài khoản.\nVui lòng liên hệ admin.",

        "warning": (
            "⚠️ LƯU Ý QUAN TRỌNG\n\n"
            "Chúng tôi CHỈ sử dụng DUY NHẤT:\n"
            "• 01 ví USDT (TRC20)\n"
            "• 01 số tài khoản ngân hàng\n\n"
            f"Tất cả thông tin do admin {ADMIN_USERNAME} xác nhận.\n\n"
            "❗ Nếu KHÁC nội dung bot gửi:\n"
            "→ KHÔNG chịu trách nhiệm\n"
            "→ Cảnh giác GIẢ MẠO / LỪA ĐẢO"
        ),
        "confirm": "✅ XÁC NHẬN",
        "cancel": "❌ HỦY"
    },

    "cn": {
        "title": "🤖 支付助手机器人",
        "wallet": "💳 USDT 钱包 (TRC20)",
        "bank": "🏦 银行账户",
        "admin": "👑 管理员",
        "edit_wallet": "✏️ 修改钱包",
        "edit_bank": "✏️ 修改银行卡",
        "preview": "👁 预览",
        "back": "⬅️ 返回",

        "ask_wallet": (
            "💳 更新 USDT 钱包 (TRC20)\n\n"
            "📌 请发送：\n"
            "• 钱包地址\n"
            "• 1 张二维码图片\n\n"
            "➡️ 图片 + 地址写在说明里。"
        ),
        "ask_bank": (
            "🏦 更新银行卡信息\n\n"
            "📌 请发送：\n"
            "• 卡号\n• 姓名\n• 银行\n"
            "• 1 张图片\n\n"
            "➡️ 图片 + 信息写在说明里。"
        ),
        "missing": "⚠️ 信息或图片不完整，请重新发送。",
        "saved": "✅ 更新成功。",
        "no_wallet": "⚠️ 尚未设置 USDT 钱包。\n请联系管理员。",
        "no_bank": "⚠️ 尚未设置银行账户。\n请联系管理员。",

        "warning": (
            "⚠️ 重要提示\n\n"
            "我们只使用唯一：\n"
            "• 1 个 USDT(TRC20) 钱包\n"
            "• 1 个银行账户\n\n"
            f"所有信息由管理员 {ADMIN_USERNAME} 确认。\n\n"
            "❗ 若与机器人信息不同：\n"
            "→ 概不负责\n"
            "→ 谨防诈骗"
        ),
        "confirm": "✅ 确认",
        "cancel": "❌ 取消"
    }
}

# ================== KEYBOARD ==================
def main_menu(lang):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(LANG[lang]["wallet"], callback_data="wallet")],
        [InlineKeyboardButton(LANG[lang]["bank"], callback_data="bank")],
        [
            InlineKeyboardButton("🇻🇳 VN", callback_data="lang_vn"),
            InlineKeyboardButton("🇨🇳 CN", callback_data="lang_cn")
        ],
        [InlineKeyboardButton(LANG[lang]["admin"], callback_data="admin")]
    ])

def admin_menu(lang):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(LANG[lang]["edit_wallet"], callback_data="edit_wallet")],
        [InlineKeyboardButton(LANG[lang]["edit_bank"], callback_data="edit_bank")],
        [InlineKeyboardButton(LANG[lang]["preview"], callback_data="preview")],
        [InlineKeyboardButton(LANG[lang]["back"], callback_data="back")]
    ])

def confirm_kb(lang):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(LANG[lang]["confirm"], callback_data="confirm"),
            InlineKeyboardButton(LANG[lang]["cancel"], callback_data="cancel")
        ]
    ])

# ================== START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data.setdefault("lang", "vn")
    l = context.chat_data["lang"]
    await update.message.reply_text(
        LANG[l]["title"],
        reply_markup=main_menu(l)
    )

# ================== CALLBACK ==================
async def cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    l = context.chat_data.get("lang", "vn")

    if q.data == "wallet":
        if not DATA["wallet"]:
            await q.message.reply_text(LANG[l]["no_wallet"])
        else:
            await q.message.reply_photo(
                DATA["wallet_img"],
                caption=f"💳 {LANG[l]['wallet']}\n\n{DATA['wallet']}\n\n{LANG[l]['warning']}"
            )

    elif q.data == "bank":
        if not DATA["bank"]:
            await q.message.reply_text(LANG[l]["no_bank"])
        else:
            await q.message.reply_photo(
                DATA["bank_img"],
                caption=f"🏦 {LANG[l]['bank']}\n\n{DATA['bank']}\n\n{LANG[l]['warning']}"
            )

    elif q.data == "lang_vn":
        context.chat_data["lang"] = "vn"
        await q.message.edit_text(LANG["vn"]["title"], reply_markup=main_menu("vn"))

    elif q.data == "lang_cn":
        context.chat_data["lang"] = "cn"
        await q.message.edit_text(LANG["cn"]["title"], reply_markup=main_menu("cn"))

    elif q.data == "admin" and uid == ADMIN_ID:
        await q.message.edit_text("👑 ADMIN", reply_markup=admin_menu(l))

    elif q.data == "edit_wallet" and uid == ADMIN_ID:
        context.user_data["await_wallet"] = True
        await q.message.reply_text(LANG[l]["ask_wallet"])

    elif q.data == "edit_bank" and uid == ADMIN_ID:
        context.user_data["await_bank"] = True
        await q.message.reply_text(LANG[l]["ask_bank"])

    elif q.data == "preview" and uid == ADMIN_ID:
        if DATA["wallet"]:
            await q.message.reply_photo(
                DATA["wallet_img"],
                caption=f"{DATA['wallet']}\n\n{LANG[l]['warning']}"
            )

    elif q.data == "confirm" and uid == ADMIN_ID:
        data = PREVIEW.pop(uid, None)
        if data:
            DATA.update(data)
            context.user_data.clear()
            await q.message.reply_text(LANG[l]["saved"])

    elif q.data == "cancel" and uid == ADMIN_ID:
        PREVIEW.pop(uid, None)
        context.user_data.clear()
        await q.message.reply_text("❌ Đã hủy.")

    elif q.data == "back":
        await q.message.edit_text(LANG[l]["title"], reply_markup=main_menu(l))

# ================== MESSAGE ==================
async def msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    l = context.chat_data.get("lang", "vn")

    text = update.message.text or update.message.caption
    photo = update.message.photo

    # ===== UPDATE WALLET =====
    if context.user_data.get("await_wallet") and uid == ADMIN_ID:
        if text and photo:
            PREVIEW[uid] = {
                "wallet": text.strip(),
                "wallet_img": photo[-1].file_id
            }
            await update.message.reply_photo(
                photo[-1].file_id,
                caption=f"👁 {LANG[l]['preview']}\n\n{text}\n\n{LANG[l]['warning']}",
                reply_markup=confirm_kb(l)
            )
        else:
            await update.message.reply_text(LANG[l]["missing"])

    # ===== UPDATE BANK =====
    elif context.user_data.get("await_bank") and uid == ADMIN_ID:
        if text and photo:
            PREVIEW[uid] = {
                "bank": text.strip(),
                "bank_img": photo[-1].file_id
            }
            await update.message.reply_photo(
                photo[-1].file_id,
                caption=f"👁 {LANG[l]['preview']}\n\n{text}\n\n{LANG[l]['warning']}",
                reply_markup=confirm_kb(l)
            )
        else:
            await update.message.reply_text(LANG[l]["missing"])

    # ===== AUTO KEYWORDS =====
    else:
        t = (update.message.text or "").lower()
        if any(k in t for k in ["ví", "trc20"]):
            if DATA["wallet"]:
                await update.message.reply_photo(
                    DATA["wallet_img"],
                    caption=f"{DATA['wallet']}\n\n{LANG[l]['warning']}"
                )
        elif any(k in t for k in ["stk", "thanh toán"]):
            if DATA["bank"]:
                await update.message.reply_photo(
                    DATA["bank_img"],
                    caption=f"{DATA['bank']}\n\n{LANG[l]['warning']}"
                )

# ================== RUN ==================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(cb))
    app.add_handler(MessageHandler(filters.ALL, msg))
    print("🤖 Assistant bot running…")
    app.run_polling()

if __name__ == "__main__":
    main()
