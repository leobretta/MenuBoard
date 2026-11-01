from flask import (Flask, render_template, request, redirect, url_for,
                   flash, abort, session)
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import timedelta
import copy
import hmac
import json
import os
import secrets
import socket
import subprocess
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MENU_FILE = os.path.join(BASE_DIR, "menu.json")
CONFIG_FILE = os.path.join(BASE_DIR, "admin_config.json")   # admin login, made by the setup page
SETUP_CODE_FILE = os.path.join(BASE_DIR, "setup_code.txt")  # one-time code for first-run setup
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")    # uploaded logo lives here
DEFAULT_BACKGROUND = "#F4EFE6"
MAX_LOGO_BYTES = 5 * 1024 * 1024
DEFAULT_LOGO_SIZE = 15            # logo height as a percent of the TV screen's height
MIN_LOGO_SIZE, MAX_LOGO_SIZE = 5, 40

# What a brand-new install starts with (used only if menu.json doesn't exist yet).
STARTER_MENU = {
    "business_name": "",
    "background_color": DEFAULT_BACKGROUND,
    "logo": "",
    "logo_size": DEFAULT_LOGO_SIZE,
    "tv1": {"sections": [{"title": "Menu Section",
                          "items": [{"name": "Sample Item", "price": 9.99}]}]},
    "tv2": {"sections": [{"title": "Another Section",
                          "items": [{"name": "Sample Item", "price": 4.99}]}]},
}


def read_config():
    """Admin login details (username, password hash, secret key), or {} if not set up yet."""
    try:
        with open(CONFIG_FILE) as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_private_json(path, data):
    """Write a JSON file that only the owner can read, replacing it safely."""
    tmp_path = path + ".tmp"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(data, file, indent=4)
    os.replace(tmp_path, path)


app = Flask(__name__)
# The secret key signs the login cookie. It lives in admin_config.json so logins
# survive restarts; before setup is finished we use a temporary random key.
app.secret_key = read_config().get("secret_key") or secrets.token_hex(32)
app.permanent_session_lifetime = timedelta(days=7)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"   # blocks other sites from posting to /admin
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["MAX_CONTENT_LENGTH"] = MAX_LOGO_BYTES + 512 * 1024


def load_menu():
    try:
        with open(MENU_FILE) as file:
            return json.load(file)
    except FileNotFoundError:
        menu = copy.deepcopy(STARTER_MENU)
        save_menu(menu)
        return menu


def save_menu(menu):
    # Write to a temp file first so a TV never reads a half-written menu.
    tmp_path = MENU_FILE + ".tmp"
    with open(tmp_path, "w") as file:
        json.dump(menu, file, indent=4)
    os.replace(tmp_path, MENU_FILE)


# ---------------- Logo ----------------

def detect_image_type(data):
    """Identify an image by its first bytes (not its file name). SVG is deliberately
    not allowed because SVG files can contain scripts."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def read_logo_upload(upload):
    """Check an uploaded logo. Returns (data, file_type, error_message)."""
    data = upload.read(MAX_LOGO_BYTES + 1)
    if len(data) > MAX_LOGO_BYTES:
        return None, None, "That logo is too large. Please use an image under 5 MB."
    kind = detect_image_type(data)
    if not kind:
        return None, None, "The logo must be a PNG, JPG, GIF, or WebP image."
    return data, kind, None


def remove_logo_files():
    if os.path.isdir(UPLOAD_DIR):
        for name in os.listdir(UPLOAD_DIR):
            if name.startswith("logo."):
                os.remove(os.path.join(UPLOAD_DIR, name))


def write_logo(data, kind):
    """Save the logo (replacing any old one) and return its file name."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    remove_logo_files()
    filename = f"logo.{kind}"
    with open(os.path.join(UPLOAD_DIR, filename), "wb") as file:
        file.write(data)
    return filename


def logo_size_for(menu):
    """Logo height in percent of screen height, kept within sensible limits."""
    try:
        size = int(menu.get("logo_size", DEFAULT_LOGO_SIZE))
    except (TypeError, ValueError):
        size = DEFAULT_LOGO_SIZE
    return max(MIN_LOGO_SIZE, min(MAX_LOGO_SIZE, size))


def logo_url(menu):
    """URL of the logo to show, or None. The ?v= part makes TVs fetch a new
    logo right away instead of showing a cached old one."""
    name = menu.get("logo")
    if name:
        path = os.path.join(UPLOAD_DIR, name)
        if os.path.exists(path):
            return url_for("static", filename=f"uploads/{name}", v=int(os.path.getmtime(path)))
    elif "logo" not in menu:
        # Older installs kept the logo at static/images/logo.png
        legacy = os.path.join(BASE_DIR, "static", "images", "logo.png")
        if os.path.exists(legacy):
            return url_for("static", filename="images/logo.png", v=int(os.path.getmtime(legacy)))
    return None


