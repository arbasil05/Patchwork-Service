import bcrypt
import getpass
import hmac

def main():
    print("This script will generate a secure bcrypt hash for the admin password.")
    password = getpass.getpass("Enter plaintext password: ")
    confirm  = getpass.getpass("Confirm password: ")

    # Use constant-time comparison even in this offline context — keeps the
    # codebase consistent and avoids any timing leak in future refactors.
    if not hmac.compare_digest(password.encode("utf-8"), confirm.encode("utf-8")):
        print("Passwords do not match.")
        return

    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12))

    print("\n---")
    print("Add the following lines to backend/.env:\n")
    print("ADMIN_USERNAME=admin")
    print(f"ADMIN_PASSWORD_HASH={hashed.decode('utf-8')}")
    print("\n---")
    print("Keep this hash private. The plaintext password is not stored anywhere.")

if __name__ == "__main__":
    main()
