"""Write admin templates to admin/templates/ directory."""
import os
from admin.templates_code import (
    BASE_TEMPLATE,
    LOGIN_TEMPLATE,
    CHANGE_PASSWORD_TEMPLATE,
    DASHBOARD_TEMPLATE,
    PRINT_SIZES_TEMPLATE,
    ORDERS_LIST_TEMPLATE,
    ORDER_DETAIL_TEMPLATE,
    SETTINGS_TEMPLATE,
    FILE_MANAGER_TEMPLATE,
)

TEMPLATES = {
    "base.html": BASE_TEMPLATE,
    "login.html": LOGIN_TEMPLATE,
    "change_password.html": CHANGE_PASSWORD_TEMPLATE,
    "dashboard.html": DASHBOARD_TEMPLATE,
    "print_sizes.html": PRINT_SIZES_TEMPLATE,
    "orders.html": ORDERS_LIST_TEMPLATE,
    "order_detail.html": ORDER_DETAIL_TEMPLATE,
    "settings.html": SETTINGS_TEMPLATE,
    "files.html": FILE_MANAGER_TEMPLATE,
}

os.makedirs("admin/templates", exist_ok=True)
for fname, content in TEMPLATES.items():
    with open(f"admin/templates/{fname}", "w", encoding="utf-8") as f:
        f.write(content)

print("Admin templates successfully written.")
