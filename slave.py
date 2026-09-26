import os
import html
import io
import time
import base64
import threading
import requests
import telebot

from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# ============================================================
# KONFIGURASI
# ============================================================
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Kredensial KlikQRIS WAJIB disimpan di Railway Variables.
KLIQRIS_API_KEY = os.getenv("KLIQRIS_API_KEY")
KLIQRIS_MERCHANT_ID = os.getenv("KLIQRIS_MERCHANT_ID")

# API produksi KlikQRIS
KLIQRIS_BASE_URL = os.getenv("KLIQRIS_BASE_URL", "https://klikqris.com/api")

# Harga paket
PACKAGE_PRICE = 30000

# Cek status pembayaran setiap N detik
PAYMENT_CHECK_INTERVAL = int(os.getenv("PAYMENT_CHECK_INTERVAL", "10"))

# ID Grup VIP 3 Grup
VIP_GROUP_IDS = [
    -1004451939488,
    -1004486985873,
    -1003813292350
]

# ID Grup Paket Slave
SLAVE_GROUP_IDS = [
    -1003755316830
]

bot = telebot.TeleBot(TOKEN)

# {telegram_user_id: {"order_id": ..., "package": ..., "message_id": ...,
#                     "total_amount": ..., "expired_at": ..., "status": ...}}
active_transactions = {}

# Paket terakhir yang dipilih user
user_selected_package = {}


# ============================================================
# HELPER
# ============================================================
def check_config():
    missing = []
    if not TOKEN:
        missing.append("BOT_TOKEN")
    if not ADMIN_ID:
        missing.append("ADMIN_ID")
    if not KLIQRIS_API_KEY:
        missing.append("KLIQRIS_API_KEY")
    if not KLIQRIS_MERCHANT_ID:
        missing.append("KLIQRIS_MERCHANT_ID")

    if missing:
        raise RuntimeError(
            "Environment variable belum diisi: " + ", ".join(missing)
        )


def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("🛒 Tambahan Paket VIP 3 Grup (Rp 30.000)"))
    markup.add(KeyboardButton("⛓️ Paket Slave (Rp 30.000)"))
    markup.add(KeyboardButton("⭐ Testimoni"), KeyboardButton("❓ Bantuan"))
    markup.add(KeyboardButton("📞 Hubungi Admin"))
    return markup


def package_info(pkg_code):
    if pkg_code == "vip":
        return "Tambahan Paket VIP 3 Grup", VIP_GROUP_IDS
    return "Paket Slave", SLAVE_GROUP_IDS


def create_order_id(user_id):
    # Harus unik di KlikQRIS.
    return f"WD-{int(time.time())}-{user_id}"


def klikqris_headers():
    return {
        "Content-Type": "application/json",
        "x-api-key": KLIQRIS_API_KEY,
        "id_merchant": KLIQRIS_MERCHANT_ID,
    }


def create_klikqris_transaction(order_id, pkg_label):
    """
    Membuat QRIS dinamis melalui KlikQRIS.
    Endpoint: POST /qris/create
    """
    url = f"{KLIQRIS_BASE_URL}/qris/create"

    payload = {
        "order_id": order_id,
        "id_merchant": KLIQRIS_MERCHANT_ID,
        "amount": PACKAGE_PRICE,
        "keterangan": f"Pembayaran {pkg_label}",
    }

    response = requests.post(
        url,
        json=payload,
        headers=klikqris_headers(),
        timeout=30,
    )

    try:
        result = response.json()
    except Exception:
        raise RuntimeError(
            f"KlikQRIS mengembalikan response bukan JSON "
            f"(HTTP {response.status_code}): {response.text[:500]}"
        )

    if response.status_code != 200 or not result.get("status"):
        raise RuntimeError(
            f"KlikQRIS HTTP {response.status_code}: "
            f"{result.get('message', result)}"
        )

    data = result.get("data") or {}
    if not data.get("order_id"):
        raise RuntimeError(f"Response KlikQRIS tidak memiliki order_id: {result}")

    return data


def check_klikqris_status(order_id):
    """
    Cek status transaksi:
    GET /qris/status/{order_id}
    """
    url = f"{KLIQRIS_BASE_URL}/qris/status/{order_id}"

    response = requests.get(
        url,
        headers=klikqris_headers(),
        timeout=20,
    )

    try:
        result = response.json()
    except Exception:
        raise RuntimeError(
            f"Response status bukan JSON (HTTP {response.status_code})"
        )

    if response.status_code != 200 or not result.get("status"):
        raise RuntimeError(
            f"KlikQRIS status HTTP {response.status_code}: "
            f"{result.get('message', result)}"
        )

    return result.get("data") or {}


