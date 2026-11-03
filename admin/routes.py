"""FastAPI routes for Web Admin interface."""
import os
import shutil
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Request, Response, Form, Depends, HTTPException, status, Query
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from starlette.background import BackgroundTask
import tempfile
import zipfile
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from backend.database import get_db_engine, PrintSize, PaperFinish, Order, OrderItem, SystemSetting
from backend.security import verify_password, hash_password
from admin.auth import (
    create_session,
    validate_session,
    revoke_session,
    record_failed_attempt,
    record_successful_login,
    is_rate_limited,
)
from gui.theme import THEMES, get_theme_colors, set_active_theme

admin_router = APIRouter(prefix="/admin", include_in_schema=False)
templates = Jinja2Templates(directory="admin/templates")


def get_current_theme_colors(db: Session) -> dict:
    th_setting = db.query(SystemSetting).filter_by(key="ui_theme").first()
    theme_key = th_setting.value if th_setting else "midnight_blue"
    return get_theme_colors(theme_key)


def get_db():
    engine = get_db_engine()
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_admin(request: Request) -> bool:
    token = request.cookies.get("admin_session")
    return validate_session(token)


@admin_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None):
    if get_current_admin(request):
        return RedirectResponse(url="/admin", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html", context={"error": error, "is_authenticated": False})


@admin_router.post("/login")
async def handle_login(
    request: Request,
    response: Response,
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    client_ip = request.client.host if request.client else "127.0.0.1"
    is_locked, remaining = is_rate_limited(client_ip)
    if is_locked:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": f"Too many failed login attempts. Please wait {remaining} seconds.",
                "is_authenticated": False,
            },
            status_code=429,
        )

    pwd_setting = db.query(SystemSetting).filter_by(key="admin_password_hash").first()
    if not pwd_setting or not verify_password(password, pwd_setting.value):
        record_failed_attempt(client_ip)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Invalid administrator password.", "is_authenticated": False},
            status_code=400,
        )

    record_successful_login(client_ip)
    token = create_session("admin")

    must_change = db.query(SystemSetting).filter_by(key="admin_password_must_change").first()
    redirect_url = "/admin/change-password" if (must_change and must_change.value == "true") else "/admin"

    res = RedirectResponse(url=redirect_url, status_code=303)
    res.set_cookie("admin_session", token, httponly=True, samesite="lax", max_age=3600)
    return res


@admin_router.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("admin_session")
    if token:
        revoke_session(token)
    res = RedirectResponse(url="/admin/login", status_code=303)
    res.delete_cookie("admin_session")
    return res


@admin_router.get("/change-password", response_class=HTMLResponse)
async def change_password_page(request: Request):
    if not get_current_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    return templates.TemplateResponse(request=request, name="change_password.html", context={"is_authenticated": True})


@admin_router.post("/change-password")
async def handle_change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db)
):
    if not get_current_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    if new_password != confirm_password:
        return templates.TemplateResponse(
            request=request,
            name="change_password.html",
            context={"error": "New passwords do not match.", "is_authenticated": True},
            status_code=400,
        )

    if len(new_password) < 8:
        return templates.TemplateResponse(
            request=request,
            name="change_password.html",
            context={"error": "Password must be at least 8 characters.", "is_authenticated": True},
            status_code=400,
        )

    pwd_setting = db.query(SystemSetting).filter_by(key="admin_password_hash").first()
    if not pwd_setting or not verify_password(current_password, pwd_setting.value):
        return templates.TemplateResponse(
            request=request,
            name="change_password.html",
            context={"error": "Current password incorrect.", "is_authenticated": True},
            status_code=400,
        )

    pwd_setting.value = hash_password(new_password)
    must_change = db.query(SystemSetting).filter_by(key="admin_password_must_change").first()
    if must_change:
        must_change.value = "false"
    db.commit()

    return RedirectResponse(url="/admin", status_code=303)


