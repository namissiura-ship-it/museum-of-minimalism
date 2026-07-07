# -*- coding: utf-8 -*-
"""
Museum of Minimalism - demo site
A small Flask app that recreates the booking flow from the Figma design:
home -> exhibition -> plan your visit -> reservation/tickets -> checkout -> done
"""
import os
import time
import uuid
from datetime import datetime, timedelta

from flask import (
    Flask, render_template, request, redirect, url_for, session, jsonify, abort
)

from data import EXHIBITIONS, RECOMMENDATIONS, EVENTS, ARCHIVE, PROMO_CODES

app = Flask(__name__)
# In production, set the SECRET_KEY environment variable (e.g. in Render's
# dashboard) to a long random string. The fallback below is only for local
# development so the app still runs out of the box with `python app.py`.
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

RESERVATION_MINUTES = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_booking():
    return session.get("booking")


def get_order():
    return session.get("order")


def calc_totals(exhibition, ticket_type, quantity, promo_code=None):
    price = exhibition["tickets"][ticket_type]["price"]
    subtotal = price * quantity
    discount_fraction = PROMO_CODES.get((promo_code or "").upper(), 0)
    discount = round(subtotal * discount_fraction, 2)
    total = round(subtotal - discount, 2)
    return subtotal, discount, total


# ---------------------------------------------------------------------------
# Site-wide search index (used by the search overlay in base.html)
# ---------------------------------------------------------------------------
@app.context_processor
def inject_search_index():
    items = []

    items.append({"title": "Art", "subtitle": "Section", "type": "Section", "url": url_for("index") + "#art"})
    items.append({"title": "Exhibitions", "subtitle": "Section", "type": "Section", "url": url_for("index") + "#exhibitions"})
    items.append({"title": "Events", "subtitle": "Section", "type": "Section", "url": url_for("index") + "#events"})
    items.append({"title": "In Future", "subtitle": "Section", "type": "Section", "url": url_for("index") + "#future"})
    items.append({"title": "Archive", "subtitle": "Section", "type": "Section", "url": url_for("index") + "#archive"})

    for ex in EXHIBITIONS.values():
        items.append({
            "title": ex["title"],
            "subtitle": ex.get("artist", ""),
            "type": "Exhibition",
            "url": url_for("exhibition", slug=ex["slug"]),
        })

    for ev in EVENTS:
        items.append({
            "title": ev["title"],
            "subtitle": ev.get("type", ""),
            "type": "Event",
            "url": url_for("index") + "#events",
        })

    for a in ARCHIVE:
        items.append({
            "title": a["title"],
            "subtitle": (a.get("artist", "") or "")[:70],
            "type": "Archive",
            "url": url_for("index") + "#archive",
        })

    return {"search_index": items}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    featured = EXHIBITIONS["sequence"]
    other_exhibitions = [e for slug, e in EXHIBITIONS.items() if slug != "sequence"]
    return render_template(
        "index.html",
        featured=featured,
        exhibitions=other_exhibitions,
        events=EVENTS,
        archive=ARCHIVE,
    )


@app.route("/exhibition/<slug>")
def exhibition(slug):
    ex = EXHIBITIONS.get(slug)
    if not ex:
        abort(404)
    return render_template("exhibition.html", ex=ex)


@app.route("/exhibition/<slug>/plan", methods=["POST"])
def plan_visit(slug):
    ex = EXHIBITIONS.get(slug)
    if not ex:
        abort(404)

    date_label = request.form.get("date_label", "monday \u2022 May 16, 2021")
    time_slot = request.form.get("time_slot")
    interaction = request.form.get("interaction")

    if time_slot not in ex["time_slots"]:
        time_slot = ex["time_slots"][0]
    if interaction not in ex["interactions"]:
        interaction = next(iter(ex["interactions"]))

    session["booking"] = {
        "exhibition_slug": slug,
        "date_label": date_label,
        "time_slot": time_slot,
        "interaction": interaction,
    }
    session.pop("order", None)
    return redirect(url_for("reservation"))


@app.route("/reservation")
def reservation():
    booking = get_booking()
    if not booking:
        return redirect(url_for("index"))
    ex = EXHIBITIONS[booking["exhibition_slug"]]
    order = get_order() or {"ticket_type": "tandem", "quantity": 1}
    subtotal, discount, total = calc_totals(ex, order["ticket_type"], order["quantity"])
    return render_template(
        "reservation.html",
        ex=ex,
        booking=booking,
        order=order,
        total=total,
    )


@app.route("/reservation/update", methods=["POST"])
def reservation_update():
    """AJAX endpoint: recompute totals when quantities change."""
    booking = get_booking()
    if not booking:
        return jsonify({"error": "no booking"}), 400
    ex = EXHIBITIONS[booking["exhibition_slug"]]
    data = request.get_json(force=True)
    ticket_type = data.get("ticket_type", "tandem")
    quantity = max(0, int(data.get("quantity", 0)))
    if ticket_type not in ex["tickets"]:
        return jsonify({"error": "invalid ticket type"}), 400

    subtotal, discount, total = calc_totals(ex, ticket_type, quantity)
    return jsonify({
        "ticket_type": ticket_type,
        "quantity": quantity,
        "unit_price": ex["tickets"][ticket_type]["price"],
        "subtotal": subtotal,
        "total": total,
    })


