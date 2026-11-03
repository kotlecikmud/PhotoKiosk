#!/usr/bin/env python3
"""Unified CLI and runtime entry point for PhotoKiosk."""
import sys
import os
import argparse
from pathlib import Path
from collections import defaultdict
import uvicorn
from sqlalchemy.orm import sessionmaker

from backend.database import (
    init_database,
    get_db_engine,
    Order,
    OrderItem,
    PrintSize,
    PaperFinish,
    get_service_price,
)
from backend.library import scan_directory_for_photos
from backend.orders import generate_order_id, OrderProcessor


def handle_order_create(args):
    """Creates a new order from a given directory with images."""
    engine = init_database()
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        dir_path = Path(args.dir).resolve()
        if not dir_path.exists() or not dir_path.is_dir():
            print(f"Error: Directory does not exist or is not a directory: {dir_path}")
            sys.exit(1)

        photos = scan_directory_for_photos(str(dir_path))
        if not photos:
            print(f"No supported photos found in directory: {dir_path}")
            sys.exit(1)

        # Validate size and paper
        valid_size = db.query(PrintSize).filter_by(name=args.size).first()
        if not valid_size:
            all_sizes = [s.name for s in db.query(PrintSize).all()]
            print(f"Error: Invalid size '{args.size}'. Available sizes: {', '.join(all_sizes)}")
            sys.exit(1)

        valid_finish = db.query(PaperFinish).filter_by(name=args.finish.upper()).first()
        if not valid_finish:
            all_finishes = [f.name for f in db.query(PaperFinish).all()]
            print(f"Error: Invalid paper finish '{args.finish}'. Available finishes: {', '.join(all_finishes)}")
            sys.exit(1)

        order_id = generate_order_id()
        total_items = len(photos)
        total_prints = total_items * args.copies

        db_order = Order(
            order_id=order_id,
            status="PENDING",
            total_items=total_items,
            total_prints=total_prints,
        )
        db.add(db_order)

        order_items = []
        for p in photos:
            item = OrderItem(
                order_id=order_id,
                original_filename=p["filename"],
                original_rel_path=p["filepath"],
                print_size_name=args.size,
                paper_finish=args.finish.upper(),
                crop_mode=args.crop,
                quantity=args.copies,
                status="PENDING",
            )
            db.add(item)
            order_items.append(item)

        db.commit()

        u_price = get_service_price(args.size, args.finish.lower(), db=db)
        total_price = u_price * total_prints

        print("=" * 60)
        print(f"Successfully Created New Order: {order_id}")
        print("=" * 60)
        print(f"Status:       PENDING")
        print(f"Directory:    {dir_path}")
        print(f"Total Photos: {total_items}")
        print(f"Total Prints: {total_prints}")
        print(f"Size:         {args.size}")
        print(f"Paper Finish: {args.finish.upper()}")
        print(f"Crop Mode:    {args.crop}")
        print(f"Unit Price:   {u_price:.2f} PLN / print")
        print(f"Total Price:  {total_price:.2f} PLN")
        print("=" * 60)
        return order_id
    finally:
        db.close()


