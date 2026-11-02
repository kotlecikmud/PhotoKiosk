"""Admin HTML templates for PhotoKiosk administration interface."""

BASE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{% block title %}PhotoKiosk Admin{% endblock %}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    :root {
      --bg: #12141a;
      --card-bg: #1c202a;
      --border: #2e3547;
      --primary: #3b82f6;
      --primary-hover: #2563eb;
      --danger: #ef4444;
      --success: #10b981;
      --text: #f3f4f6;
      --text-muted: #9ca3af;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    body { background-color: var(--bg); color: var(--text); display: flex; min-height: 100vh; }
    .sidebar { width: 260px; background-color: var(--card-bg); border-right: 1px solid var(--border); padding: 24px; display: flex; flex-direction: column; gap: 8px; }
    .sidebar h2 { font-size: 20px; color: var(--primary); margin-bottom: 24px; }
    .sidebar a { color: var(--text-muted); text-decoration: none; padding: 12px 16px; border-radius: 8px; font-weight: 500; transition: all 0.2s; }
    .sidebar a:hover, .sidebar a.active { background-color: var(--primary); color: white; }
    .content { flex: 1; padding: 36px; overflow-y: auto; }
    .card { background-color: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 24px; margin-bottom: 24px; }
    .card h3 { font-size: 18px; margin-bottom: 16px; }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; }
    th, td { text-align: left; padding: 12px; border-bottom: 1px solid var(--border); }
    th { color: var(--text-muted); font-size: 13px; text-transform: uppercase; }
    .badge { display: inline-block; padding: 4px 8px; border-radius: 6px; font-size: 12px; font-weight: 600; }
    .badge-success { background: rgba(16, 185, 129, 0.2); color: var(--success); }
    .badge-danger { background: rgba(239, 68, 68, 0.2); color: var(--danger); }
    .badge-pending { background: rgba(245, 158, 11, 0.2); color: #f59e0b; }
    input, select, button { padding: 10px 14px; border-radius: 6px; border: 1px solid var(--border); background: #0f1218; color: white; font-size: 14px; }
    button { background: var(--primary); border: none; cursor: pointer; font-weight: 600; transition: background 0.2s; }
    button:hover { background: var(--primary-hover); }
    button.btn-danger { background: var(--danger); }
    .btn-sm { padding: 6px 10px; font-size: 12px; }
    .form-group { margin-bottom: 16px; display: flex; flex-direction: column; gap: 6px; }
    .alert { padding: 12px 16px; border-radius: 8px; margin-bottom: 20px; }
    .alert-danger { background: rgba(239, 68, 68, 0.2); border: 1px solid var(--danger); color: #fca5a5; }
    .alert-success { background: rgba(16, 185, 129, 0.2); border: 1px solid var(--success); color: #6ee7b7; }
    .grid-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
    .stat-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 20px; }
    .stat-val { font-size: 28px; font-weight: 700; color: var(--primary); margin-top: 8px; }
  </style>
</head>
<body>
  {% if is_authenticated %}
  <div class="sidebar">
    <h2>PhotoKiosk Admin</h2>
    <a href="/admin" class="{% if active_tab == 'dashboard' %}active{% endif %}">Dashboard</a>
    <a href="/admin/print-sizes" class="{% if active_tab == 'print-sizes' %}active{% endif %}">Print Sizes</a>
    <a href="/admin/orders" class="{% if active_tab == 'orders' %}active{% endif %}">Orders</a>
    <a href="/admin/files" class="{% if active_tab == 'files' %}active{% endif %}">File Manager</a>
    <a href="/admin/settings" class="{% if active_tab == 'settings' %}active{% endif %}">Settings</a>
    <div style="margin-top: auto;">
      <a href="/admin/logout" style="color: var(--danger);">Logout</a>
    </div>
  </div>
  {% endif %}
  <div class="content">
    {% block content %}{% endblock %}
  </div>
</body>
</html>
"""

LOGIN_TEMPLATE = """{% extends "base.html" %}
{% block title %}PhotoKiosk Admin — Login{% endblock %}
{% block content %}
<div style="max-width: 420px; margin: 80px auto;">
  <div class="card">
    <h2 style="margin-bottom: 8px;">Administrator Login</h2>
    <p style="color: var(--text-muted); margin-bottom: 24px; font-size: 14px;">Please authenticate to manage kiosk settings.</p>
    {% if error %}
    <div class="alert alert-danger">{{ error }}</div>
    {% endif %}
    {% if message %}
    <div class="alert alert-success">{{ message }}</div>
    {% endif %}
    <form method="POST" action="/admin/login">
      <div class="form-group">
        <label>Password</label>
        <input type="password" name="password" required autofocus placeholder="Enter administrator password">
      </div>
      <button type="submit" style="width: 100%; margin-top: 8px;">Sign In</button>
    </form>
  </div>
</div>
{% endblock %}
"""

CHANGE_PASSWORD_TEMPLATE = """{% extends "base.html" %}
{% block title %}Change Password — PhotoKiosk{% endblock %}
{% block content %}
<div style="max-width: 460px; margin: 60px auto;">
  <div class="card">
    <h3>First Login: Password Change Required</h3>
    <p style="color: var(--text-muted); font-size: 14px; margin-bottom: 20px;">For security, please set a new strong password before continuing.</p>
    {% if error %}
    <div class="alert alert-danger">{{ error }}</div>
    {% endif %}
    <form method="POST" action="/admin/change-password">
      <div class="form-group">
        <label>Current Password</label>
        <input type="password" name="current_password" required>
      </div>
      <div class="form-group">
        <label>New Password (min 8 chars)</label>
        <input type="password" name="new_password" required minlength="8">
      </div>
      <div class="form-group">
        <label>Confirm New Password</label>
        <input type="password" name="confirm_password" required minlength="8">
      </div>
      <button type="submit" style="width: 100%; margin-top: 12px;">Save New Password</button>
    </form>
  </div>
</div>
{% endblock %}
"""

DASHBOARD_TEMPLATE = """{% extends "base.html" %}
{% block title %}Dashboard — PhotoKiosk Admin{% endblock %}
{% block content %}
<h1 style="font-size: 26px; margin-bottom: 24px;">System Overview</h1>

<div class="grid-stats">
  <div class="stat-card">
    <div style="color: var(--text-muted);">Total Orders</div>
    <div class="stat-val">{{ total_orders }}</div>
  </div>
  <div class="stat-card">
    <div style="color: var(--text-muted);">Completed Orders</div>
    <div class="stat-val" style="color: var(--success);">{{ completed_orders }}</div>
  </div>
  <div class="stat-card">
    <div style="color: var(--text-muted);">Disk Available</div>
    <div class="stat-val" style="font-size: 22px;">{{ disk_free_gb }} GB</div>
  </div>
  <div class="stat-card">
    <div style="color: var(--text-muted);">Active Print Sizes</div>
    <div class="stat-val">{{ active_sizes }}</div>
  </div>
</div>

<div class="card">
  <h3>Recent Orders</h3>
  <table>
    <thead>
      <tr>
        <th>Order ID</th>
        <th>Created</th>
        <th>Prints</th>
        <th>Status</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody>
      {% for o in recent_orders %}
      <tr>
        <td><strong>{{ o.order_id }}</strong></td>
        <td>{{ o.created_at.strftime('%Y-%m-%d %H:%M') }}</td>
        <td>{{ o.total_prints }}</td>
        <td>
          <span class="badge {% if o.status == 'COMPLETED' %}badge-success{% elif o.status == 'FAILED' %}badge-danger{% else %}badge-pending{% endif %}">
            {{ o.status }}
          </span>
        </td>
        <td><a href="/admin/orders/{{ o.order_id }}" style="color: var(--primary); text-decoration: none;">View Details</a></td>
      </tr>
      {% else %}
      <tr><td colspan="5" style="text-align: center; color: var(--text-muted);">No orders recorded yet.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
"""

PRINT_SIZES_TEMPLATE = """{% extends "base.html" %}
{% block title %}Print Sizes — PhotoKiosk Admin{% endblock %}
{% block content %}
<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px;">
  <h1 style="font-size: 26px;">Print Sizes & Finishes</h1>
</div>

<div class="card">
  <h3>Add New Print Size</h3>
  <form method="POST" action="/admin/print-sizes" style="display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap;">
    <div class="form-group" style="margin: 0;">
      <label>Size Name (e.g. 10x15)</label>
      <input type="text" name="name" required placeholder="10x15">
    </div>
    <div class="form-group" style="margin: 0;">
      <label>Width (mm)</label>
      <input type="number" step="0.1" name="width_mm" required placeholder="102.0">
    </div>
    <div class="form-group" style="margin: 0;">
      <label>Height (mm)</label>
      <input type="number" step="0.1" name="height_mm" required placeholder="152.0">
    </div>
    <button type="submit">Add Size</button>
  </form>
</div>

<div class="card">
  <h3>Configured Print Sizes</h3>
  <table>
    <thead>
      <tr>
        <th>Name</th>
        <th>Width (mm)</th>
        <th>Height (mm)</th>
        <th>Pixels @ 300 DPI</th>
        <th>Status</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody>
      {% for s in sizes %}
      <tr>
        <td><strong>{{ s.name }}</strong></td>
        <td>{{ s.width_mm }} mm</td>
        <td>{{ s.height_mm }} mm</td>
        <td>{{ ((s.width_mm / 25.4) * 300)|round|int }} × {{ ((s.height_mm / 25.4) * 300)|round|int }} px</td>
        <td>
          <span class="badge {% if s.is_active %}badge-success{% else %}badge-danger{% endif %}">
            {% if s.is_active %}ACTIVE{% else %}DISABLED{% endif %}
          </span>
        </td>
        <td>
          <form method="POST" action="/admin/print-sizes/{{ s.id }}/toggle" style="display: inline;">
            <button type="submit" class="btn-sm">Toggle</button>
          </form>
          <form method="POST" action="/admin/print-sizes/{{ s.id }}/delete" style="display: inline;" onsubmit="return confirm('Delete this format?');">
            <button type="submit" class="btn-sm btn-danger">Delete</button>
          </form>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
"""

ORDERS_LIST_TEMPLATE = """{% extends "base.html" %}
{% block title %}Orders — PhotoKiosk Admin{% endblock %}
{% block content %}
<h1 style="font-size: 26px; margin-bottom: 24px;">Order Management</h1>

<div class="card">
  <table>
    <thead>
      <tr>
        <th>Order ID</th>
        <th>Created At</th>
        <th>Total Items</th>
        <th>Total Prints</th>
        <th>Status</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody>
      {% for o in orders %}
      <tr>
        <td><strong>{{ o.order_id }}</strong></td>
        <td>{{ o.created_at.strftime('%Y-%m-%d %H:%M:%S') }}</td>
        <td>{{ o.total_items }}</td>
        <td>{{ o.total_prints }}</td>
        <td>
          <span class="badge {% if o.status == 'COMPLETED' %}badge-success{% elif o.status == 'FAILED' %}badge-danger{% else %}badge-pending{% endif %}">
            {{ o.status }}
          </span>
        </td>
        <td>
          <a href="/admin/orders/{{ o.order_id }}" style="color: var(--primary); text-decoration: none; font-weight: 600;">Inspect</a>
        </td>
      </tr>
      {% else %}
      <tr><td colspan="6" style="text-align: center; color: var(--text-muted);">No orders found.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
"""

ORDER_DETAIL_TEMPLATE = """{% extends "base.html" %}
{% block title %}Order {{ order.order_id }} — PhotoKiosk Admin{% endblock %}
{% block content %}
<div style="margin-bottom: 20px;">
  <a href="/admin/orders" style="color: var(--text-muted); text-decoration: none;">&larr; Back to Orders</a>
</div>

<h1 style="font-size: 24px; margin-bottom: 12px;">Order {{ order.order_id }}</h1>
<div style="margin-bottom: 24px;">
  <span class="badge {% if order.status == 'COMPLETED' %}badge-success{% elif order.status == 'FAILED' %}badge-danger{% else %}badge-pending{% endif %}">
    {{ order.status }}
  </span>
  <span style="color: var(--text-muted); margin-left: 12px;">Created: {{ order.created_at.strftime('%Y-%m-%d %H:%M:%S') }} UTC</span>
</div>

{% if order.error_message %}
<div class="alert alert-danger">
  <strong>Failure Diagnosis:</strong> {{ order.error_message }}
</div>
{% endif %}

<div class="card">
  <h3>Ordered Print Items</h3>
  <table>
    <thead>
      <tr>
        <th>Original File</th>
        <th>Format</th>
        <th>Finish</th>
        <th>Crop Mode</th>
        <th>Quantity</th>
        <th>Generated Output Path</th>
      </tr>
    </thead>
    <tbody>
      {% for item in order.items %}
      <tr>
        <td><strong>{{ item.original_filename }}</strong></td>
        <td>{{ item.print_size_name }}</td>
        <td>{{ item.paper_finish }}</td>
        <td>{{ item.crop_mode }}</td>
        <td>x{{ item.quantity }}</td>
        <td style="font-family: monospace; font-size: 12px; color: var(--text-muted);">{{ item.output_rel_path }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>

{% if manifest_content %}
<div class="card">
  <h3>Order Manifest (order.json)</h3>
  <pre style="background: #0d1017; padding: 16px; border-radius: 8px; overflow-x: auto; font-size: 13px; color: #a5b4fc;">{{ manifest_content }}</pre>
</div>
{% endif %}
{% endblock %}
"""

SETTINGS_TEMPLATE = """{% extends "base.html" %}
{% block title %}System Settings — PhotoKiosk Admin{% endblock %}
{% block content %}
<h1 style="font-size: 26px; margin-bottom: 24px;">System Settings</h1>

{% if success_msg %}
<div class="alert alert-success">{{ success_msg }}</div>
{% endif %}

<div class="card">
  <h3>Print & Image Processing Configuration</h3>
  <form method="POST" action="/admin/settings" style="max-width: 500px;">
    <div class="form-group">
      <label>Default Output DPI</label>
      <input type="number" name="default_dpi" value="{{ settings.get('default_dpi', '300') }}" required>
    </div>
    <div class="form-group">
      <label>Border Width (mm)</label>
      <input type="number" step="0.5" name="border_width_mm" value="{{ settings.get('border_width_mm', '4.0') }}" required>
    </div>
    <div class="form-group">
      <label>Auto-Orientation Matching</label>
      <select name="auto_orientation_matching">
        <option value="true" {% if settings.get('auto_orientation_matching') == 'true' %}selected{% endif %}>Enabled (Match photo aspect ratio to paper)</option>
        <option value="false" {% if settings.get('auto_orientation_matching') == 'false' %}selected{% endif %}>Disabled (Keep paper orientation fixed)</option>
      </select>
    </div>
    <button type="submit" style="margin-top: 12px;">Save Settings</button>
  </form>
</div>

<div class="card" style="max-width: 500px;">
  <h3>Change Administrator Password</h3>
  <form method="POST" action="/admin/change-password">
    <div class="form-group">
      <label>Current Password</label>
      <input type="password" name="current_password" required>
    </div>
    <div class="form-group">
      <label>New Password (min 8 chars)</label>
      <input type="password" name="new_password" required minlength="8">
    </div>
    <div class="form-group">
      <label>Confirm New Password</label>
      <input type="password" name="confirm_password" required minlength="8">
    </div>
    <button type="submit" style="margin-top: 12px;">Update Password</button>
  </form>
</div>
{% endblock %}
"""

FILE_MANAGER_TEMPLATE = """{% extends "base.html" %}
{% block title %}File Manager — PhotoKiosk Admin{% endblock %}
{% block content %}
<h1 style="font-size: 26px; margin-bottom: 24px;">Storage & File Manager</h1>

<div class="card">
  <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
    <h3>Shared Storage Directory ({{ current_path }})</h3>
  </div>
  <table>
    <thead>
      <tr>
        <th>Name</th>
        <th>Type</th>
        <th>Size</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody>
      {% for f in files %}
      <tr>
        <td><strong>{{ f.name }}</strong></td>
        <td>{{ f.type }}</td>
        <td>{{ f.size_display }}</td>
        <td>
          <form method="POST" action="/admin/files/delete" style="display: inline;" onsubmit="return confirm('Delete this file?');">
            <input type="hidden" name="filepath" value="{{ f.rel_path }}">
            <button type="submit" class="btn-sm btn-danger">Delete</button>
          </form>
        </td>
      </tr>
      {% else %}
      <tr><td colspan="4" style="text-align: center; color: var(--text-muted);">Shared photo folder is currently empty.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
"""