@admin_router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    if not get_current_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    total_orders = db.query(Order).count()
    completed = db.query(Order).filter_by(status="COMPLETED").count()
    active_sizes = db.query(PrintSize).filter_by(is_active=True).count()
    recent = db.query(Order).order_by(Order.created_at.desc()).limit(10).all()

    # Disk space calculation
    disk = shutil.disk_usage("storage")
    free_gb = round(disk.free / (1024 ** 3), 1)

    theme_colors = get_current_theme_colors(db)
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "is_authenticated": True,
            "active_tab": "dashboard",
            "total_orders": total_orders,
            "completed_orders": completed,
            "active_sizes": active_sizes,
            "recent_orders": recent,
            "disk_free_gb": free_gb,
            "theme_colors": theme_colors,
        },
    )


@admin_router.get("/print-sizes", response_class=HTMLResponse)
async def list_print_sizes(request: Request, db: Session = Depends(get_db)):
    if not get_current_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    sizes = db.query(PrintSize).order_by(PrintSize.sort_order).all()
    return templates.TemplateResponse(
        request=request,
        name="print_sizes.html",
        context={"is_authenticated": True, "active_tab": "print-sizes", "sizes": sizes},
    )


@admin_router.post("/print-sizes")
async def add_print_size(
    request: Request,
    name: str = Form(...),
    width_mm: float = Form(...),
    height_mm: float = Form(...),
    db: Session = Depends(get_db)
):
    if not get_current_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    existing = db.query(PrintSize).filter_by(name=name.strip()).first()
    if not existing:
        db.add(PrintSize(name=name.strip(), width_mm=width_mm, height_mm=height_mm, is_active=True))
        db.commit()

    return RedirectResponse(url="/admin/print-sizes", status_code=303)


@admin_router.post("/print-sizes/{size_id}/toggle")
async def toggle_print_size(size_id: int, request: Request, db: Session = Depends(get_db)):
    if not get_current_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    size = db.query(PrintSize).filter_by(id=size_id).first()
    if size:
        size.is_active = not size.is_active
        db.commit()
    return RedirectResponse(url="/admin/print-sizes", status_code=303)


@admin_router.post("/print-sizes/{size_id}/delete")
async def delete_print_size(size_id: int, request: Request, db: Session = Depends(get_db)):
    if not get_current_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    size = db.query(PrintSize).filter_by(id=size_id).first()
    if size:
        db.delete(size)
        db.commit()
    return RedirectResponse(url="/admin/print-sizes", status_code=303)


@admin_router.get("/orders", response_class=HTMLResponse)
async def list_orders(request: Request, db: Session = Depends(get_db)):
    if not get_current_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    orders = db.query(Order).order_by(Order.created_at.desc()).all()
    return templates.TemplateResponse(
        request=request,
        name="orders.html",
        context={"is_authenticated": True, "active_tab": "orders", "orders": orders},
    )


@admin_router.get("/orders/{order_id}", response_class=HTMLResponse)
async def view_order(order_id: str, request: Request, db: Session = Depends(get_db)):
    if not get_current_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    order = db.query(Order).filter_by(order_id=order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    manifest_json_str = None
    if order.manifest_path and os.path.exists(order.manifest_path):
        try:
            with open(order.manifest_path, "r", encoding="utf-8") as f:
                manifest_json_str = f.read()
        except Exception:
            pass

    return templates.TemplateResponse(
        request=request,
        name="order_detail.html",
        context={
            "is_authenticated": True,
            "active_tab": "orders",
            "order": order,
            "manifest_content": manifest_json_str,
        },
    )


@admin_router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db)):
    if not get_current_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    settings_map = {s.key: s.value for s in db.query(SystemSetting).all()}
    theme_colors = get_current_theme_colors(db)

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "is_authenticated": True,
            "active_tab": "settings",
            "settings": settings_map,
            "themes": THEMES,
            "theme_colors": theme_colors,
        },
    )