def decode_qris_image(data):
    """
    KlikQRIS dapat mengembalikan:
      qris_image = data:image/png;base64,...
    atau qris_url = URL gambar.
    """
    qris_image = data.get("qris_image")

    if qris_image:
        if qris_image.startswith("data:image"):
            try:
                encoded = qris_image.split(",", 1)[1]
                return io.BytesIO(base64.b64decode(encoded))
            except Exception as e:
                raise RuntimeError(f"Gagal decode qris_image: {e}")

    qris_url = data.get("qris_url")
    if qris_url:
        r = requests.get(qris_url, timeout=30)
        r.raise_for_status()
        return io.BytesIO(r.content)

    raise RuntimeError("KlikQRIS tidak mengembalikan qris_image atau qris_url.")


def send_payment_qr(chat_id, pkg_code):
    pkg_label, _ = package_info(pkg_code)
    order_id = create_order_id(chat_id)

    data = create_klikqris_transaction(order_id, pkg_label)

    total_amount = data.get("total_amount", PACKAGE_PRICE)
    expired_at = data.get("expired_at", "-")

    qr_file = decode_qris_image(data)
    qr_file.seek(0)

    caption = (
        f"💳 <b>{html.escape(pkg_label)}</b>\n\n"
        f"Nominal pembayaran: <b>Rp {int(float(total_amount)):,.0f}</b>\n"
        f"Order ID: <code>{html.escape(str(order_id))}</code>\n"
        f"Expired: <b>{html.escape(str(expired_at))}</b>\n\n"
        "Scan QRIS di atas menggunakan aplikasi pembayaran Anda.\n"
        "Setelah pembayaran berhasil, bot akan memverifikasi otomatis.\n\n"
        "⚠️ Bayar sesuai nominal yang tertera pada QRIS."
    )

    sent = bot.send_photo(
        chat_id,
        qr_file,
        caption=caption,
        parse_mode="HTML"
    )

    active_transactions[chat_id] = {
        "order_id": order_id,
        "package": pkg_code,
        "message_id": sent.message_id,
        "total_amount": total_amount,
        "expired_at": expired_at,
        "status": "PENDING",
    }

    return data


def cleanup_payment_message(user_id):
    tx = active_transactions.get(user_id)
    if not tx:
        return

    try:
        bot.delete_message(
            chat_id=user_id,
            message_id=tx["message_id"]
        )
    except Exception:
        pass


def deliver_package(user_id, pkg_code, order_id):
    pkg_label, target_groups = package_info(pkg_code)

    generated_links = []

    for group_id in target_groups:
        invite = bot.create_chat_invite_link(
            chat_id=group_id,
            member_limit=1
        )
        generated_links.append(invite.invite_link)

    links_text = "\n".join(
        [f"• {link}" for link in generated_links]
    )

    bot.send_message(
        user_id,
        f"✅ <b>Pembayaran Berhasil!</b>\n\n"
        f"Paket: <b>{html.escape(pkg_label)}</b>\n"
        f"Order ID: <code>{html.escape(order_id)}</code>\n\n"
        f"Berikut link akses Anda:\n\n"
        f"{links_text}\n\n"
        f"<b>Catatan:</b>\n"
        f"- Setiap link hanya dapat digunakan 1 kali.\n"
        f"- Jangan bagikan link kepada orang lain.",
        parse_mode="HTML"
    )


def payment_monitor():
    """
    Background monitor.
    Mengecek transaksi PENDING dan otomatis memberikan akses
    ketika status KlikQRIS menjadi SUCCESS/PAID.
    """
    while True:
        time.sleep(PAYMENT_CHECK_INTERVAL)

        for user_id, tx in list(active_transactions.items()):
            try:
                if tx.get("status") != "PENDING":
                    continue

                data = check_klikqris_status(tx["order_id"])
                status = str(data.get("status", "")).upper()

                if status in ("SUCCESS", "PAID"):
                    tx["status"] = "SUCCESS"

                    try:
                        deliver_package(
                            user_id,
                            tx["package"],
                            tx["order_id"]
                        )
                        cleanup_payment_message(user_id)
                    except Exception as delivery_error:
                        # Jangan menghapus transaksi jika pembuatan link gagal.
                        print(
                            f"[DELIVERY ERROR] user={user_id} "
                            f"order={tx['order_id']}: {delivery_error}"
                        )
                        continue

                    del active_transactions[user_id]

                elif status == "EXPIRED":
                    tx["status"] = "EXPIRED"

                    bot.send_message(
                        user_id,
                        "⏰ <b>QRIS sudah kedaluwarsa.</b>\n\n"
                        "Silakan pilih paket kembali untuk membuat QRIS baru.",
                        parse_mode="HTML"
                    )
                    cleanup_payment_message(user_id)
                    del active_transactions[user_id]

            except Exception as e:
                print(
                    f"[STATUS CHECK ERROR] user={user_id} "
                    f"order={tx.get('order_id')}: {e}"
                )


