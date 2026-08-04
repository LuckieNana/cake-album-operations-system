"""
Push notification companion service for Cake Album Operations.
Runs alongside the main Streamlit app. Handles:
  - Serving the service worker JS file (must be served from site root to control the whole site)
  - Receiving push subscriptions from browsers and storing them in the same database Streamlit uses
  - A shared helper (send_push_to_department) that the main app calls to actually deliver a push

This is a separate small Flask app because Streamlit has no built-in way to receive raw POST
data from client-side JavaScript (no REST endpoint mechanism) - this fills that specific gap.
"""
from flask import Flask, request, jsonify, Response
import sqlite3
import json
import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_FILE = os.path.join(APP_DIR, "cake_album_operations_v1_1_TEST.db")
VAPID_PRIVATE_KEY_FILE = os.path.join(APP_DIR, "vapid_private_key.pem")
VAPID_CLAIMS = {"sub": "mailto:admin@cakealbumerp.com"}

app = Flask(__name__)


def connect():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.execute("""CREATE TABLE IF NOT EXISTS push_subscriptions(
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, department TEXT,
        endpoint TEXT NOT NULL UNIQUE, subscription_json TEXT NOT NULL, created_at TEXT NOT NULL)""")
    conn.commit()
    return conn


SERVICE_WORKER_JS = """
self.addEventListener('push', function(event) {
    let data = {title: 'Cake Album Operations', body: 'New update'};
    try { data = event.data.json(); } catch (e) {}
    event.waitUntil(
        self.registration.showNotification(data.title, {
            body: data.body,
            icon: '/push/icon.png',
            badge: '/push/icon.png',
            tag: data.tag || 'cake-album-notification',
            renotify: true
        })
    );
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({type: 'window'}).then(function(clientList) {
            for (const client of clientList) {
                if ('focus' in client) return client.focus();
            }
            if (clients.openWindow) return clients.openWindow('/');
        })
    );
});
"""


@app.route("/service-worker.js")
def service_worker_root():
    # This is the route that actually matters. A service worker's default scope is the
    # directory it's served from - registering it from /push/service-worker.js meant it
    # could only ever control pages under /push/, never the actual app at the site root.
    # Serving it from here instead gives it the whole site as its scope, which is what
    # navigator.serviceWorker.ready needs in order to ever resolve for the main app page.
    return Response(SERVICE_WORKER_JS, mimetype="application/javascript")


@app.route("/push/service-worker.js")
def service_worker():
    # Kept for backward compatibility - the /service-worker.js route above is the one
    # that actually matters for scope reasons.
    return Response(SERVICE_WORKER_JS, mimetype="application/javascript")


@app.route("/push/subscribe", methods=["POST"])
def subscribe():
    data = request.get_json(force=True)
    username = data.get("username", "").strip()
    department = data.get("department", "").strip()
    subscription = data.get("subscription")
    if not username or not subscription:
        return jsonify({"error": "missing username or subscription"}), 400
    endpoint = subscription.get("endpoint")
    conn = connect()
    conn.execute("""INSERT INTO push_subscriptions(username, department, endpoint, subscription_json, created_at)
                     VALUES(?,?,?,?, datetime('now'))
                     ON CONFLICT(endpoint) DO UPDATE SET username=excluded.username, department=excluded.department,
                     subscription_json=excluded.subscription_json""",
                 (username, department, endpoint, json.dumps(subscription)))
    conn.commit()
    conn.close()
    return jsonify({"status": "subscribed"})


@app.route("/push/unsubscribe", methods=["POST"])
def unsubscribe():
    data = request.get_json(force=True)
    endpoint = data.get("endpoint")
    if endpoint:
        conn = connect()
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
        conn.commit()
        conn.close()
    return jsonify({"status": "unsubscribed"})


@app.route("/push/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    connect().close()
    app.run(host="0.0.0.0", port=8502)