@admin_router.post("/settings")
async def update_settings(
    request: Request,
    default_dpi: str = Form(...),
    border_width_mm: str = Form(...),
    auto_orientation_matching: str = Form(...),
    ui_theme: str = Form("midnight_blue"),
    source_iphone: Optional[str] = Form(None),
    source_android: Optional[str] = Form(None),
    source_removable: Optional[str] = Form(None),
    source_cddvd: Optional[str] = Form(None),
    source_network: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    if not get_current_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    settings_to_update = [
        ("default_dpi", default_dpi),
        ("border_width_mm", border_width_mm),
        ("auto_orientation_matching", auto_orientation_matching),
        ("ui_theme", ui_theme),
        ("source_iphone", "true" if source_iphone == "true" else "false"),
        ("source_android", "true" if source_android == "true" else "false"),
        ("source_removable", "true" if source_removable == "true" else "false"),
        ("source_cddvd", "true" if source_cddvd == "true" else "false"),
        ("source_network", "true" if source_network == "true" else "false"),
    ]

    for k, v in settings_to_update:
        s = db.query(SystemSetting).filter_by(key=k).first()
        if s:
            s.value = v
        else:
            db.add(SystemSetting(key=k, value=v))
    db.commit()

    # Also update in-process active theme for GUI
    set_active_theme(ui_theme)

    return RedirectResponse(url="/admin/settings", status_code=303)


@admin_router.get("/files", response_class=HTMLResponse)
async def file_manager(request: Request, subpath: str = Query("", alias="path"), db: Session = Depends(get_db)):
    if not get_current_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    base_dir = Path("storage/shared").resolve()
    base_dir.mkdir(parents=True, exist_ok=True)

    # Safe path resolution
    clean_sub = subpath.strip("/\\ ")
    current_target = (base_dir / clean_sub).resolve()

    if not current_target.is_relative_to(base_dir) or not current_target.exists():
        current_target = base_dir
        clean_sub = ""

    parent_sub = ""
    if current_target != base_dir:
        parent_rel = current_target.parent.relative_to(base_dir)
        parent_sub = str(parent_rel).replace("\\", "/") if str(parent_rel) != "." else ""

    file_list = []
    try:
        for f in sorted(current_target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            is_dir = f.is_dir()
            size_display = "-" if is_dir else f"{round(f.stat().st_size / 1024, 1)} KB"
            rel_to_base = str(f.relative_to(base_dir)).replace("\\", "/")
            file_list.append({
                "name": f.name,
                "is_dir": is_dir,
                "type": "Folder" if is_dir else "File",
                "size_display": size_display,
                "rel_path": rel_to_base,
            })
    except Exception:
        pass

    theme_colors = get_current_theme_colors(db)
    return templates.TemplateResponse(
        request=request,
        name="files.html",
        context={
            "is_authenticated": True,
            "active_tab": "files",
            "current_path": str(current_target),
            "current_subpath": clean_sub.replace("\\", "/"),
            "parent_subpath": parent_sub,
            "files": file_list,
            "theme_colors": theme_colors,
        },
    )


@admin_router.get("/files/download")
async def download_file(request: Request, filepath: str = Query(...)):
    if not get_current_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    base_dir = Path("storage/shared").resolve()
    target = (base_dir / filepath.strip("/\\ ")).resolve()

    if not target.is_relative_to(base_dir) or not target.exists():
        raise HTTPException(status_code=404, detail="File or folder not found")

    if target.is_file():
        return FileResponse(
            path=str(target),
            filename=target.name,
            media_type="application/octet-stream"
        )
    else:
        # Zip directory for download
        tmp_zip = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tmp_zip_path = tmp_zip.name
        tmp_zip.close()

        with zipfile.ZipFile(tmp_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(target):
                for file in files:
                    full_p = Path(root) / file
                    rel_p = full_p.relative_to(target)
                    zf.write(full_p, arcname=str(rel_p))

        return FileResponse(
            path=tmp_zip_path,
            filename=f"{target.name}.zip",
            media_type="application/zip",
            background=BackgroundTask(lambda: os.unlink(tmp_zip_path) if os.path.exists(tmp_zip_path) else None)
        )


@admin_router.post("/files/delete")
async def delete_file(request: Request, filepath: str = Form(...), current_subpath: str = Form("")):
    if not get_current_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    base_dir = Path("storage/shared").resolve()
    target = (base_dir / filepath.strip("/\\ ")).resolve()

    # Prevent directory traversal attacks
    if target.is_relative_to(base_dir) and target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

    redirect_url = f"/admin/files?path={current_subpath}" if current_subpath else "/admin/files"
    return RedirectResponse(url=redirect_url, status_code=303)