# ---------------- First-run setup helpers ----------------

def setup_complete():
    config = read_config()
    return bool(config.get("username") and config.get("password_hash"))


def get_setup_code():
    """A 6-digit code, kept in setup_code.txt, that proves you have access to the
    device running the menu. Needed once, to finish setup."""
    if setup_complete():
        return None
    try:
        with open(SETUP_CODE_FILE) as file:
            code = file.read().strip()
    except FileNotFoundError:
        code = ""
    if not code:
        code = f"{secrets.randbelow(10 ** 6):06d}"
        fd = os.open(SETUP_CODE_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write(code)
    return code


def lan_ip():
    """This device's address on the local network (what phones/laptops would use)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))  # nothing is actually sent
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def device_addresses():
    """Every IPv4 address this device has on the network (Ethernet and/or Wi-Fi),
    main one first. Empty if it isn't connected to a network."""
    found = []
    try:
        output = subprocess.run(["hostname", "-I"], capture_output=True,
                                text=True, timeout=2).stdout
        found = [a for a in output.split()
                 if "." in a and not a.startswith(("127.", "169.254."))]
    except (OSError, subprocess.SubprocessError):
        pass
    if not found:
        ip = lan_ip()
        if not ip.startswith("127."):
            found = [ip]
    return found


def address_urls(path):
    """Full web addresses for this device, e.g. http://192.168.1.50:5000/setup.
    Returns (list of IP-based urls, a name-based url like http://raspberrypi.local:5000/setup)."""
    port = request.environ.get("SERVER_PORT", "5000")
    ip_urls = [f"http://{ip}:{port}{path}" for ip in device_addresses()]
    name_url = f"http://{socket.gethostname()}.local:{port}{path}"
    return ip_urls, name_url


def make_qr_svg(text):
    """A QR code (as inline SVG) that opens `text` when scanned, or None if the
    optional 'segno' library isn't installed. The page works fine without it."""
    try:
        import segno
    except ImportError:
        return None
    try:
        code = segno.make(text, error="m")
        return code.svg_inline(scale=1, border=2, dark="#000000", light="#ffffff", omitsize=True)
    except Exception as error:  # never let a QR problem break the welcome screen
        print(f"Could not draw the QR code: {error}", flush=True)
        return None


def is_local_request():
    return request.remote_addr in ("127.0.0.1", "::1")


def screen_names(menu):
    """Any top-level key that holds sections is a TV screen (tv1, tv2, ...)."""
    return [key for key, value in menu.items()
            if isinstance(value, dict) and "sections" in value]


def text_color_for(background):
    """Black text on light backgrounds, white text on dark ones."""
    try:
        value = background.lstrip("#")
        r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return "#000000"
    brightness = (r * 299 + g * 587 + b * 114) / 1000
    return "#000000" if brightness > 140 else "#ffffff"


def render_screen(screen):
    menu = load_menu()
    if screen not in screen_names(menu):
        abort(404)

    if not setup_complete():
        setup_urls, name_url = address_urls("/setup")
        return render_template(
            "waiting.html",
            setup_urls=setup_urls,
            name_url=name_url,
            qr_svg=make_qr_svg(setup_urls[0]) if setup_urls else None,
            # Only the screen on the device itself shows the code; other
            # computers on the network can't see it.
            setup_code=get_setup_code() if is_local_request() else None,
        )

    background = menu.get("background_color", DEFAULT_BACKGROUND)
    return render_template(
        "tv.html",
        sections=menu[screen]["sections"],
        business=menu.get("business_name", ""),
        logo=logo_url(menu),
        logo_height=logo_size_for(menu),
        background=background,
        text_color=text_color_for(background),
    )


@app.route("/")
def index():
    return redirect(url_for("admin") if setup_complete() else url_for("setup"))


@app.route("/tv1")
def tv1():
    return render_screen("tv1")


@app.route("/tv2")
def tv2():
    return render_screen("tv2")


# ---------------- First-run setup ----------------

@app.route("/setup", methods=["GET", "POST"])
def setup():
    if setup_complete():
        return redirect(url_for("admin") if session.get("logged_in") else url_for("admin_login"))

    errors = []
    form = {"store_name": "", "username": "admin", "background_color": DEFAULT_BACKGROUND}

    if request.method == "POST":
        ip = request.remote_addr or "unknown"
        form["store_name"] = request.form.get("store_name", "").strip()
        form["username"] = request.form.get("username", "").strip()
        color = request.form.get("background_color", "").strip()
        form["background_color"] = color
        code = request.form.get("setup_code", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        logo_data = logo_kind = None

        if is_locked_out(ip):
            errors.append("Too many wrong setup codes. Please wait a few minutes and try again.")
        else:
            expected = get_setup_code() or ""
            if not hmac.compare_digest(code.encode(), expected.encode()):
                failed_logins.setdefault(ip, []).append(time.time())
                errors.append("That setup code isn't right. It's shown on the TV screen "
                              "and in the file setup_code.txt on the device running the menu.")

            if not form["store_name"]:
                errors.append("Please enter your store name.")
            if not form["username"]:
                errors.append("Please choose a username.")
            if len(password) < 8:
                errors.append("Your password needs at least 8 characters.")
            elif password != confirm:
                errors.append("The two passwords don't match.")
            if not (len(color) == 7 and color.startswith("#")
                    and all(c in "0123456789abcdefABCDEF" for c in color[1:])):
                errors.append("Please pick a valid background color.")
                form["background_color"] = DEFAULT_BACKGROUND

            upload = request.files.get("logo")
            if upload and upload.filename:
                logo_data, logo_kind, logo_error = read_logo_upload(upload)
                if logo_error:
                    errors.append(logo_error)

        if not errors:
            menu = load_menu()
            menu["business_name"] = form["store_name"]
            menu["background_color"] = form["background_color"]
            if logo_data:
                menu["logo"] = write_logo(logo_data, logo_kind)
            else:
                menu.setdefault("logo", "")
            save_menu(menu)

            # Writing the login file is what marks setup as finished, so it goes last.
            write_private_json(CONFIG_FILE, {
                "username": form["username"],
                "password_hash": generate_password_hash(password),
                "secret_key": app.secret_key,
            })
            if os.path.exists(SETUP_CODE_FILE):
                os.remove(SETUP_CODE_FILE)
            failed_logins.pop(ip, None)

            session.clear()
            session.permanent = True
            session["logged_in"] = True
            flash("Setup complete! Your menu screens are ready to fill in.")
            return redirect(url_for("admin"))

    return render_template("setup.html", errors=errors, form=form)


@app.errorhandler(413)
def file_too_large(error):
    flash("That file is too large. Please use an image under 5 MB.")
    return redirect(url_for("setup") if request.path == "/setup" else url_for("admin"))


# ---------------- Login ----------------

MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 300
failed_logins = {}  # ip address -> timestamps of recent failed attempts


def is_locked_out(ip):
    now = time.time()
    recent = [t for t in failed_logins.get(ip, []) if now - t < LOCKOUT_SECONDS]
    failed_logins[ip] = recent
    return len(recent) >= MAX_FAILED_LOGINS


@app.before_request
def require_login():
    """Everything under /admin needs a login, except the login page itself."""
    if request.path.startswith("/admin") and request.endpoint != "admin_login":
        if not setup_complete():
            return redirect(url_for("setup"))
        if not session.get("logged_in"):
            return redirect(url_for("admin_login"))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("logged_in"):
        return redirect(url_for("admin"))

    if not setup_complete():
        return redirect(url_for("setup"))

    config = read_config()

    error = None
    if request.method == "POST":
        ip = request.remote_addr or "unknown"

        if is_locked_out(ip):
            error = "Too many failed attempts. Please wait a few minutes and try again."
        else:
            username = request.form.get("username", "")
            password = request.form.get("password", "")

            username_ok = hmac.compare_digest(username.encode(), config["username"].encode())
            password_ok = check_password_hash(config["password_hash"], password)

            if username_ok and password_ok:
                failed_logins.pop(ip, None)
                session.clear()
                session.permanent = True
                session["logged_in"] = True
                return redirect(url_for("admin"))

            failed_logins.setdefault(ip, []).append(time.time())
            error = "Incorrect username or password."

    return render_template("login.html", error=error)


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


# ---------------- Admin ----------------

@app.route("/admin")
def admin():
    menu = load_menu()
    return render_template(
        "admin.html",
        menu=menu,
        screens=screen_names(menu),
        background=menu.get("background_color", DEFAULT_BACKGROUND),
        logo=logo_url(menu),
        logo_size=logo_size_for(menu),
        min_logo_size=MIN_LOGO_SIZE,
        max_logo_size=MAX_LOGO_SIZE,
        admin_urls=address_urls("/admin")[0],
        admin_name_url=address_urls("/admin")[1],
    )


@app.route("/admin/business", methods=["POST"])
def admin_update_business():
    menu = load_menu()
    menu["business_name"] = request.form.get("business_name", "").strip()
    save_menu(menu)
    flash("Business name updated.")
    return redirect(url_for("admin"))


@app.route("/admin/logo", methods=["POST"])
def admin_update_logo():
    upload = request.files.get("logo")
    if not upload or not upload.filename:
        flash("Choose an image file first.")
        return redirect(url_for("admin"))

    data, kind, error = read_logo_upload(upload)
    if error:
        flash(error)
    else:
        menu = load_menu()
        menu["logo"] = write_logo(data, kind)
        save_menu(menu)
        flash("Logo updated.")
    return redirect(url_for("admin"))


@app.route("/admin/logo/size", methods=["POST"])
def admin_update_logo_size():
    try:
        size = int(request.form.get("logo_size", ""))
    except ValueError:
        flash("That isn't a valid size.")
        return redirect(url_for("admin"))

    menu = load_menu()
    menu["logo_size"] = max(MIN_LOGO_SIZE, min(MAX_LOGO_SIZE, size))
    save_menu(menu)
    flash("Logo size updated.")
    return redirect(url_for("admin"))


@app.route("/admin/logo/remove", methods=["POST"])
def admin_remove_logo():
    menu = load_menu()
    remove_logo_files()
    menu["logo"] = ""
    save_menu(menu)
    flash("Logo removed. Your store name will be shown instead.")
    return redirect(url_for("admin"))


@app.route("/admin/background", methods=["POST"])
def admin_update_background():
    color = request.form.get("background_color", "").strip()
    valid = (
        len(color) == 7
        and color.startswith("#")
        and all(c in "0123456789abcdefABCDEF" for c in color[1:])
    )
    if valid:
        menu = load_menu()
        menu["background_color"] = color
        save_menu(menu)
        flash("Background color updated.")
    else:
        flash("That doesn't look like a valid color.")
    return redirect(url_for("admin"))


@app.route("/admin/<screen>/section/add", methods=["POST"])
def admin_add_section(screen):
    menu = load_menu()
    if screen not in screen_names(menu):
        flash("That screen doesn't exist.")
        return redirect(url_for("admin"))

    title = request.form.get("new_section_title", "").strip()
    if title:
        menu[screen]["sections"].append({"title": title, "items": []})
        save_menu(menu)
        flash(f"Added section '{title}' to {screen}.")

    return redirect(url_for("admin"))


@app.route("/admin/<screen>/section/<int:section_index>/delete", methods=["POST"])
def admin_delete_section(screen, section_index):
    menu = load_menu()
    sections = menu.get(screen, {}).get("sections", [])

    if 0 <= section_index < len(sections):
        removed = sections.pop(section_index)
        save_menu(menu)
        flash(f"Removed section '{removed['title']}'.")

    return redirect(url_for("admin"))


@app.route("/admin/<screen>/section/<int:section_index>/title", methods=["POST"])
def admin_update_section_title(screen, section_index):
    menu = load_menu()
    sections = menu.get(screen, {}).get("sections", [])

    if 0 <= section_index < len(sections):
        sections[section_index]["title"] = request.form.get("title", "").strip()
        save_menu(menu)
        flash("Section title updated.")

    return redirect(url_for("admin"))


@app.route("/admin/<screen>/section/<int:section_index>/update", methods=["POST"])
def admin_update_items(screen, section_index):
    menu = load_menu()
    sections = menu.get(screen, {}).get("sections", [])

    if not (0 <= section_index < len(sections)):
        flash("That section doesn't exist.")
        return redirect(url_for("admin"))

    items = sections[section_index]["items"]
    updated_items = []

    for index, item in enumerate(items):
        if request.form.get(f"delete_{index}"):
            continue  # skip items marked for deletion

        name = request.form.get(f"name_{index}", item["name"]).strip()
        price_raw = request.form.get(f"price_{index}", item["price"])

        try:
            price = round(float(price_raw), 2)
        except ValueError:
            price = item["price"]

        updated_items.append({"name": name, "price": price})

    sections[section_index]["items"] = updated_items
    save_menu(menu)
    flash("Items updated.")
    return redirect(url_for("admin"))


@app.route("/admin/<screen>/section/<int:section_index>/add_item", methods=["POST"])
def admin_add_item(screen, section_index):
    menu = load_menu()
    sections = menu.get(screen, {}).get("sections", [])

    if not (0 <= section_index < len(sections)):
        flash("That section doesn't exist.")
        return redirect(url_for("admin"))

    name = request.form.get("new_name", "").strip()
    price_raw = request.form.get("new_price", "0")

    if name:
        try:
            price = round(float(price_raw), 2)
        except ValueError:
            price = 0.0

        sections[section_index]["items"].append({"name": name, "price": price})
        save_menu(menu)
        flash(f"Added '{name}'.")

    return redirect(url_for("admin"))


if not setup_complete():
    _ips = device_addresses()
    print(f"\n*** FIRST-TIME SETUP ***\n"
          f"Open  http://{_ips[0] if _ips else 'this-device-address'}:5000/setup  in a browser "
          f"and enter the setup code: {get_setup_code()}\n", flush=True)


if __name__ == "__main__":
    app.run(debug=True)
