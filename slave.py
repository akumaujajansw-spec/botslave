import os
import html
import io
import time
import base64
import threading
import requests
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")
KLIQRIS_API_KEY = os.getenv("KLIQRIS_API_KEY")
KLIQRIS_MERCHANT_ID = os.getenv("KLIQRIS_MERCHANT_ID")
KLIQRIS_BASE_URL = os.getenv("KLIQRIS_BASE_URL", "https://klikqris.com/api")
PACKAGE_PRICE = 30000
PAYMENT_CHECK_INTERVAL = int(os.getenv("PAYMENT_CHECK_INTERVAL", "10"))

VIP_GROUP_IDS = [-1004451939488, -1004486985873, -1003813292350]
SLAVE_GROUP_IDS = [-1003755316830]

try:
    ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW else 0
except ValueError:
    ADMIN_ID = 0

bot = telebot.TeleBot(TOKEN) if TOKEN else None
active_transactions = {}
user_selected_package = {}

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
        raise RuntimeError("Environment variable belum diisi: " + ", ".join(missing))

def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("🛒 Tambahan Paket VIP 3 Grup (Rp 30.000)"))
    markup.add(KeyboardButton("⛓️ Paket Slave (Rp 30.000)"))
    markup.add(KeyboardButton("⭐ Testimoni"), KeyboardButton("❓ Bantuan"))
    markup.add(KeyboardButton("📞 Hubungi Admin"))
    return markup

def package_info(pkg_code):
    return ("Tambahan Paket VIP 3 Grup", VIP_GROUP_IDS) if pkg_code == "vip" else ("Paket Slave", SLAVE_GROUP_IDS)

def klikqris_headers():
    return {"Content-Type": "application/json", "x-api-key": KLIQRIS_API_KEY, "id_merchant": KLIQRIS_MERCHANT_ID}

def create_order_id(user_id):
    return f"WD-{int(time.time())}-{user_id}"

def create_klikqris_transaction(order_id, pkg_label):
    url = f"{KLIQRIS_BASE_URL}/qris/create"
    payload = {
        "order_id": order_id,
        "id_merchant": KLIQRIS_MERCHANT_ID,
        "amount": PACKAGE_PRICE,
        "keterangan": f"Pembayaran {pkg_label}",
    }
    response = requests.post(url, json=payload, headers=klikqris_headers(), timeout=30)

    try:
        result = response.json()
    except Exception:
        raise RuntimeError(f"KlikQRIS HTTP {response.status_code}, response bukan JSON: {response.text[:500]}")

    # KlikQRIS berhasil membuat transaksi dengan HTTP 201.
    if not (200 <= response.status_code < 300):
        raise RuntimeError(f"KlikQRIS HTTP {response.status_code}: {result.get('message', result)}")

    if result.get("status") is False:
        raise RuntimeError(f"KlikQRIS HTTP {response.status_code}: {result.get('message', result)}")

    data = result.get("data") or {}
    if not data:
        data = {k: result.get(k) for k in ("order_id", "qris_image", "qris_url", "total_amount", "expired_at", "status") if result.get(k) is not None}

    if not data:
        raise RuntimeError(f"KlikQRIS berhasil membuat transaksi, tetapi data transaksi tidak ditemukan: {result}")

    data.setdefault("order_id", order_id)
    return data

def check_klikqris_status(order_id):
    url = f"{KLIQRIS_BASE_URL}/qris/status/{order_id}"
    response = requests.get(url, headers=klikqris_headers(), timeout=20)

    try:
        result = response.json()
    except Exception:
        raise RuntimeError(f"KlikQRIS status HTTP {response.status_code}, response bukan JSON.")

    if not (200 <= response.status_code < 300):
        raise RuntimeError(f"KlikQRIS status HTTP {response.status_code}: {result.get('message', result)}")

    if result.get("status") is False:
        raise RuntimeError(f"KlikQRIS status: {result.get('message', result)}")

    data = result.get("data") or {}
    if not data:
        data = {k: result.get(k) for k in ("order_id", "status", "payment_status", "transaction_status", "expired_at", "paid_at") if result.get(k) is not None}
    return data

