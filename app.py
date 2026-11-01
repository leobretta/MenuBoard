from flask import (Flask, render_template, request, redirect, url_for,
                   flash, abort, session)
from werkzeug.security import check_password_hash
from datetime import timedelta
import hmac
import json
import os
import secrets
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MENU_FILE = os.path.join(BASE_DIR, "menu.json")
CONFIG_FILE = os.path.join(BASE_DIR, "admin_config.json")  # created by set_password.py
DEFAULT_BACKGROUND = "#FBDA04"


def read_config():
    """Admin login details (username, password hash, secret key), or {} if not set up yet."""
    try:
        with open(CONFIG_FILE) as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


app = Flask(__name__)
# The secret key signs the login cookie. It lives in admin_config.json so logins
# survive restarts; if that file doesn't exist yet we use a temporary random key.
app.secret_key = read_config().get("secret_key") or secrets.token_hex(32)
app.permanent_session_lifetime = timedelta(days=7)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"   # blocks other sites from posting to /admin
app.config["SESSION_COOKIE_HTTPONLY"] = True


def load_menu():
    with open(MENU_FILE) as file:
        return json.load(file)


def save_menu(menu):
    with open(MENU_FILE, "w") as file:
        json.dump(menu, file, indent=4)


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

    background = menu.get("background_color", DEFAULT_BACKGROUND)
    return render_template(
        "tv.html",
        sections=menu[screen]["sections"],
        business=menu["business_name"],
        background=background,
        text_color=text_color_for(background),
    )


@app.route("/tv1")
def tv1():
    return render_screen("tv1")


@app.route("/tv2")
def tv2():
    return render_screen("tv2")


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
        if not session.get("logged_in"):
            return redirect(url_for("admin_login"))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("logged_in"):
        return redirect(url_for("admin"))

    config = read_config()
    if not config.get("username") or not config.get("password_hash"):
        return render_template(
            "login.html",
            error="No admin login has been set up yet. On the computer running "
                  "the menu, run:  python3 set_password.py",
            setup_needed=True,
        )

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
    )


@app.route("/admin/business", methods=["POST"])
def admin_update_business():
    menu = load_menu()
    menu["business_name"] = request.form.get("business_name", "").strip()
    save_menu(menu)
    flash("Business name updated.")
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


if __name__ == "__main__":
    app.run(debug=True)