# ============================================================
# HANDLER
# ============================================================
@bot.message_handler(commands=["start"])
def send_welcome(message):
    bot.send_chat_action(message.chat.id, "typing")
    bot.reply_to(
        message,
        "Halo! Selamat datang di bot WarungDosa.\n\n"
        "🔥 <b>Pilihan Paket Pembelian:</b>\n"
        "1. <b>Tambahan Paket VIP 3 Grup</b> — Rp 30.000\n"
        "2. <b>Paket Slave</b> — Rp 30.000\n\n"
        "QRIS yang dibuat akan <b>unik untuk setiap transaksi</b>.",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


@bot.message_handler(
    func=lambda message:
    message.text == "🛒 Tambahan Paket VIP 3 Grup (Rp 30.000)"
)
def handle_buy_vip(message):
    user_id = message.from_user.id
    user_selected_package[user_id] = "vip"

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton(
            "💳 Buat QRIS Dinamis",
            callback_data="create_qris_vip"
        )
    )

    bot.send_message(
        message.chat.id,
        "Anda memilih <b>Tambahan Paket VIP 3 Grup</b> — Rp 30.000.\n\n"
        "Klik tombol di bawah untuk membuat QRIS pembayaran baru.",
        parse_mode="HTML",
        reply_markup=markup
    )


@bot.message_handler(
    func=lambda message:
    message.text == "⛓️ Paket Slave (Rp 30.000)"
)
def handle_buy_slave(message):
    user_id = message.from_user.id
    user_selected_package[user_id] = "slave"

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton(
            "💳 Buat QRIS Dinamis",
            callback_data="create_qris_slave"
        )
    )

    bot.send_message(
        message.chat.id,
        "Anda memilih <b>Paket Slave</b> — Rp 30.000.\n\n"
        "Klik tombol di bawah untuk membuat QRIS pembayaran baru.",
        parse_mode="HTML",
        reply_markup=markup
    )


@bot.message_handler(func=lambda message: message.text == "⭐ Testimoni")
def handle_testimoni(message):
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton(
            "🔗 Buka Channel Testimoni",
            url="https://t.me/testiwarungdosaa"
        )
    )

    bot.send_message(
        message.chat.id,
        "⭐ <b>Testimoni Pelanggan WarungDosa</b>\n\n"
        "Silakan klik tombol di bawah untuk melihat testimoni.",
        parse_mode="HTML",
        reply_markup=markup
    )


@bot.message_handler(func=lambda message: message.text == "❓ Bantuan")
def handle_faq(message):
    bot.reply_to(
        message,
        "💡 <b>Panduan:</b>\n\n"
        "1. Pilih paket.\n"
        "2. Klik <b>Buat QRIS Dinamis</b>.\n"
        "3. Scan QRIS dan bayar sesuai nominal yang tampil.\n"
        "4. Bot mengecek pembayaran otomatis.\n"
        "5. Setelah sukses, link grup dikirim otomatis.\n\n"
        "Tidak perlu kirim screenshot bukti transfer.",
        parse_mode="HTML"
    )


@bot.message_handler(func=lambda message: message.text == "📞 Hubungi Admin")
def handle_contact_admin(message):
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton(
            "💬 Chat Admin Sekarang",
            url="https://t.me/WarungDosa"
        )
    )

    bot.send_message(
        message.chat.id,
        "💬 Silakan hubungi Admin jika mengalami kendala pembayaran.",
        reply_markup=markup
    )


@bot.callback_query_handler(
    func=lambda call:
    call.data in ["create_qris_vip", "create_qris_slave"]
)
def process_create_qris(call):
    user_id = call.from_user.id
    pkg_code = "vip" if call.data == "create_qris_vip" else "slave"

    bot.answer_callback_query(
        call.id,
        "Membuat QRIS dinamis..."
    )

    # Jika masih ada transaksi pending, jangan membuat transaksi
    # baru secara tidak sengaja.
    existing = active_transactions.get(user_id)
    if existing and existing.get("status") == "PENDING":
        bot.send_message(
            user_id,
            "⚠️ Anda masih memiliki pembayaran yang menunggu.\n\n"
            f"Order ID: <code>{html.escape(existing['order_id'])}</code>\n"
            f"Nominal: <b>Rp {int(float(existing['total_amount'])):,.0f}</b>\n"
            f"Expired: <b>{html.escape(str(existing['expired_at']))}</b>",
            parse_mode="HTML"
        )
        return

    try:
        send_payment_qr(user_id, pkg_code)

    except Exception as e:
        print(f"[CREATE QR ERROR] {e}")
        bot.send_message(
            user_id,
            "❌ Gagal membuat QRIS dinamis.\n\n"
            f"Detail: <code>{html.escape(str(e))}</code>\n\n"
            "Silakan coba lagi atau hubungi admin.",
            parse_mode="HTML"
        )


# ============================================================
# START
# ============================================================
if __name__ == "__main__":
    check_config()

    print("Bot QRIS Dinamis KlikQRIS berjalan...")

    # Monitor pembayaran di background.
    monitor_thread = threading.Thread(
        target=payment_monitor,
        daemon=True
    )
    monitor_thread.start()

    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30
    )
