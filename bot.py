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

SUPER_ADMINS = {8572604188}  # admin chính

groups = {}

# ================= DATA =================
def get_group(gid):
    if gid not in groups:
        groups[gid] = {
            "wallet": None,
            "wallet_img": None,
            "bank": None,
            "lang": "VN",
            "admins": set(),
            "tmp_wallet": {},
            "tmp_bank": {}
        }
    return groups[gid]

def is_admin(uid, gid):
    g = get_group(gid)
    return uid in SUPER_ADMINS or uid in g["admins"]

# ================= TEXT =================
ADMIN_TAG = "@qitianlong777"

ANTI_FAKE_VN = (
    "⚠️ LƯU Ý QUAN TRỌNG\n"
    "Chúng tôi CHỈ sử dụng DUY NHẤT:\n"
    "• 01 ví USDT (TRC20)\n"
    "• 01 số tài khoản ngân hàng\n\n"
    f"Tất cả thông tin do admin {ADMIN_TAG} xác nhận.\n\n"
    "❗ Nếu thông tin KHÁC với nội dung bot gửi:\n"
    "→ Chúng tôi KHÔNG chịu trách nhiệm\n"
    "→ Cảnh giác GIẢ MẠO / LỪA ĐẢO"
)

# ================= MENU =================
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Ví USDT (TRC20)", callback_data="view_wallet")],
        [InlineKeyboardButton("🏦 Số tài khoản", callback_data="view_bank")]
    ])

def confirm_menu(key):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Xác nhận lưu", callback_data=f"confirm_{key}"),
            InlineKeyboardButton("❌ Hủy", callback_data=f"cancel_{key}")
        ]
    ])

def admin_edit_menu(key):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Sửa", callback_data=f"edit_{key}")]
    ])

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 BOT TRỢ LÝ THANH TOÁN",
        reply_markup=main_menu()
    )

# ================= CALLBACK =================
async def cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    gid = q.message.chat.id
    uid = q.from_user.id
    g = get_group(gid)

    # ===== VIEW =====
    if q.data == "view_wallet":
        if g["wallet"]:
            kb = admin_edit_menu("wallet") if is_admin(uid, gid) else None
            await q.message.reply_photo(
                photo=g["wallet_img"],
                caption=f"💳 VÍ USDT (TRC20)\n\n{g['wallet']}\n\n{ANTI_FAKE_VN}",
                reply_markup=kb
            )
        else:
            await q.message.reply_text(ANTI_FAKE_VN)

    elif q.data == "view_bank":
        if g["bank"]:
            kb = admin_edit_menu("bank") if is_admin(uid, gid) else None
            await q.message.reply_text(f"{g['bank']}\n\n{ANTI_FAKE_VN}", reply_markup=kb)
        else:
            await q.message.reply_text(ANTI_FAKE_VN)

    # ===== EDIT =====
    elif q.data == "edit_wallet" and is_admin(uid, gid):
        g["tmp_wallet"].clear()
        context.user_data["await_wallet"] = True
        await q.message.reply_text(
            "💳 CẬP NHẬT VÍ USDT (TRC20)\n\n"
            "📌 Gửi ĐỊA CHỈ VÍ + HÌNH ẢNH QR\n"
            "👉 Thiếu bot sẽ nhắc lại"
        )

    elif q.data == "edit_bank" and is_admin(uid, gid):
        g["tmp_bank"].clear()
        context.user_data["await_bank"] = True
        await q.message.reply_text(
            "🏦 CẬP NHẬT SỐ TÀI KHOẢN\n\n"
            "📌 Gửi:\n• STK\n• Tên chủ TK\n• Ngân hàng"
        )

    # ===== CONFIRM =====
    elif q.data == "confirm_wallet":
        g["wallet"] = g["tmp_wallet"]["text"]
        g["wallet_img"] = g["tmp_wallet"]["img"]
        g["tmp_wallet"].clear()
        await q.message.reply_text("✅ Đã lưu ví USDT (TRC20)")

    elif q.data == "confirm_bank":
        g["bank"] = g["tmp_bank"]["text"]
        g["tmp_bank"].clear()
        await q.message.reply_text("✅ Đã lưu số tài khoản")

    elif q.data.startswith("cancel_"):
        key = q.data.split("_")[1]
        g[f"tmp_{key}"].clear()
        await q.message.reply_text("❌ Đã hủy")

# ================= MESSAGE =================
async def msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    gid = msg.chat.id
    uid = msg.from_user.id
    g = get_group(gid)
    text = (msg.text or "").strip()

    # ===== AUTO REPLY =====
    low = text.lower()
    if any(k in low for k in ["ví", "trc20"]) and g["wallet"]:
        await msg.reply_photo(
            photo=g["wallet_img"],
            caption=f"💳 VÍ USDT (TRC20)\n\n{g['wallet']}\n\n{ANTI_FAKE_VN}"
        )
        return

    if any(k in low for k in ["stk", "thanh toán"]) and g["bank"]:
        await msg.reply_text(f"{g['bank']}\n\n{ANTI_FAKE_VN}")
        return

    # ===== WALLET FLOW =====
    if context.user_data.get("await_wallet") and is_admin(uid, gid):
        if msg.photo:
            g["tmp_wallet"]["img"] = msg.photo[-1].file_id
        if msg.text:
            g["tmp_wallet"]["text"] = msg.text

        if "img" not in g["tmp_wallet"]:
            await msg.reply_text("⚠️ Bạn chưa gửi HÌNH ẢNH QR")
            return
        if "text" not in g["tmp_wallet"]:
            await msg.reply_text("⚠️ Bạn chưa gửi ĐỊA CHỈ VÍ")
            return

        context.user_data.clear()
        await msg.reply_photo(
            photo=g["tmp_wallet"]["img"],
            caption=f"🔍 XEM TRƯỚC VÍ\n\n{g['tmp_wallet']['text']}\n\n{ANTI_FAKE_VN}",
            reply_markup=confirm_menu("wallet")
        )
        return

    # ===== BANK FLOW =====
    if context.user_data.get("await_bank") and is_admin(uid, gid):
        if not text:
            return
        g["tmp_bank"]["text"] = text
        context.user_data.clear()
        await msg.reply_text(
            f"🔍 XEM TRƯỚC SỐ TÀI KHOẢN\n\n{text}\n\n{ANTI_FAKE_VN}",
            reply_markup=confirm_menu("bank")
        )

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
