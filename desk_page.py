"""Signed-in pharmacy desk. Patient rows stay off the public pages."""

from html import escape

import pharmacy


def page(state, csrf, notice=""):
    current = state["current"]
    current_html = (
        _current(current)
        if current
        else "<p>No patient is assigned. Assign the next one for a call or a fill.</p>"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pharmacy desk — PharmPear</title>
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/styles.css">
</head>
<body>
  <div class="ribbon">Pharmacy desk · One patient at a time · Driver routes</div>
  <header class="site-header">
    <a class="brand" href="/desk">
      <svg class="mark" viewBox="0 0 48 48" aria-hidden="true">
        <path fill="#1c6b43" d="M24 6c2 6 2 10 0 16 6-2 10-2 16 0-6 2-10 6-12 12-2-6-6-10-12-12 6-2 10-6 8-16z"/>
        <path fill="#0e3d28" d="M23 22h2v18h-2z"/>
      </svg>
      <span>PharmPear</span>
    </a>
    <nav class="site-nav">
      <a href="#workers">Workers</a>
      <a href="#queue">Queue</a>
      <a href="#routes">Routes</a>
      <a href="#labels">Labels</a>
      <a href="#stock">Stock</a>
      <a href="/">Public site</a>
      <a class="logout" href="/logout">Log out</a>
    </nav>
  </header>
  <main class="desk">
    <p class="eyebrow">Pharmacy management service</p>
    <h1>The desk</h1>
    <p class="lede">This is the signed-in service from the public page: the patient file, one assignment at a time, driver routes, shipping prices, and the labels. Sample rows are practice data. A drug-interaction note supports the fill and does not replace the prescriber.</p>
    <p class="form-note">{escape(notice)}</p>
    <section class="panel" id="workers">
      <h2>Workers</h2>
      <p>Set the task for the online worker and the at-site worker. One of them also makes the calls. The next assignment uses those choices.</p>
      {_workers(state["workers"], state["tasks"], csrf)}
    </section>
    <section class="desk-grid" id="queue">
      <article class="panel">
        <h2>This patient</h2>
        {current_html}
        {_complete_form(csrf, bool(current))}
      </article>
      <article class="panel">
        <h2>Next patient</h2>
        {_waiting(state["waiting"], csrf)}
      </article>
    </section>
    <section class="panel" id="files">
      <h2>Patient info</h2>
      <p>Each file shows a summary of the whole file and the last call summary.</p>
      {_patients(state["patients"])}
    </section>
    <section class="panel" id="fax">
      <h2>Read a fax</h2>
      <p>Paste a fax with Patient, Drug, Directions, Quantity, and Prescriber lines. The desk puts those fields on a fill.</p>
      <form method="post" action="/desk/fax">
        <input type="hidden" name="csrf" value="{escape(csrf)}">
        <label>Fax text
          <textarea name="fax" rows="5" maxlength="2000" placeholder="Patient: Sample East&#10;Drug: Lisinopril 10 mg&#10;Directions: One tablet daily&#10;Quantity: 30&#10;Prescriber: Sample Prescriber"></textarea>
        </label>
        <button class="btn btn-solid" type="submit">Read fax into the queue</button>
      </form>
    </section>
    <section class="panel" id="routes">
      <h2>Driver routes</h2>
      <p>Optimize uses the route planner's local estimate: same ZIP is 8 minutes, a shared ZIP prefix is 14, and anything farther is 22. Stops are balanced across drivers, then each route is ordered to the next nearest stop. A maps key is not used.</p>
      <form method="post" action="/desk/routes">
        <input type="hidden" name="csrf" value="{escape(csrf)}">
        <button class="btn btn-solid" type="submit">Optimize driver routes</button>
      </form>
      {_routes(state["routes"], state["drivers"])}
    </section>
    <section class="panel" id="labels">
      <h2>Labels and shipping price</h2>
      <p>Shipments pick the lowest price on the desk rate card. That is the ShipStation step until a ShipStation key is saved. Print sheets are for the receipt, the medication label, and the FedEx label.</p>
      {_labels(state["orders"], csrf)}
    </section>
    <section class="panel" id="stock">
      <h2>Stock from prescription usage</h2>
      <p>Finishing a fill reduces on-hand stock by the quantity. When stock is at or under the reorder line, replenish it from the wholesaler.</p>
      {_stock(state["drugs"], csrf)}
    </section>
  </main>
</body>
</html>
"""


def _current(row):
    interaction = ""
    if row["interaction_note"]:
        interaction = f"<p class='flag'>{escape(row['interaction_note'])}</p>"
    drug = escape(row["drug"] or "Call only")
    return f"""
      <p class="board-label">{escape(row['task'])}</p>
      <p>{_worker_line(row)}</p>
      <h3>{escape(row['patient_name'])}</h3>
      <p>{escape(row['place'])} {escape(row['zip'])}</p>
      <p><strong>{drug}</strong> · {escape(row['directions'] or 'No directions')}</p>
      <p>File summary: {escape(row['file_summary'])}</p>
      <p>Last call summary: {escape(row['last_call_summary'])}</p>
      <p>Payment: {escape(row['payment_status'])}</p>
      {interaction}
    """


def _worker_line(row):
    name = (row.get("worker_name") or "").strip()
    if not name:
        return "No worker is on this assignment yet."
    place = "Online" if row.get("worker_place") == "online" else "At the site"
    return f"{place}: {escape(name)}"


def _workers(workers, tasks, csrf):
    return f"""
      <form method="post" action="/desk/workers" class="desk-grid">
        <input type="hidden" name="csrf" value="{escape(csrf)}">
        {_seat_fields("online", "Online worker", workers["online"], tasks)}
        {_seat_fields("site", "At-site worker", workers["site"], tasks)}
        <p><button class="btn btn-solid" type="submit">Save workers</button></p>
      </form>
    """


def _seat_fields(place, title, seat, tasks):
    options = "".join(
        f'<option value="{key}"{" selected" if seat["task"] == key else ""}>{escape(label)}</option>'
        for key, label in tasks.items()
    )
    checked = " checked" if seat["makes_calls"] else ""
    field = "online" if place == "online" else "site"
    return f"""
      <fieldset>
        <legend>{title}</legend>
        <label>Name
          <input name="{field}_name" maxlength="60" value="{escape(seat['name'])}" required>
        </label>
        <label>Task
          <select name="{field}_task">{options}</select>
        </label>
        <label class="check"><input type="radio" name="caller" value="{place}"{checked}> This worker makes the calls</label>
      </fieldset>
    """


def _complete_form(csrf, enabled):
    if not enabled:
        return ""
    return f"""
      <form method="post" action="/desk/complete">
        <input type="hidden" name="csrf" value="{escape(csrf)}">
        <label>Note for the file
          <input name="note" maxlength="240" placeholder="Short note. Do not add a date of birth or member ID.">
        </label>
        <button class="btn btn-solid" type="submit">Finish this patient</button>
      </form>
    """


def _waiting(rows, csrf):
    if not rows:
        return "<p>The queue is clear.</p>"
    nxt = rows[0]
    label = escape(nxt["drug"] or "Call")
    return f"""
      <p>Only the next patient can be assigned. {len(rows)} waiting.</p>
      <h3>{escape(nxt['patient_name'])}</h3>
      <p>{label}</p>
      <form method="post" action="/desk/assign">
        <input type="hidden" name="csrf" value="{escape(csrf)}">
        <button class="btn btn-solid" name="task" value="call" type="submit">Assign for a call</button>
        <button class="btn btn-ghost" name="task" value="fill" type="submit">Assign for a fill</button>
      </form>
    """


def _patients(rows):
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f"<td>{escape(row['name'])}</td>"
            f"<td>{escape(row['file_summary'])}</td>"
            f"<td>{escape(row['last_call_summary'])}</td>"
            "</tr>"
        )
    return (
        "<table class='desk-table'><thead><tr><th>Patient</th><th>File summary</th>"
        f"<th>Last call summary</th></tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


def _routes(groups, drivers):
    if not groups:
        names = ", ".join(escape(row["name"]) for row in drivers) or "No drivers"
        return f"<p>No route yet. Drivers on the desk: {names}.</p>"
    blocks = []
    for name, stops in groups.items():
        items = "".join(
            f"<li>Stop {stop['stop_order']}: {escape(stop['patient_name'])}, "
            f"{escape(stop['place'])} {escape(stop['zip'])} · {stop['travel_minutes']} min"
            + (
                f"<br>{escape(stop['interaction_note'])}"
                if stop.get("interaction_note")
                else ""
            )
            + "</li>"
            for stop in stops
        )
        total = sum(stop["travel_minutes"] for stop in stops) + 8 * len(stops)
        blocks.append(f"<h3>{escape(name)}</h3><p>{total} minutes with service time.</p><ol>{items}</ol>")
    return "".join(blocks)


def _labels(orders, csrf):
    rows = []
    for row in orders:
        if row["task"] != "fill" or row["status"] == "done":
            continue
        ship = ""
        if row["ship_method"] == "fedex":
            choice = escape(row["ship_choice"] or "Not chosen")
            price = escape(row["ship_price"] or "—")
            ship = (
                f"<form method='post' action='/desk/ship/{row['id']}'>"
                f"<input type='hidden' name='csrf' value='{escape(csrf)}'>"
                f"<button class='btn btn-ghost' type='submit'>Best label price</button></form>"
                f"<span>{choice} ${price}</span>"
            )
        rows.append(
            "<tr>"
            f"<td>{escape(row['patient_name'])}</td>"
            f"<td>{escape(row['drug'])}</td>"
            f"<td>{escape(row['ship_method'])}</td>"
            f"<td>{ship}</td>"
            f"<td><a href='/desk/print/receipt/{row['id']}'>Receipt</a> · "
            f"<a href='/desk/print/label/{row['id']}'>Medication label</a> · "
            f"<a href='/desk/print/fedex/{row['id']}'>FedEx label</a></td>"
            "</tr>"
        )
    if not rows:
        return "<p>No fills to print.</p>"
    return (
        "<table class='desk-table'><thead><tr><th>Patient</th><th>Drug</th><th>Ship</th>"
        f"<th>Price</th><th>Print</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _stock(drugs, csrf):
    body = []
    for row in drugs:
        low = row["on_hand"] <= row["reorder_at"]
        action = ""
        if low:
            action = (
                f"<form method='post' action='/desk/replenish/{row['id']}'>"
                f"<input type='hidden' name='csrf' value='{escape(csrf)}'>"
                "<button class='btn btn-ghost' type='submit'>Replenish from wholesaler</button></form>"
            )
        body.append(
            "<tr>"
            f"<td>{escape(row['name'])}</td>"
            f"<td>{row['on_hand']}</td>"
            f"<td>{row['reorder_at']}</td>"
            f"<td>{'Reorder' if low else 'Enough'} {action}</td>"
            "</tr>"
        )
    return (
        "<table class='desk-table'><thead><tr><th>Drug</th><th>On hand</th><th>Reorder at</th>"
        f"<th></th></tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


def print_sheet(kind, order):
    titles = {
        "receipt": "Receipt",
        "label": "Medication label",
        "fedex": "FedEx label",
    }
    title = titles.get(kind, "Print")
    extra = ""
    if kind == "fedex":
        extra = (
            f"<p>Ship to {escape(order['place'])} {escape(order['zip'])}. "
            f"Service: {escape(order['ship_choice'] or 'Choose a price on the desk first')}."
            " This sheet is the print layout. It is not a live carrier label.</p>"
        )
    if kind == "label":
        extra = f"<p>{escape(order['directions'])} · Qty {order['quantity']}</p>"
    if kind == "receipt":
        extra = (
            f"<p>Payment stays on this order: {escape(order['payment_status'])}.</p>"
            f"<p>{escape(order['drug'])} · Qty {order['quantity']}</p>"
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{escape(title)} — PharmPear</title>
  <link rel="stylesheet" href="/styles.css">
</head>
<body>
  <main class="desk">
    <p class="eyebrow">Print</p>
    <h1>{escape(title)}</h1>
    <p><strong>{escape(order['patient_name'])}</strong></p>
    <p>{escape(order['drug'] or '')}</p>
    <p>Prescriber: {escape(order['prescriber'] or 'Not on the order')}</p>
    {extra}
    <p><button class="btn btn-solid" type="button" onclick="window.print()">Print</button>
    <a href="/desk">Back to the desk</a></p>
  </main>
</body>
</html>
"""