@app.route("/reservation/confirm", methods=["POST"])
def reservation_confirm():
    booking = get_booking()
    if not booking:
        return redirect(url_for("index"))
    ex = EXHIBITIONS[booking["exhibition_slug"]]

    ticket_type = request.form.get("ticket_type", "tandem")
    quantity = max(1, int(request.form.get("quantity", 1)))
    if ticket_type not in ex["tickets"]:
        ticket_type = "tandem"

    contact_method = request.form.get("contact_method", "email")
    first_name = request.form.get("first_name", "").strip()
    email = request.form.get("email", "").strip()

    session["order"] = {
        "id": str(uuid.uuid4())[:8].upper(),
        "ticket_type": ticket_type,
        "quantity": quantity,
        "contact_method": contact_method,
        "first_name": first_name,
        "email": email,
        "promo_code": "",
        "expires_at": (datetime.utcnow() + timedelta(minutes=RESERVATION_MINUTES)).isoformat(),
    }
    return redirect(url_for("checkout"))


@app.route("/checkout")
def checkout():
    booking = get_booking()
    order = get_order()
    if not booking or not order:
        return redirect(url_for("index"))
    ex = EXHIBITIONS[booking["exhibition_slug"]]
    subtotal, discount, total = calc_totals(
        ex, order["ticket_type"], order["quantity"], order.get("promo_code")
    )
    expires_at = datetime.fromisoformat(order["expires_at"])
    seconds_left = max(0, int((expires_at - datetime.utcnow()).total_seconds()))
    return render_template(
        "checkout.html",
        ex=ex,
        booking=booking,
        order=order,
        subtotal=subtotal,
        discount=discount,
        total=total,
        seconds_left=seconds_left,
    )


@app.route("/checkout/promo", methods=["POST"])
def checkout_promo():
    booking = get_booking()
    order = get_order()
    if not booking or not order:
        return jsonify({"error": "no active order"}), 400
    ex = EXHIBITIONS[booking["exhibition_slug"]]

    data = request.get_json(force=True)
    code = (data.get("code") or "").strip().upper()

    if code and code not in PROMO_CODES:
        return jsonify({"error": "Invalid promo code"}), 400

    order["promo_code"] = code
    session["order"] = order
    subtotal, discount, total = calc_totals(ex, order["ticket_type"], order["quantity"], code)
    return jsonify({
        "valid": bool(code),
        "code": code,
        "subtotal": subtotal,
        "discount": discount,
        "total": total,
    })


@app.route("/checkout/pay", methods=["POST"])
def checkout_pay():
    booking = get_booking()
    order = get_order()
    if not booking or not order:
        return redirect(url_for("index"))
    ex = EXHIBITIONS[booking["exhibition_slug"]]

    expires_at = datetime.fromisoformat(order["expires_at"])
    if datetime.utcnow() > expires_at:
        session.pop("order", None)
        return redirect(url_for("reservation"))

    payment_method = request.form.get("payment_method", "card")
    agreed = request.form.get("agree_terms")
    if not agreed:
        subtotal, discount, total = calc_totals(
            ex, order["ticket_type"], order["quantity"], order.get("promo_code")
        )
        seconds_left = max(0, int((expires_at - datetime.utcnow()).total_seconds()))
        return render_template(
            "checkout.html",
            ex=ex, booking=booking, order=order,
            subtotal=subtotal, discount=discount, total=total,
            seconds_left=seconds_left,
            error="Please agree to the Terms of Use and Privacy Policy to continue.",
        )

    # NOTE: this is a demo - no real payment gateway is called here.
    # A production build would integrate Stripe/PayPal server-side APIs.
    order["payment_method"] = payment_method
    order["paid_at"] = datetime.utcnow().isoformat()
    session["order"] = order
    session["last_completed_order"] = order
    return redirect(url_for("done"))


@app.route("/done")
def done():
    booking = get_booking()
    order = session.get("last_completed_order")
    if not booking or not order:
        return redirect(url_for("index"))
    ex = EXHIBITIONS[booking["exhibition_slug"]]

    # Clear the active booking/order so a refresh doesn't replay checkout
    session.pop("booking", None)
    session.pop("order", None)

    return render_template(
        "done.html",
        ex=ex,
        booking=booking,
        order=order,
        recommendations=RECOMMENDATIONS,
    )


if __name__ == "__main__":
    # Locally this still runs with the Flask dev server on port 5000.
    # In production (Render), gunicorn imports `app` directly and this
    # block never executes - see Procfile / render.yaml.
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=debug, host="0.0.0.0", port=port)