def decode_qris_image(data):
    qris_image = data.get("qris_image")
    if qris_image:
        if isinstance(qris_image, str) and qris_image.startswith("data:image"):
            try:
                return io.BytesIO(base64.b64decode(qris_image.split(",", 1)[1]))
            except Exception as exc:
                raise RuntimeError(f"Gagal decode qris_image: {exc}")
        if isinstance(qris_image, str):
            try:
                return io.BytesIO(base64.b64decode(qris_image))
            except Exception:
                pass

    qris_url = data.get("qris_url")
    if qris_url:
        try:
            response = requests.get(qris_url, timeout=30)
            response.raise_for_status()
            return io.BytesIO(response.content)
        except Exception as exc:
            raise RuntimeError(f"Gagal mengambil qris_url: {exc}")

    raise RuntimeError(f"KlikQRIS tidak mengembalikan qris_image atau qris_url. Data: {data}")

def send_payment_qr(user_id, pkg_code):
    pkg_label, _ = package_info(pkg_code)
    order_id = create_order_id(user_id)
    data = create_klikqris_transaction(order_id, pkg_label)
    total_amount = data.get("total_amount", PACKAGE_PRICE)
    expired_at = data.get("expired_at", "-")

    try:
        amount_display = f"Rp {int(float(total_amount)):,.0f}"
    except Exception:
        amount_display = f"Rp {total_amount}"

    qr_file = decode_qris_image(data)
    qr_file.seek(0)

    caption = (
        f"💳 <b>{html.escape(pkg_label)}</b>\n\n"
        f"Nominal: <b>{amount_display}</b>\n"
        f"Order ID: <code>{html.escape(str(order_id))}</code>\n"
        f"Expired: <b>{html.escape(str(expired_at))}</b>\n\n"
        "Silakan scan QRIS di atas dan bayar sesuai nominal yang tertera.\n\n"
        "✅ Setelah pembayaran berhasil, bot akan mengecek pembayaran secara otomatis.\n"
        "❌ Tidak perlu mengirim screenshot bukti transfer."
    )
    sent = bot.send_photo(user_id, qr_file, caption=caption, parse_mode="HTML")

    active_transactions[user_id] = {
        "order_id": order_id,
        "package": pkg_code,
        "message_id": sent.message_id,
        "total_amount": total_amount,
        "expired_at": expired_at,
        "status": "PENDING",
    }

def cleanup_payment_message(user_id):
    transaction = active_transactions.get(user_id)
    if not transaction:
        return
    try:
        bot.delete_message(chat_id=user_id, message_id=transaction["message_id"])
    except Exception:
        pass

def deliver_package(user_id, pkg_code, order_id):
    pkg_label, target_groups = package_info(pkg_code)
    generated_links = []
    for group_id in target_groups:
        invite = bot.create_chat_invite_link(chat_id=group_id, member_limit=1)
        generated_links.append(invite.invite_link)

    links_text = "\n".join(f"• {link}" for link in generated_links)
    bot.send_message(
        user_id,
        f"✅ <b>Pembayaran Berhasil!</b>\n\n"
        f"Paket: <b>{html.escape(pkg_label)}</b>\n"
        f"Order ID: <code>{html.escape(str(order_id))}</code>\n\n"
        f"Berikut link akses Anda:\n\n{links_text}\n\n"
        "<b>Catatan:</b>\n- Setiap link hanya dapat digunakan 1 kali.\n- Jangan bagikan link kepada orang lain.",
        parse_mode="HTML",
    )

def normalize_payment_status(data):
    for value in (data.get("status"), data.get("payment_status"), data.get("transaction_status")):
        if value is not None:
            return str(value).strip().upper()
    return ""

def payment_monitor():
    while True:
        time.sleep(PAYMENT_CHECK_INTERVAL)
        for user_id, transaction in list(active_transactions.items()):
            if transaction.get("status") != "PENDING":
                continue
            order_id = transaction["order_id"]
            try:
                data = check_klikqris_status(order_id)
                status = normalize_payment_status(data)
                print(f"[KLIQRIS] order={order_id} status={status or 'UNKNOWN'}")

                if status in {"SUCCESS", "PAID", "SETTLED", "COMPLETED", "BERHASIL"}:
                    try:
                        deliver_package(user_id, transaction["package"], order_id)
                    except Exception as delivery_error:
                        print(f"[DELIVERY ERROR] user={user_id} order={order_id}: {delivery_error}")
                        continue
                    cleanup_payment_message(user_id)
                    active_transactions.pop(user_id, None)

                elif status in {"EXPIRED", "CANCELLED", "CANCELED"}:
                    bot.send_message(user_id, "⏰ <b>QRIS sudah kedaluwarsa.</b>\n\nSilakan pilih paket kembali untuk membuat QRIS baru.", parse_mode="HTML")
                    cleanup_payment_message(user_id)
                    active_transactions.pop(user_id, None)

            except Exception as exc:
                print(f"[STATUS CHECK ERROR] user={user_id} order={order_id}: {exc}")