def handle_order_show(args):
    """Displays a detailed view of an order with quantities, formats, paper finishes, prices."""
    engine = init_database()
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        order = db.query(Order).filter_by(order_id=args.order_id).first()
        if not order:
            print(f"Error: Order '{args.order_id}' not found.")
            sys.exit(1)

        print("=" * 65)
        print(f"Order Inspection: {order.order_id}")
        print("=" * 65)
        print(f"Status:       {order.status}")
        print(f"Created At:   {order.created_at.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"Total Photos: {order.total_items}")
        print(f"Total Prints: {order.total_prints}")
        if order.error_message:
            print(f"Error Log:    {order.error_message}")

        # Group breakdown
        groups = defaultdict(lambda: {"count": 0, "photos": set(), "unit_price": 0.0, "subtotal": 0.0})
        total_cost = 0.0
        for item in order.items:
            key = (item.print_size_name, item.paper_finish.upper(), item.crop_mode)
            u_price = get_service_price(item.print_size_name, item.paper_finish.lower(), db=db)
            subtot = u_price * item.quantity
            groups[key]["count"] += item.quantity
            groups[key]["photos"].add(item.original_filename)
            groups[key]["unit_price"] = u_price
            groups[key]["subtotal"] += subtot
            total_cost += subtot

        print("-" * 65)
        print("SUMMARY BREAKDOWN BY FORMAT & FINISH:")
        print(f"{'Format':<10} {'Finish':<10} {'Crop Mode':<12} {'Prints':<8} {'Unit (PLN)':<12} {'Subtotal (PLN)'}")
        print("-" * 65)
        for (sz, fn, cr), data in sorted(groups.items()):
            print(f"{sz:<10} {fn:<10} {cr:<12} {data['count']:<8} {data['unit_price']:<12.2f} {data['subtotal']:.2f}")

        print("-" * 65)
        print(f"Estimated Grand Total: {total_cost:.2f} PLN")
        print("=" * 65)

        if args.verbose:
            print("\nITEMIZED PHOTOS:")
            for idx, item in enumerate(order.items, 1):
                out = item.output_rel_path or item.status
                print(f"  {idx:3d}. {item.original_filename} [{item.print_size_name} {item.paper_finish}] x{item.quantity} -> {out}")
    finally:
        db.close()


def handle_order_list(args):
    """Lists orders with status, total items, prints, and date."""
    engine = init_database()
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        q = db.query(Order)
        if args.pending:
            q = q.filter(Order.status.in_(["PENDING", "PROCESSING"]))
        orders = q.order_by(Order.created_at.desc()).all()

        if not orders:
            print("No matching orders found.")
            return

        print(f"{'Order ID':<25} {'Status':<12} {'Created At':<20} {'Photos':<8} {'Prints'}")
        print("-" * 75)
        for o in orders:
            print(f"{o.order_id:<25} {o.status:<12} {o.created_at.strftime('%Y-%m-%d %H:%M:%S'):<20} {o.total_items:<8} {o.total_prints}")
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="PhotoKiosk Application & Services")
    subparsers = parser.add_subparsers(dest="subcommand", help="Sub-commands")

    # Order management CLI subcommands
    order_parser = subparsers.add_parser("order", help="Order management CLI commands")
    order_subparsers = order_parser.add_subparsers(dest="order_action", help="Order actions")

    # order create
    create_parser = order_subparsers.add_parser("create", help="Create new order from directory with images")
    create_parser.add_argument("--dir", required=True, help="Path to directory containing photos")
    create_parser.add_argument("--size", default="10x15", help="Print size format (default: 10x15)")
    create_parser.add_argument("--finish", default="GLOSSY", help="Paper finish (default: GLOSSY)")
    create_parser.add_argument("--crop", default="fill_in", choices=["fill_in", "fit_in", "border", "no_resize"], help="Crop mode (default: fill_in)")
    create_parser.add_argument("--copies", type=int, default=1, help="Copies per photo (default: 1)")

    # order show
    show_parser = order_subparsers.add_parser("show", help="Show detailed breakdown of an order")
    show_parser.add_argument("order_id", help="Order ID (e.g., ORD-YYYYMMDD-HHMMSS-XXXX)")
    show_parser.add_argument("-v", "--verbose", action="store_true", help="Display itemized photo list")

    # order list
    list_parser = order_subparsers.add_parser("list", help="List orders")
    list_parser.add_argument("--pending", action="store_true", help="List only pending/processing orders")

    # Standard server / kiosk flags
    parser.add_argument("--mode", choices=["all", "gui", "backend"], default="all",
                        help="Run mode: 'gui' for kiosk interface, 'backend' for API/Admin, 'all' for both.")
    parser.add_argument("--fullscreen", action="store_true", help="Launch GUI in fullscreen mode.")
    parser.add_argument("--host", default="0.0.0.0", help="Backend bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Backend bind port (default: 8080)")

    args = parser.parse_args()

    # Route subcommands
    if args.subcommand == "order":
        if args.order_action == "create":
            handle_order_create(args)
            return
        elif args.order_action == "show":
            handle_order_show(args)
            return
        elif args.order_action == "list":
            handle_order_list(args)
            return
        else:
            order_parser.print_help()
            return

    # Ensure database is initialized
    init_database()

    if args.mode == "backend":
        print(f"Starting PhotoKiosk Backend & Admin service on http://{args.host}:{args.port}")
        uvicorn.run("backend.app:app", host=args.host, port=args.port, reload=False)

    elif args.mode == "gui":
        print("Launching PhotoKiosk Touchscreen Interface...")
        from gui.app import run_gui
        run_gui(fullscreen=args.fullscreen)

    elif args.mode == "all":
        import multiprocessing
        print(f"Starting PhotoKiosk Backend on port {args.port}...")
        backend_proc = multiprocessing.Process(
            target=uvicorn.run,
            args=("backend.app:app",),
            kwargs={"host": args.host, "port": args.port, "reload": False},
            daemon=True
        )
        backend_proc.start()

        print("Launching PhotoKiosk GUI...")
        from gui.app import run_gui
        try:
            run_gui(fullscreen=args.fullscreen)
        finally:
            if backend_proc.is_alive():
                backend_proc.terminate()
                backend_proc.join()


if __name__ == "__main__":
    main()
