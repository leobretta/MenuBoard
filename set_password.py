"""Set (or change) the username and password for the /admin page.

Run it from the project folder:   python3 set_password.py
The login is stored, hashed, in admin_config.json next to this file.
"""
import getpass
import json
import os
import secrets

from werkzeug.security import generate_password_hash

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin_config.json")


def main():
    config = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as file:
            config = json.load(file)
        print("An admin login already exists. Continuing will replace it.\n")

    default_user = config.get("username", "admin")
    username = input(f"Username [{default_user}]: ").strip() or default_user

    while True:
        password = getpass.getpass("Password (at least 8 characters): ")
        if len(password) < 8:
            print("That's too short, please try again.")
            continue
        if password != getpass.getpass("Confirm password: "):
            print("Those didn't match, please try again.")
            continue
        break

    config["username"] = username
    config["password_hash"] = generate_password_hash(password)
    config.setdefault("secret_key", secrets.token_hex(32))

    # Only the owner of the file can read it.
    fd = os.open(CONFIG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(config, file, indent=4)

    print(f"\nSaved. You can now log in at /admin as '{username}'.")
    print("If the menu server is already running, restart it once for the change to be fully applied:")
    print("    sudo systemctl restart menu-board    (on the Raspberry Pi)")


if __name__ == "__main__":
    main()