@bot.message_handler(commands=["start"])
def send_welcome(message):
    bot.send_chat_action(message.chat.id, "typing")
    bot.reply_to(
        message,
        "Halo! Selamat datang di bot WarungDosa.\n\n"
        "🔥 <b>Pilihan Paket Pembelian:</b>\n"
        "1. <b>Tambahan Paket VIP 3 Grup</b> — Rp 30.000\n"
        "2. <b>Paket Slave</b> — Rp 30.000\n\n"
        "QRIS akan dibuat dinamis untuk setiap transaksi.",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )

@bot.message_handler(func=lambda message: message.text == "🛒 Tambahan Paket VIP 3 Grup (Rp 30.000)")
def handle_buy_vip(message):
    user_selected_package[message.from_user.id] = "vip"
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("💳 Buat QRIS Dinamis", callback_data="create_qris_vip"))
    bot.send_message(message.chat.id, "Anda memilih <b>Tambahan Paket VIP 3 Grup</b> — Rp 30.000.\n\nKlik tombol di bawah untuk membuat QRIS pembayaran baru.", parse_mode="HTML", reply_markup=markup)

@bot.message_handler(func=lambda message: message.text == "⛓️ Paket Slave (Rp 30.000)")
def handle_buy_slave(message):
    user_selected_package[message.from_user.id] = "slave"
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("💳 Buat QRIS Dinamis", callback_data="create_qris_slave"))
    bot.send_message(message.chat.id, "Anda memilih <b>Paket Slave</b> — Rp 30.000.\n\nKlik tombol di bawah untuk membuat QRIS pembayaran baru.", parse_mode="HTML", reply_markup=markup)

@bot.message_handler(func=lambda message: message.text == "⭐ Testimoni")
def handle_testimoni(message):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔗 Buka Channel Testimoni", url="https://t.me/testiwarungdosaa"))
    bot.send_message(message.chat.id, "⭐ <b>Testimoni Pelanggan WarungDosa</b>\n\nSilakan klik tombol di bawah untuk melihat testimoni.", parse_mode="HTML", reply_markup=markup)

@bot.message_handler(func=lambda message: message.text == "❓ Bantuan")
def handle_faq(message):
    bot.reply_to(message, "💡 <b>Panduan:</b>\n\n1. Pilih paket.\n2. Klik <b>Buat QRIS Dinamis</b>.\n3. Scan QRIS dan bayar sesuai nominal yang tampil.\n4. Bot mengecek pembayaran otomatis.\n5. Setelah sukses, link grup dikirim otomatis.\n\nTidak perlu mengirim screenshot bukti transfer.", parse_mode="HTML")

@bot.message_handler(func=lambda message: message.text == "📞 Hubungi Admin")
def handle_contact_admin(message):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("💬 Chat Admin Sekarang", url="https://t.me/WarungDosa"))
    bot.send_message(message.chat.id, "💬 Silakan hubungi Admin jika mengalami kendala pembayaran.", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data in ["create_qris_vip", "create_qris_slave"])
def process_create_qris(call):
    user_id = call.from_user.id
    pkg_code = "vip" if call.data == "create_qris_vip" else "slave"
    bot.answer_callback_query(call.id, "Membuat QRIS dinamis...")

    existing = active_transactions.get(user_id)
    if existing and existing.get("status") == "PENDING":
        try:
            amount = f"Rp {int(float(existing['total_amount'])):,.0f}"
        except Exception:
            amount = str(existing["total_amount"])
        bot.send_message(
            user_id,
            "⚠️ Anda masih memiliki pembayaran yang menunggu.\n\n"
            f"Order ID: <code>{html.escape(str(existing['order_id']))}</code>\n"
            f"Nominal: <b>{amount}</b>\n"
            f"Expired: <b>{html.escape(str(existing['expired_at']))}</b>",
            parse_mode="HTML",
        )
        return

    try:
        send_payment_qr(user_id, pkg_code)
    except Exception as exc:
        print(f"[CREATE QR ERROR] {exc}")
        bot.send_message(
            user_id,
            "❌ <b>Gagal membuat QRIS.</b>\n\n"
            f"<code>{html.escape(str(exc))}</code>\n\n"
            "Silakan coba lagi atau hubungi admin.",
            parse_mode="HTML",
        )

if __name__ == "__main__":
    check_config()
    print("Bot QRIS Dinamis KlikQRIS berjalan...")
    threading.Thread(target=payment_monitor, daemon=True).start()
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
