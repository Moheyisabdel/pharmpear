"""Pharmacy desk: one patient at a time, stock, and driver routes.

Drive times use the same local estimate as Route Planner JAK when no maps key
is set: same ZIP is 8 minutes, same ZIP prefix is 14, otherwise 22.
"""

import re
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "pharmpear.db"
_LOCK = threading.Lock()

INTERACTIONS = (
    (("ibuprofen", "warfarin"), "Ibuprofen with warfarin can raise bleeding risk."),
    (("lisinopril", "potassium"), "Lisinopril with a potassium product can raise potassium."),
    (("simvastatin", "clarithromycin"), "Clarithromycin with simvastatin can raise statin levels."),
)

SERVICE_MINUTES = 8
TASKS = {
    "call": "Make calls",
    "fill": "Fill the prescription",
    "fax": "Read a fax",
    "summary": "Summarize the file and the last call",
    "route": "Plan the driver route",
}
WORKER_DEFAULTS = {
    "online_name": "Online worker",
    "online_task": "call",
    "site_name": "At-site worker",
    "site_task": "fill",
    "caller": "online",
}


def connect():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _LOCK:
        conn = connect()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS patients (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                place TEXT NOT NULL,
                zip TEXT NOT NULL,
                file_summary TEXT NOT NULL,
                last_call_summary TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS drugs (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                on_hand INTEGER NOT NULL,
                reorder_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY,
                patient_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                drug TEXT NOT NULL,
                directions TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                prescriber TEXT NOT NULL,
                task TEXT NOT NULL,
                status TEXT NOT NULL,
                ship_method TEXT NOT NULL,
                payment_status TEXT NOT NULL,
                interaction_note TEXT NOT NULL,
                ship_choice TEXT NOT NULL,
                ship_price TEXT NOT NULL,
                driver_name TEXT NOT NULL,
                stop_order INTEGER NOT NULL,
                travel_minutes INTEGER NOT NULL,
                FOREIGN KEY(patient_id) REFERENCES patients(id)
            );
            CREATE TABLE IF NOT EXISTS drivers (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                place TEXT NOT NULL,
                zip TEXT NOT NULL,
                active INTEGER NOT NULL
            );
            """
        )
        _ensure_workers(conn)
        if conn.execute("SELECT COUNT(*) AS n FROM patients").fetchone()["n"] == 0:
            _seed(conn)
        conn.commit()
        conn.close()


def _ensure_workers(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(orders)")}
    if "worker_name" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN worker_name TEXT NOT NULL DEFAULT ''")
    if "worker_place" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN worker_place TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    for key, value in WORKER_DEFAULTS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
            (key, value),
        )


def _seed(conn):
    patients = [
        (
            "Sample North",
            "Houston, TX",
            "77002",
            "Sample file. One open fill for the driver route. No date of birth is stored.",
            "No call yet.",
        ),
        (
            "Sample East",
            "Houston, TX",
            "77006",
            "Sample file. Waiting for a call. The last call summary stays on this file.",
            "No call yet.",
        ),
        (
            "Sample West",
            "Katy, TX",
            "77494",
            "Sample file. Warfarin is already on the file. A new ibuprofen fill must show the interaction note.",
            "Last call: confirmed the delivery window. No drug names were repeated back.",
        ),
        (
            "Sample South",
            "Sugar Land, TX",
            "77479",
            "Sample file. This fill ships. The desk picks the lowest label price.",
            "No call yet.",
        ),
    ]
    for row in patients:
        conn.execute(
            "INSERT INTO patients (name, place, zip, file_summary, last_call_summary) VALUES (?,?,?,?,?)",
            row,
        )
    for name, on_hand, reorder_at in (
        ("Lisinopril 10 mg", 40, 20),
        ("Ibuprofen 200 mg", 12, 15),
        ("Warfarin 5 mg", 18, 10),
    ):
        conn.execute(
            "INSERT INTO drugs (name, on_hand, reorder_at) VALUES (?,?,?)",
            (name, on_hand, reorder_at),
        )
    orders = [
        (3, "electronic", "Warfarin 5 mg", "As directed", 30, "Sample Prescriber", "fill", "done", "none"),
        (1, "electronic", "Lisinopril 10 mg", "One tablet daily", 30, "Sample Prescriber", "fill", "out", "driver"),
        (3, "fax", "Ibuprofen 200 mg", "One tablet with food", 20, "Sample Prescriber", "fill", "out", "driver"),
        (2, "fax", "", "", 0, "", "call", "waiting", "none"),
        (4, "electronic", "Lisinopril 10 mg", "One tablet daily", 30, "Sample Prescriber", "fill", "waiting", "fedex"),
    ]
    for row in orders:
        patient_id, source, drug, directions, quantity, prescriber, task, status, ship = row
        note = interaction_for(conn, patient_id, drug)
        conn.execute(
            """
            INSERT INTO orders (
                patient_id, source, drug, directions, quantity, prescriber, task, status,
                ship_method, payment_status, interaction_note, ship_choice, ship_price,
                driver_name, stop_order, travel_minutes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                patient_id,
                source,
                drug,
                directions,
                quantity,
                prescriber,
                task,
                status,
                ship,
                "On this order",
                note,
                "",
                "",
                "",
                0,
                0,
            ),
        )
    conn.executemany(
        "INSERT INTO drivers (name, place, zip, active) VALUES (?,?,?,1)",
        (("Driver One", "Houston, TX", "77002"), ("Driver Two", "Houston, TX", "77019")),
    )
    optimize_routes(conn)


