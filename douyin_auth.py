"""Windows-user-bound Douyin login storage; never log cookie values."""
from secret_store import SECRET_DIR, protect, unprotect


COOKIE_PATH = SECRET_DIR / "douyin-live-cookie.dpapi"


def cookie_items(header):
    result = {}
    if "\r" in str(header) or "\n" in str(header):
        raise ValueError("抖音登录信息格式无效")
    for part in str(header or "").split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key and value:
            result[key] = value
    return result


def merge_cookie(existing, login):
    return "; ".join(f"{key}={value}" for key, value in {
        **cookie_items(existing), **cookie_items(login)}.items())


def has_login(header):
    values = cookie_items(header)
    return bool(values.get("sessionid") or values.get("sessionid_ss"))


def load_cookie():
    try:
        value = unprotect(COOKIE_PATH)
        return value if has_login(value) else ""
    except (OSError, ValueError):
        return ""


def save_cookie(header):
    if not has_login(header):
        raise ValueError("尚未完成抖音登录")
    protect(merge_cookie("", header), COOKIE_PATH)


def clear_cookie():
    COOKIE_PATH.unlink(missing_ok=True)


def douyin_cookie_header(cookies):
    """Take only Douyin cookies from the dedicated login browser."""
    selected = {}
    for cookie in cookies:
        domain = str(cookie.get("domain") or "").lstrip(".").lower()
        if domain != "douyin.com" and not domain.endswith(".douyin.com"):
            continue
        name, value = str(cookie.get("name") or ""), str(cookie.get("value") or "")
        if name and value and not any(char in name + value for char in "\r\n;"):
            selected[name] = value
    return "; ".join(f"{key}={value}" for key, value in selected.items())