def interaction_for(conn, patient_id, drug):
    names = [drug.lower()]
    rows = conn.execute(
        "SELECT drug FROM orders WHERE patient_id = ? AND drug != ''",
        (patient_id,),
    ).fetchall()
    names.extend(row["drug"].lower() for row in rows)
    blob = " ".join(names)
    notes = []
    for pair, note in INTERACTIONS:
        if pair[0] in blob and pair[1] in blob:
            notes.append(note)
    if not notes:
        return ""
    return " ".join(notes) + " This review supports the fill and does not replace the prescriber."


def travel_minutes(place_a, zip_a, place_b, zip_b):
    if (place_a, zip_a) == (place_b, zip_b):
        return 8
    if zip_a and zip_b and zip_a[:3] == zip_b[:3]:
        return 14
    return 22


def optimize_routes(conn):
    """Balance driver fills, then order each route by the next nearest stop."""
    depot = ("Houston, TX", "77002")
    drivers = conn.execute("SELECT * FROM drivers WHERE active = 1 ORDER BY id").fetchall()
    stops = conn.execute(
        """
        SELECT orders.id, patients.place, patients.zip, patients.name
        FROM orders JOIN patients ON patients.id = orders.patient_id
        WHERE orders.task = 'fill' AND orders.ship_method = 'driver'
          AND orders.status IN ('assigned', 'out')
        ORDER BY orders.id
        """
    ).fetchall()
    conn.execute(
        """
        UPDATE orders SET driver_name = '', stop_order = 0, travel_minutes = 0
        WHERE ship_method = 'driver' AND status IN ('assigned', 'out')
        """
    )
    if not drivers or not stops:
        return
    loads = {row["id"]: 0 for row in drivers}
    assigned = {row["id"]: [] for row in drivers}
    ranked = sorted(
        stops,
        key=lambda row: travel_minutes(depot[0], depot[1], row["place"], row["zip"]),
        reverse=True,
    )
    for stop in ranked:
        driver_id = min(loads, key=lambda key: (loads[key], key))
        assigned[driver_id].append(stop)
        loads[driver_id] += SERVICE_MINUTES + travel_minutes(
            depot[0], depot[1], stop["place"], stop["zip"]
        )
    by_id = {row["id"]: row for row in drivers}
    for driver_id, bucket in assigned.items():
        driver = by_id[driver_id]
        cursor = (driver["place"], driver["zip"])
        remaining = list(bucket)
        order = 1
        while remaining:
            remaining.sort(
                key=lambda row: travel_minutes(cursor[0], cursor[1], row["place"], row["zip"])
            )
            nxt = remaining.pop(0)
            minutes = travel_minutes(cursor[0], cursor[1], nxt["place"], nxt["zip"])
            conn.execute(
                """
                UPDATE orders
                SET driver_name = ?, stop_order = ?, travel_minutes = ?
                WHERE id = ?
                """,
                (driver["name"], order, minutes, nxt["id"]),
            )
            cursor = (nxt["place"], nxt["zip"])
            order += 1


def ship_rates(zip_code):
    local = str(zip_code).startswith("77")
    ground = 8.40 if local else 11.20
    return (
        ("FedEx Ground", ground),
        ("USPS Priority", round(ground + 1.80, 2)),
        ("FedEx Express", round(ground + 6.50, 2)),
    )


def cheapest_rate(zip_code):
    return min(ship_rates(zip_code), key=lambda item: item[1])


def parse_fax(text):
    def grab(label):
        match = re.search(rf"^{label}\s*:\s*(.+)$", text, re.I | re.M)
        return match.group(1).strip() if match else ""

    quantity = grab("quantity") or grab("qty")
    try:
        qty = int(re.sub(r"\D", "", quantity) or "0")
    except ValueError:
        qty = 0
    return {
        "patient": grab("patient"),
        "drug": grab("drug"),
        "directions": grab("directions") or grab("sig"),
        "quantity": qty,
        "prescriber": grab("prescriber"),
    }


def _rows(conn, sql, args=()):
    return [dict(row) for row in conn.execute(sql, args).fetchall()]


def worker_settings(conn):
    saved = {
        row["key"]: row["value"]
        for row in conn.execute("SELECT key, value FROM settings").fetchall()
    }
    settings = dict(WORKER_DEFAULTS)
    settings.update({key: saved[key] for key in WORKER_DEFAULTS if key in saved})
    if settings["online_task"] not in TASKS:
        settings["online_task"] = "call"
    if settings["site_task"] not in TASKS:
        settings["site_task"] = "fill"
    if settings["caller"] not in {"online", "site"}:
        settings["caller"] = "online"
    return settings


def _seat(settings, place):
    return {
        "place": place,
        "name": settings[f"{place}_name"].strip() or WORKER_DEFAULTS[f"{place}_name"],
        "task": settings[f"{place}_task"],
        "task_label": TASKS[settings[f"{place}_task"]],
        "makes_calls": settings["caller"] == place,
    }


def seats(settings):
    return {"online": _seat(settings, "online"), "site": _seat(settings, "site")}


def worker_for(settings, task):
    pair = seats(settings)
    if task == "call":
        return pair[settings["caller"]]
    fillers = [seat for seat in pair.values() if seat["task"] == "fill"]
    if len(fillers) == 1:
        return fillers[0]
    if len(fillers) > 1:
        return pair["site"]
    return None


def save_workers(online_name, online_task, site_name, site_task, caller):
    online_name = " ".join((online_name or "").split())[:60]
    site_name = " ".join((site_name or "").split())[:60]
    if not online_name or not site_name:
        return "Name both the online worker and the at-site worker."
    if online_task not in TASKS or site_task not in TASKS:
        return "Choose a task for each worker."
    if caller not in {"online", "site"}:
        return "Choose which worker makes the calls."
    init_db()
    with _LOCK:
        conn = connect()
        values = {
            "online_name": online_name,
            "online_task": online_task,
            "site_name": site_name,
            "site_task": site_task,
            "caller": caller,
        }
        for key, value in values.items():
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        conn.commit()
        conn.close()
    caller_name = online_name if caller == "online" else site_name
    return f"Saved. {caller_name} makes the calls."


def snapshot():
    init_db()
    with _LOCK:
        conn = connect()
        workers = seats(worker_settings(conn))
        patients = _rows(conn, "SELECT * FROM patients ORDER BY id")
        orders = _rows(
            conn,
            """
            SELECT orders.*, patients.name AS patient_name, patients.place, patients.zip,
                   patients.file_summary, patients.last_call_summary
            FROM orders JOIN patients ON patients.id = orders.patient_id
            ORDER BY orders.status = 'assigned' DESC, orders.id
            """,
        )
        drugs = _rows(conn, "SELECT * FROM drugs ORDER BY name")
        drivers = _rows(conn, "SELECT * FROM drivers WHERE active = 1 ORDER BY id")
        conn.close()
    current = next((row for row in orders if row["status"] == "assigned"), None)
    waiting = [row for row in orders if row["status"] == "waiting"]
    return {
        "patients": patients,
        "orders": orders,
        "drugs": drugs,
        "drivers": drivers,
        "workers": workers,
        "tasks": TASKS,
        "current": current,
        "waiting": waiting,
        "routes": _route_groups(orders),
    }


def _route_groups(orders):
    groups = {}
    for row in orders:
        if row["ship_method"] != "driver" or not row["driver_name"]:
            continue
        groups.setdefault(row["driver_name"], []).append(row)
    for stops in groups.values():
        stops.sort(key=lambda row: row["stop_order"])
    return groups


def assign_next(task):
    if task not in {"call", "fill"}:
        return "Choose a call or a fill."
    init_db()
    with _LOCK:
        conn = connect()
        open_row = conn.execute(
            "SELECT id FROM orders WHERE status = 'assigned' LIMIT 1"
        ).fetchone()
        if open_row:
            conn.close()
            return "Finish the open patient before assigning the next one."
        nxt = conn.execute(
            "SELECT id, patient_id, drug FROM orders WHERE status = 'waiting' ORDER BY id LIMIT 1"
        ).fetchone()
        if not nxt:
            conn.close()
            return "No patient is waiting."
        settings = worker_settings(conn)
        worker = worker_for(settings, task)
        if worker is None:
            conn.close()
            return "Set one worker's task to fill the prescription before assigning a fill."
        note = interaction_for(conn, nxt["patient_id"], nxt["drug"])
        ship = "driver" if task == "fill" else "none"
        conn.execute(
            """
            UPDATE orders
            SET task = ?, status = 'assigned', ship_method = ?, interaction_note = ?,
                worker_name = ?, worker_place = ?
            WHERE id = ?
            """,
            (task, ship, note, worker["name"], worker["place"], nxt["id"]),
        )
        conn.commit()
        conn.close()
    place = "online" if worker["place"] == "online" else "at the site"
    return f"Assigned the next patient to {worker['name']} ({place}) for a {task}."


def complete_current(note):
    init_db()
    note = (note or "").strip()[:240]
    with _LOCK:
        conn = connect()
        current = conn.execute(
            """
            SELECT orders.id, orders.task, orders.drug, orders.quantity, orders.patient_id
            FROM orders WHERE status = 'assigned' ORDER BY id LIMIT 1
            """
        ).fetchone()
        if not current:
            conn.close()
            return "No patient is assigned."
        if current["task"] == "fill" and current["drug"]:
            drug = conn.execute(
                "SELECT id, on_hand FROM drugs WHERE name = ?",
                (current["drug"],),
            ).fetchone()
            if drug:
                conn.execute(
                    "UPDATE drugs SET on_hand = ? WHERE id = ?",
                    (max(0, drug["on_hand"] - int(current["quantity"] or 0)), drug["id"]),
                )
        if current["task"] == "call":
            summary = note or "Call finished."
            conn.execute(
                "UPDATE patients SET last_call_summary = ? WHERE id = ?",
                (summary, current["patient_id"]),
            )
        conn.execute("UPDATE orders SET status = 'done' WHERE id = ?", (current["id"],))
        conn.commit()
        conn.close()
    return "That patient is finished. Assign the next one."


def plan_routes():
    init_db()
    with _LOCK:
        conn = connect()
        optimize_routes(conn)
        conn.commit()
        conn.close()
    return "Driver routes are ordered. Times are the route planner's local estimate."


def choose_ship(order_id):
    init_db()
    with _LOCK:
        conn = connect()
        row = conn.execute(
            """
            SELECT orders.id, patients.zip
            FROM orders JOIN patients ON patients.id = orders.patient_id
            WHERE orders.id = ? AND orders.ship_method = 'fedex'
            """,
            (order_id,),
        ).fetchone()
        if not row:
            conn.close()
            return "That order is not a shipment."
        name, price = cheapest_rate(row["zip"])
        conn.execute(
            "UPDATE orders SET ship_choice = ?, ship_price = ? WHERE id = ?",
            (name, f"{price:.2f}", row["id"]),
        )
        conn.commit()
        conn.close()
    return f"Lowest label price is {name}."


def replenish(drug_id):
    init_db()
    with _LOCK:
        conn = connect()
        row = conn.execute("SELECT id, on_hand FROM drugs WHERE id = ?", (drug_id,)).fetchone()
        if not row:
            conn.close()
            return "That stock item is not on the desk."
        conn.execute(
            "UPDATE drugs SET on_hand = ? WHERE id = ?",
            (row["on_hand"] + 30, row["id"]),
        )
        conn.commit()
        conn.close()
    return "Wholesaler replenishment added 30 to stock."


def intake_fax(text):
    parsed = parse_fax(text or "")
    if not parsed["patient"] or not parsed["drug"]:
        return "A fax needs a Patient line and a Drug line."
    init_db()
    with _LOCK:
        conn = connect()
        patient = conn.execute(
            "SELECT id FROM patients WHERE lower(name) = lower(?)",
            (parsed["patient"],),
        ).fetchone()
        if patient:
            patient_id = patient["id"]
        else:
            cur = conn.execute(
                """
                INSERT INTO patients (name, place, zip, file_summary, last_call_summary)
                VALUES (?, 'Houston, TX', '77002', ?, 'No call yet.')
                """,
                (
                    parsed["patient"][:80],
                    "Sample file opened from a fax. No date of birth is stored.",
                ),
            )
            patient_id = cur.lastrowid
        note = interaction_for(conn, patient_id, parsed["drug"])
        conn.execute(
            "INSERT INTO drugs (name, on_hand, reorder_at) VALUES (?, 30, 10) ON CONFLICT(name) DO NOTHING",
            (parsed["drug"][:80],),
        )
        conn.execute(
            """
            INSERT INTO orders (
                patient_id, source, drug, directions, quantity, prescriber, task, status,
                ship_method, payment_status, interaction_note, ship_choice, ship_price,
                driver_name, stop_order, travel_minutes
            ) VALUES (?, 'fax', ?, ?, ?, ?, 'fill', 'waiting', 'driver', 'On this order', ?, '', '', '', 0, 0)
            """,
            (
                patient_id,
                parsed["drug"][:80],
                (parsed["directions"] or "See the fax")[:160],
                parsed["quantity"] or 30,
                (parsed["prescriber"] or "Prescriber on the fax")[:80],
                note,
            ),
        )
        conn.execute(
            """
            UPDATE patients
            SET file_summary = ?
            WHERE id = ?
            """,
            (
                f"Fax read into the order: {parsed['drug'][:80]}. Quantity {parsed['quantity'] or 30}.",
                patient_id,
            ),
        )
        conn.commit()
        conn.close()
    return "The fax is on the queue as a fill."


def order_for_print(order_id):
    init_db()
    with _LOCK:
        conn = connect()
        row = conn.execute(
            """
            SELECT orders.*, patients.name AS patient_name, patients.place, patients.zip,
                   patients.file_summary
            FROM orders JOIN patients ON patients.id = orders.patient_id
            WHERE orders.id = ?
            """,
            (order_id,),
        ).fetchone()
        conn.close()
    return dict(row) if row else None
