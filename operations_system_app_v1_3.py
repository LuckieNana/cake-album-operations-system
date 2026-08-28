"""
Cake Album Operations Platform v1.5 — Release 5.1 (Full Notification Chain)
SQLite-based release connected to cake_album_operations_v1_1_TEST.db.

Core rule:
No cake moves forward without acceptance by the receiving stage.
If rejected, it returns to the sender with issue logging, correction, and resubmission.
"""

import sqlite3
import uuid
import hashlib
import base64
import os
try:
    import socket
    import urllib3.util.connection as _urllib3_cn
    _urllib3_cn.allowed_gai_family = lambda: socket.AF_INET
except Exception:
    pass  # if urllib3 isn't available yet for some reason, outbound calls just behave as before
import smtplib
try:
    import gspread
    from google.oauth2.service_account import Credentials as GoogleCredentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import time
import threading
import subprocess
import shutil
import json
import re
import secrets as secrets_mod
from pathlib import Path
from datetime import datetime, date, time as dtime, timedelta

import pandas as pd
import streamlit as st
from urllib.parse import urlparse

# Mobile/iPhone image handling. Pillow is optional so the app still starts even if
# the deployment image does not include it. When available we resize/compress phone
# photos before storing them; this keeps Streamlit websocket payloads small.
try:
    from PIL import Image, ImageOps
    PIL_AVAILABLE = True
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
        HEIF_AVAILABLE = True
    except Exception:
        HEIF_AVAILABLE = False
except Exception:
    PIL_AVAILABLE = False
    HEIF_AVAILABLE = False

APP_DIR = Path(__file__).parent
DATABASE_FILE = APP_DIR / "cake_album_operations_v1_1_TEST.db"
REFERENCE_IMAGE_DIR = APP_DIR / "cake_reference_images"
REFERENCE_IMAGE_DIR.mkdir(exist_ok=True)
REFERENCE_VIDEO_DIR = APP_DIR / "cake_reference_videos"
REFERENCE_VIDEO_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# PUSH / PWA ASSETS
#
# Streamlit cannot serve arbitrary URLs like "/service-worker.js" or accept a
# POST to "/push/subscribe". Everything below works *within* what Streamlit can
# actually do, and needs no separate backend service at all:
#   * Static files are served from ./static  ->  https://<host>/app/static/<file>
#     (requires  [server] enableStaticServing = true  in .streamlit/config.toml,
#      written automatically below).
#   * The push subscription is handed back to Python through a one-time URL
#     query parameter instead of a REST endpoint.
# ---------------------------------------------------------------------------
STATIC_DIR = APP_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)
STREAMLIT_CONFIG_DIR = APP_DIR / ".streamlit"


def _streamlit_package_static_dir():
    """Streamlit always serves its own package folder at  https://<host>/static/<file>
    with the correct MIME type, whether or not [server] enableStaticServing is on.
    /app/static/... only works when that config flag is on AND Streamlit has been
    restarted since — until then the reverse proxy answers with index.html, which is
    exactly the 'unsupported MIME type (text/html)' error the browser reported.
    Dropping a copy of the service worker in here removes that dependency."""
    try:
        import streamlit as _st_pkg
        d = Path(_st_pkg.__file__).parent / "static"
        return d if d.is_dir() else None
    except Exception:
        return None


# The browser tries these in order and uses the first one that really answers with
# JavaScript. Never assume a single URL works — that assumption is what broke push.
SERVICE_WORKER_URL = "/app/static/service-worker.js"
SERVICE_WORKER_URL_CANDIDATES = ["/app/static/service-worker.js", "/static/service-worker.js"]
MANIFEST_URL_CANDIDATES = ["/app/static/manifest.json", "/static/manifest.json"]
MANIFEST_URL = "/app/static/manifest.json"
ICON_URL = "/app/static/icon-192.png"

VAPID_PRIVATE_KEY_FILE = APP_DIR / "vapid_private_key.pem"
VAPID_PUBLIC_KEY_FILE = APP_DIR / "vapid_public_key.txt"
VAPID_CLAIMS = {"sub": "mailto:admin@cakealbumerp.com"}

SERVICE_WORKER_SOURCE = """
// Cake Album Operations service worker.
// Receives web-push messages from the server and shows them on the device,
// even when the browser tab is closed. Do not rename this file: the browser
// remembers the exact URL it was registered from.
self.addEventListener("install", (event) => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = { title: "Cake Album Operations", body: "You have a new job." };
  if (event.data) {
    try { data = Object.assign(data, event.data.json()); }
    catch (e) { data.body = event.data.text(); }
  }
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: data.icon || "icon-192.png",
      badge: data.icon || "icon-192.png",
      tag: data.tag || ("cake-album-" + Date.now()),
      renotify: true,
      requireInteraction: false,
      data: { url: data.url || "/" }
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) { if ("focus" in client) return client.focus(); }
      if (self.clients.openWindow) return self.clients.openWindow(target);
    })
  );
});
"""

PHONE_PUSH_SETUP_SOURCE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Cake Album phone notifications</title><link rel="manifest" href="manifest.json"><link rel="apple-touch-icon" href="icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="Cake Album">
<style>body{font-family:system-ui,sans-serif;max-width:620px;margin:0 auto;padding:28px 20px;color:#1a1420}button{padding:13px 18px;border:0;border-radius:8px;background:#4b2a5c;color:#fff;font-weight:700;font-size:16px}#status{margin-top:16px;white-space:pre-wrap;line-height:1.45}.note{padding:12px;background:#eaf3ff;border-radius:8px;margin:14px 0}</style></head>
<body><h1>Phone notifications</h1><div id="install" class="note" hidden><b>iPhone/iPad:</b> In Safari, tap Share → Add to Home Screen. Open Cake Album from that new icon, return here, then enable notifications. iOS 16.4+ is required.</div>
<p>This repair runs outside the embedded app panel, which mobile browsers require for reliable permission and push registration.</p>
<button id="enable">Enable / repair phone notifications</button><div id="status"></div>
<script>
const q=new URLSearchParams(location.search), status=document.getElementById('status');
const isIOS=/iPad|iPhone|iPod/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
const standalone=matchMedia('(display-mode: standalone)').matches||navigator.standalone===true;
if(isIOS&&!standalone) document.getElementById('install').hidden=false;
function keyBytes(s){const p='='.repeat((4-s.length%4)%4),r=atob((s+p).replace(/-/g,'+').replace(/_/g,'/'));return Uint8Array.from(r,c=>c.charCodeAt(0))}
function enc(s){const b=new TextEncoder().encode(s);let x='';b.forEach(v=>x+=String.fromCharCode(v));return btoa(x).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'')}
document.getElementById('enable').onclick=async()=>{try{
 if(!isSecureContext) throw Error('HTTPS is required.');
 if(isIOS&&!standalone) throw Error('Install this page to the Home Screen first, then open it from the Cake Album icon.');
 if(!('Notification'in window)||!('serviceWorker'in navigator)||!('PushManager'in window)) throw Error('This browser does not support web push. Use Safari on iPhone or Chrome on Android.');
 const permission=await Notification.requestPermission(); if(permission!=='granted') throw Error('Notification permission is '+permission+'. Enable it in the phone settings for Cake Album.');
 const reg=await navigator.serviceWorker.register('service-worker.js',{scope:'./'}); await navigator.serviceWorker.ready;
 const old=await reg.pushManager.getSubscription(); if(old) await old.unsubscribe();
 const sub=await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:keyBytes(q.get('k')||'')});
 await reg.showNotification('Cake Album — phone connected',{body:'Background notifications are now enabled on this phone.',icon:'icon-192.png',badge:'icon-192.png',tag:'cake-album-phone-ready'});
 const payload=JSON.stringify({username:q.get('u')||'',department:q.get('d')||'',subscription:sub.toJSON()});
 const back=new URL(q.get('r')||'/',location.origin); back.searchParams.set('push_sub',enc(payload));
 status.textContent='Success. Returning to Cake Album…'; setTimeout(()=>location.replace(back.toString()),900);
 }catch(e){status.textContent='Could not enable phone notifications:\n'+e.name+': '+e.message;}};
</script></body></html>"""

MANIFEST_SOURCE = json.dumps({
    "name": "Cake Album Operations",
    "short_name": "Cake Album",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "background_color": "#FFFFFF",
    "theme_color": "#4B2A5C",
    "icons": [
        {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
        {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
    ],
}, indent=2)

# 1x1 transparent PNG fallback so the manifest never points at a missing icon.
_FALLBACK_ICON_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def ensure_streamlit_static_config():
    """Turn on Streamlit's static file serving so /app/static/service-worker.js exists.
    Without this the browser gets a 404 for the service worker and push can never work."""
    try:
        STREAMLIT_CONFIG_DIR.mkdir(exist_ok=True)
        cfg = STREAMLIT_CONFIG_DIR / "config.toml"
        text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
        if "enableStaticServing" not in text:
            if "[server]" in text:
                text = text.replace("[server]", "[server]\nenableStaticServing = true", 1)
            else:
                text = (text.rstrip() + "\n\n[server]\nenableStaticServing = true\n").lstrip()
            cfg.write_text(text, encoding="utf-8")
            print("[PUSH] Wrote .streamlit/config.toml (enableStaticServing = true) — RESTART Streamlit once.", flush=True)
    except Exception as e:
        print(f"[PUSH] Could not write .streamlit/config.toml: {e}", flush=True)


def static_serving_enabled() -> bool:
    """Is Streamlit actually serving /app/static right now? The config file alone is not
    enough — Streamlit only reads it at startup, which is why a freshly written config
    still returns the HTML app shell (and the browser then refuses the service worker
    with 'unsupported MIME type (text/html)')."""
    try:
        from streamlit import config as _st_config
        return bool(_st_config.get_option("server.enableStaticServing"))
    except Exception:
        return False


def maybe_restart_for_static_serving():
    """Never restart the process automatically — an in-process restart loop is what used
    to take the site down with 502 Bad Gateway. Just log a clear instruction instead."""
    if static_serving_enabled():
        return
    print("[PUSH] Static serving is OFF. Restart the app service once "
          "(sudo systemctl restart cake-album) so push notifications can work.", flush=True)


def ensure_push_assets():
    """Write the service worker, the PWA manifest and the icons, and create the VAPID
    key pair on first run. Safe to call on every rerun — it only writes when needed."""
    ensure_streamlit_static_config()
    targets = [STATIC_DIR]
    pkg_static = _streamlit_package_static_dir()
    if pkg_static is not None:
        targets.append(pkg_static)
    for target in targets:
        try:
            sw = target / "service-worker.js"
            if not sw.exists() or sw.read_text(encoding="utf-8") != SERVICE_WORKER_SOURCE:
                sw.write_text(SERVICE_WORKER_SOURCE, encoding="utf-8")
            mf = target / "manifest.json"
            if not mf.exists() or mf.read_text(encoding="utf-8") != MANIFEST_SOURCE:
                mf.write_text(MANIFEST_SOURCE, encoding="utf-8")
            phone_setup = target / "phone-push-setup.html"
            if not phone_setup.exists() or phone_setup.read_text(encoding="utf-8") != PHONE_PUSH_SETUP_SOURCE:
                phone_setup.write_text(PHONE_PUSH_SETUP_SOURCE, encoding="utf-8")
            for name in ("icon-192.png", "icon-512.png"):
                icon = target / name
                if not icon.exists():
                    icon.write_bytes(_FALLBACK_ICON_PNG)
            print(f"[PUSH] Push assets ready in {target}", flush=True)
        except Exception as e:
            # A read-only site-packages folder is fine — the other location still works.
            print(f"[PUSH] Could not write push assets to {target}: {e}", flush=True)

    # VAPID keys identify this server to Google/Apple/Mozilla push services.
    if VAPID_PRIVATE_KEY_FILE.exists() and VAPID_PUBLIC_KEY_FILE.exists():
        return
    try:
        from py_vapid import Vapid01 as Vapid
    except ImportError:
        print("[PUSH] py_vapid is not installed — run: pip install pywebpush py-vapid", flush=True)
        return
    try:
        v = Vapid()
        v.generate_keys()
        v.save_key(str(VAPID_PRIVATE_KEY_FILE))
        try:
            public_key = v.public_key_urlsafe_base64()          # newer py_vapid
        except AttributeError:                                   # older py_vapid
            from cryptography.hazmat.primitives import serialization
            raw = v.public_key.public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )
            public_key = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        VAPID_PUBLIC_KEY_FILE.write_text(public_key, encoding="utf-8")
        print("[PUSH] Generated a new VAPID key pair.", flush=True)
    except Exception as e:
        print(f"[PUSH] VAPID key generation failed: {e}", flush=True)


@st.cache_resource
def _init_push_assets_once():
    """Runs ensure_push_assets() and maybe_restart_for_static_serving() exactly once
    per server process, shared across every user and every rerun. Without this guard,
    Streamlit re-executes this whole file's top-level code on every single interaction
    from every logged-in person - which meant these functions were reading and writing
    several files on disk dozens of times a second under real multi-user load. That
    file I/O contention is what was causing the server to stop responding in time,
    which Nginx then reported as a 502 Bad Gateway."""
    ensure_push_assets()
    maybe_restart_for_static_serving()
    return True


_init_push_assets_once()
VAPID_PUBLIC_KEY = (
    VAPID_PUBLIC_KEY_FILE.read_text(encoding="utf-8").strip()
    if VAPID_PUBLIC_KEY_FILE.exists() else None
)

# iPhone clips are often much larger than Android clips even when they are only a few seconds long.
# Videos are stored as files (not base64 inside SQLite), so we can safely accept larger phone clips
# without making the order database or Streamlit websocket payload enormous.
MAX_VIDEO_SIZE_BYTES = 150 * 1024 * 1024  # 150MB per clip; Streamlit's server limit remains the hard ceiling

APP_VERSION = "v2.9 — Release 18.0 (Reliable iPhone Video Upload)"
APP_TAGLINE = "Baking your ideas to life"
APP_LOGO_EMOJI = "🎂"
LOGO_BASE64_JPEG = "/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJDRENDg8QEBEQCgwSExIQEw8QEBD/2wBDAQMDAwQDBAgEBAgQCwkLEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBD/wAARCAH0AfQDASIAAhEBAxEB/8QAHQABAAICAwEBAAAAAAAAAAAAAAgJBgcDBAUBAv/EAGMQAAEDAwIEAgUFCQkKCQkJAAEAAgMEBREGBwgSITETQQkiUWFxFBUygZEjM0JSYnJ2ocEWFyQ3Q4KSsbQlNDhTY3OisrXRGCY5V5Wjs8LhJzVGVHWDpNLwZGZ0hZOUw9PU/8QAHAEBAAICAwEAAAAAAAAAAAAAAAUGAwQBAgcI/8QASBEAAgEDAQQIAwQFCgMJAAAAAAECAwQRBQYSITEHEzJBUWFxgSKRoRQzQrEjYsHR8BU0UlNygpKisuEWc8IXJDVDY6PS4vH/2gAMAwEAAhEDEQA/ALPUREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEUbNyuJ/dDafV9fSap2OqJtMxTFlLc6Wse4SxZ9WQyeGY8kYPIeUjOMnGVnG1/FHtFulJFb7Zfvmy7SdPm65AQyOd7GO+g/3cpz7lrRvKDn1W9iXg+H5lhrbK6vRs1qCouVFrO9BqaX9rdb3fPODbaIi2SvBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQHx7GSsdHKxr2OBDmuGQR7CFo/dXhA2n3Ijlrbdbm6ZvDgS2stsYbG53tkh6Nd78cpPmVvFFjq0adeO7UWUSGm6re6PWVxY1ZU5eKfPya5NeTyiJGj9091OGS6U+3++1BU3bSPO2C3amp2vmEQd2a556uaOuWuw9oBxzNAClTY77ZtS2unven7pS3G31TeeGpppRJG8e5w6d+h9hX41Fp2yasstXp7UdsguFurYzHPTzM5muH7CD1BHUEAjqoj00uo+CrcmK3VVVVXLavVFR6j3gudQSnuemB4jWjLsfTYMgcww3S3p2LSk80/Hvj6+K/ItHVW22ClO3gqd6lndjwhWxzcV+Gp37q4S7sMmSi4aGuo7nRQXG31MdTS1UTZoZonBzJI3DLXNI7ggg5XMpEpDTTwwiIhwEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBYxuXt9Y90NFXPRd/iBgr4S2OXly6nmA9SVvvacH3jIPQlZOi4lFTTjLkzLQr1LWrGtRluyi001zTXFMjXwn67uGnai6cOWvHCDUOk5pRb+Z399UnMXeqex5QQ4dcljh09UqSihxxe2uu2q3d0bxAaehIc6ZlPXBvQPliHZx/ykBez4Rn2qX9tuFJdrdS3WgmEtLWQsqIZB2fG9oc0/WCFo2U3Fyt5c4cv7L5fuLZtXa068bfXLdYjc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ymA+zr9YXxXNTUNFUf3xRwS/nxh39a6b9M6bleZJNPW17j3c6kjJP6ljezfhU/y/7m9Hp78bD/3P/oU45Hmu5Q2e73N3LbbVWVZzjEEDn9fqCuLitdsgPNBbqWM+1kLR/UF2QAOgAC5js2u+r9P9zHPp7q4/R2Kz51M/9BU9Y9gd6tRmM2nbS/yMlyWSTUhhYcZ/Ck5QPdk+xZ/YuB7fu7OYK+z2uzsdnLqy4Ru5fqi5yrIkW1T2eto9ttkBedOO0FZtW1OnTXo5P6vH0ISWH0dt2kax+pty6Snd154qG3ul+GHvc0/6K2Rp/gJ2atgY+9119vL2j1myVTYI3HyOI2hw+pykmi3qelWdPlTT9eP5lQvekjam/wCFS8kl+riP1ik/qYFprYXZvSJY+xbc2SOSMgslmphUSNI8w+XmcPqKzxjGRMEcbGsa0YDWjAAX1FuwpwprEFheRULm8uL2fWXNSU5eMm2/mwiIu5rBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQH/2Q=="
LOGO_DATA_URI = f"data:image/jpeg;base64,{LOGO_BASE64_JPEG}"
BAKERY_NAME = "Cake Album"
BAKERY_PHONE = "+256 775 315 971"
BAKERY_WHATSAPP = "+256 775 315 971"

st.set_page_config(
    page_title="Cake Album | Operations v1.2",
    page_icon="🎂",
    layout="wide",
    initial_sidebar_state="expanded",
)

PRODUCT_TYPES = ["Cake", "Cookies", "Cake Loaves", "Cake Layers", "Cupcakes"]
# These product lines skip Piling/Covering/Decoration/Studio QC entirely — Baking sends straight to Packaging.
SHORT_PIPELINE_PRODUCTS = {"Cookies", "Cake Loaves", "Cake Layers", "Cupcakes"}
PRODUCT_BADGE = {
    "Cake": ("🎂 CAKE", "var(--plum)"),
    "Cookies": ("🍪 COOKIES", "#5C3A21"),
    "Cake Loaves": ("🍞 CAKE LOAF", "#8A5A2B"),
    "Cake Layers": ("🍰 CAKE LAYERS", "#A6437A"),
    "Cupcakes": ("🧁 CUPCAKES", "#C2558B"),
}
# How each product line's "size" is captured: categorical size names, or numeric inches, or dozens count.
PRODUCT_SIZE_MODE = {
    "Cake": "inches",
    "Cookies": "category_small_med_big",
    "Cake Loaves": "category_small_med_large",
    "Cake Layers": "inches",
    "Cupcakes": "dozens",
}
DOZEN_OPTIONS = ["Half Dozen (6)", "1 Dozen (12)", "2 Dozens (24)", "3 Dozens (36)", "4 Dozens (48)", "Custom"]

STANDARD_FLAVOURS = ["Vanilla", "Chocolate", "Red Velvet", "Lemon", "Coconut", "Banana", "Strawberry",
                     "Blueberry", "Butterscotch", "Bubble Gum", "Caramel", "Marble", "Carrot", "Black Forest",
                     "Fruit Cake", "Lemon Poppy", "Madeira", "Courgette", "Confetti", "Vanilla Sponge",
                     "Orange", "White Forest", "Other"]
STANDARD_CAKE_SIZES = ["6", "7", "8", "9", "10", "12", "14", "16", "18", "20", "Custom"]
COOKIE_FLAVOURS = ["Coconut", "Ginger"]
CAKE_CATEGORIES = ["Wedding", "Anniversary", "Birthday", "Baby Shower", "Bridal Shower", "Introduction / Kuhingira",
                    "Graduation", "Christmas", "New Year's", "Baptism", "Confirmation", "Holy Communion",
                    "Corporate Event", "Other"]
DOZEN_COUNTS = {"Half Dozen (6)": 6, "1 Dozen (12)": 12, "2 Dozens (24)": 24, "3 Dozens (36)": 36, "4 Dozens (48)": 48}

DEPARTMENT_NAMES = [
    "Customer Care", "Production Planning", "Baking", "Filling / Piling", "Coating / Covering",
    "Decoration", "Design & Innovation", "Studio / Final QC", "Packaging", "Dispatch / Driver",
    "Finance", "Procurement", "Owner / Admin",
]

FALLBACK_BAKERS = ["Billy", "Uncle Joe", "Ronnie", "Martin", "Andre"]
FALLBACK_PILERS = ["Zakia", "Eriya", "Lawrence", "Bobi", "Angel", "Desmond", "Zaitun", "Aisha"]
FALLBACK_COVERERS = ["Zakia", "Eriya", "Lawrence", "Bobi", "Angel", "Desmond", "Zaitun", "Aisha"]
FALLBACK_DECORATORS = ["Zakia", "Eriya", "Lawrence", "Bobi", "Angel", "Desmond", "Zaitun", "Aisha"]
FALLBACK_DRIVERS = ["Cyrus", "Company Driver", "Isaac", "Other"]

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600;9..144,700&family=Inter:wght@400;500;600;700;800&display=swap');

:root{
  --ink:#1A1420; --plum:#4B2A5C; --plum-deep:#241129; --plum-mid:#6B3F7D;
  --cherry:#C81E1E; --gold:#B8935A;
  --canvas:#F7F5F3; --surface:#FFFFFF; --border:#E7E2E6; --border-strong:#D8D0D6;
  --muted:#6E6577; --muted-soft:#9891A0;
  --success:#1E7F4F; --success-bg:#EAF6EF; --warning:#B5720E; --warning-bg:#FBF1E2; --danger:#C81E1E; --danger-bg:#FBEAEA;
}

html, body, .stApp{font-family:'Inter',-apple-system,sans-serif; background:var(--canvas)!important; color:var(--ink);}
.block-container{padding-top:1.4rem; padding-bottom:2.5rem; max-width:1360px;}

h1,h2,h3,h4{font-family:'Fraunces',Georgia,serif; color:var(--plum-deep)!important; font-weight:600; letter-spacing:-.01em;}
h3{font-size:1.15rem; font-weight:600; margin-top:.2rem;}
p, span, div, label, .stMarkdown, [data-testid='stWidgetLabel'] p{color:var(--ink);}
[data-testid='stWidgetLabel'] p{font-weight:600; font-size:.86rem; color:var(--muted)!important; text-transform:none;}
.stCaption, [data-testid='stCaptionContainer']{color:var(--muted-soft)!important;}

/* Sidebar: solid, confident, quiet */
[data-testid='stSidebar']{background:var(--plum-mid); border-right:1px solid rgba(0,0,0,.15);}
[data-testid='stSidebar'] *{color:#FFFFFF!important;}
[data-testid='stSidebar'] [data-testid='stCaptionContainer']{color:#EFE6F3!important;}
[data-testid='stSidebar'] input,[data-testid='stSidebar'] textarea,[data-testid='stSidebar'] div[data-baseweb='select']>div{background:#FFFFFF!important;color:var(--ink)!important; border-radius:8px!important;}
[data-testid='stSidebar'] hr{border-color:rgba(255,255,255,.14)!important;}
[data-testid='stSidebar'] .stButton>button{background:rgba(255,255,255,.14)!important;box-shadow:none!important;border:1px solid rgba(255,255,255,.3)!important;font-weight:600!important;}
[data-testid='stSidebar'] .stButton>button:hover{background:rgba(255,255,255,.24)!important;}

/* Page header: lighter purple band (was near-black — hard to read), thin gold scalloped edge as the one signature flourish */
.ca-header{position:relative; padding:26px 30px 22px; margin-bottom:24px; border-radius:14px;
  background:var(--plum-mid); box-shadow:0 1px 2px rgba(26,20,32,.06), 0 8px 24px rgba(26,20,32,.16);
  background-image:radial-gradient(circle at 6px 100%, transparent 6px, var(--plum-mid) 7px);
  background-size:14px 8px; background-repeat:repeat-x; background-position:bottom;
  border-bottom:3px solid var(--gold);}
.ca-header h1{margin:0; font-size:1.7rem; color:#FFFFFF!important; font-weight:600;}
.ca-header p{margin:6px 0 0; color:#F3EAF7!important; font-size:.95rem; font-weight:400; font-style:normal;}

/* Staff greeting: a quiet single line, not a shouting banner */
.ca-greeting{display:flex; align-items:center; gap:10px; padding:10px 16px; margin:0 0 18px;
  background:var(--surface); border:1px solid var(--border); border-left:3px solid var(--gold);
  border-radius:10px; font-size:.92rem; color:var(--muted);}
.ca-greeting b{color:var(--plum-deep)!important;}

/* Content cards */
.ca-card{background:var(--surface); border:1px solid var(--border); border-radius:12px;
  padding:18px 22px; margin:12px 0 18px; box-shadow:0 1px 2px rgba(26,20,32,.04); line-height:1.7;}
.ca-card *{color:var(--ink)!important;}
.ca-card b{color:var(--plum-deep)!important; font-weight:600;}

/* KPI tiles */
.ca-kpi{background:var(--surface); border:1px solid var(--border); border-top:3px solid var(--plum);
  border-radius:10px; padding:16px 18px; box-shadow:0 1px 2px rgba(26,20,32,.04);}
.ca-kpi .label{font-size:.72rem; text-transform:uppercase; letter-spacing:.05em; font-weight:700; color:var(--muted-soft)!important;}
.ca-kpi .value{font-family:'Fraunces',serif; font-size:1.9rem; font-weight:600; color:var(--plum-deep)!important; line-height:1.25;}
.ca-kpi .note{font-size:.82rem; color:var(--muted)!important; font-weight:500;}

/* Inputs */
input,textarea{background:var(--surface)!important; color:var(--ink)!important;
  border-color:var(--border-strong)!important; border-radius:8px!important;}
input:focus,textarea:focus{border-color:var(--plum)!important; box-shadow:0 0 0 3px rgba(75,42,92,.12)!important;}

/* Dropdown menus (selectbox/multiselect) — light purple, everywhere, no gaps.
   This Streamlit version renders these under [data-testid='stSelectbox'] /
   [data-testid='stMultiSelect'] for the closed box and [data-testid='stSelectboxVirtualDropdown']
   for the open option list — NOT the older data-baseweb attributes (kept below as a fallback
   for other Streamlit versions that still use them). */
[data-testid='stSelectbox'], [data-testid='stSelectbox'] *,
[data-testid='stMultiSelect'], [data-testid='stMultiSelect'] *,
div[data-baseweb='select'], div[data-baseweb='select'] *{
  background-color:#F0E6F5!important; color:#1A1420!important;}

[data-testid='stSelectboxVirtualDropdown'], [data-testid='stSelectboxVirtualDropdown'] *,
div[data-baseweb='popover'], div[data-baseweb='popover'] *,
div[data-baseweb='menu'], div[data-baseweb='menu'] *,
ul[role='listbox'], ul[role='listbox'] *,
div[role='listbox'], div[role='listbox'] *{
  background-color:#F0E6F5!important; color:#1A1420!important;}

[data-testid='stSelectboxVirtualDropdown'] [role='option']:hover,
[data-testid='stSelectboxVirtualDropdown'] [role='option'][aria-selected='true'],
div[role='option']:hover, li[role='option']:hover,
div[role='option'][aria-selected='true'], li[role='option'][aria-selected='true']{
  background-color:#DCC5EC!important; color:#1A1420!important;}

/* Buttons: solid, restrained, confident, and readable */
.stButton>button,.stFormSubmitButton>button{border-radius:8px; font-weight:700; min-height:40px;
  background:var(--plum-mid); color:#fff!important; border:1px solid var(--plum-mid); box-shadow:0 1px 2px rgba(26,20,32,.08);
  transition:background .12s ease, box-shadow .12s ease;}
.stButton>button:hover,.stFormSubmitButton>button:hover{background:var(--plum); box-shadow:0 2px 6px rgba(26,20,32,.16);}
.stButton>button:disabled{background:#EDEAEF!important; color:var(--muted-soft)!important; border-color:var(--border)!important; box-shadow:none!important;}

/* Tabs: clean underline style, not pill soup */
.stTabs [data-baseweb='tab-list']{gap:22px; background:transparent; padding:0 0 0; border-bottom:1px solid var(--border); border-radius:0;}
.stTabs [data-baseweb='tab']{background:transparent!important; border:none!important; border-radius:0!important;
  font-weight:600; color:var(--muted)!important; padding:8px 2px!important; margin-bottom:-1px;}
.stTabs [aria-selected='true']{background:transparent!important; color:var(--plum-deep)!important;
  border-bottom:2px solid var(--cherry)!important;}
.stTabs [aria-selected='true'] *{color:var(--plum-deep)!important;}

/* Radio / checkbox labels */
.stRadio label p, .stCheckbox label p{font-weight:500!important; color:var(--ink)!important;}

/* Dataframes */
[data-testid='stDataFrame']{background:var(--surface); border-radius:10px; border:1px solid var(--border);
  box-shadow:0 1px 2px rgba(26,20,32,.04); overflow:hidden;}
[data-testid='stDataFrame'] *{color:var(--ink)!important;}

/* Status-style alerts, kept quiet */
.stAlert{border-radius:10px!important;}

hr{border-color:var(--border)!important;}

@media print{
@page{margin:6mm}
body *{visibility:hidden!important}
.print-note,.print-note *{visibility:visible!important}
.print-note{position:fixed!important;left:0!important;top:0!important;margin:0!important;box-shadow:none!important;background:#fff!important;color:#000!important;page-break-inside:avoid!important;break-inside:avoid!important;page-break-after:avoid!important}
.print-note *{color:#000!important;page-break-inside:avoid!important}
.print-note.print-note--full{width:110mm!important;max-width:110mm!important;border:2px solid #111!important;padding:6mm!important;font-family:Arial,sans-serif!important}
.print-note.print-note--label{width:76mm!important;max-width:76mm!important;border:1px dashed #111!important;padding:3mm!important;font-family:Arial,sans-serif!important;font-size:11px!important}
[data-testid='stSidebar'],header,footer,.stButton,.stAlert,.stTabs [data-baseweb='tab-list']{display:none!important}
}
</style>
"""


DARK_MODE_CSS_OVERRIDE = """
<style>
:root{
  --ink:#EDE9F0!important; --canvas:#1A1420!important; --surface:#2A2130!important;
  --border:#453A4E!important; --border-strong:#5A4C66!important; --plum-deep:#F0E6F5!important;
  --muted:#B8ADC2!important; --muted-soft:#9891A0!important;
  --success-bg:#173324!important; --warning-bg:#3A2C12!important; --danger-bg:#3A1A1A!important;
}
h1,h2,h3,h4{color:#F0E6F5!important;}
[data-testid='stDataFrame'], [data-testid='stTable']{background:var(--surface)!important;}
[data-testid='stExpander']{background:var(--surface)!important; border-color:var(--border)!important;}
.stButton>button{background:var(--surface)!important; color:var(--ink)!important; border-color:var(--border-strong)!important;}
[data-testid='stMetric']{background:var(--surface)!important;}
</style>
"""


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)
    if st.session_state.get("theme_mode") == "dark":
        st.markdown(DARK_MODE_CSS_OVERRIDE, unsafe_allow_html=True)


def render_theme_toggle():
    """Dark mode is easier on the eyes at night and genuinely reduces battery drain on
    phone OLED screens (dark pixels use meaningfully less power than light ones) -
    stored in session_state so it holds for the rest of this session."""
    current = st.session_state.get("theme_mode", "light")
    with st.sidebar:
        choice = st.radio("Appearance", ["☀️ Light", "🌙 Dark"], index=(1 if current == "dark" else 0),
                           key="theme_toggle_radio", horizontal=True)
        new_mode = "dark" if "Dark" in choice else "light"
        if new_mode != current:
            st.session_state["theme_mode"] = new_mode
            st.rerun()


DECORATOR_WEEK = {
    "Monday": "A new week means another opportunity to create something unforgettable. Every smooth finish, every color and every detail represents Cake Album. Decorate with excellence because somewhere, a customer is waiting to smile because of what your hands create. Your dreams matter too — and when we grow Cake Album together, we create room for all of us to grow.",
    "Tuesday": "Never look at it as \"just another cake.\" To the customer, it may be a birthday they prayed to see, a wedding they dreamed about or a surprise for someone they love. Give every cake the attention you would want for your own special day. The customer comes first.",
    "Wednesday": "Great decorators do not hide mistakes — they correct them before the customer ever sees them. Check your work, support your teammates and speak up when something is wrong. Excellence is a team responsibility. Together, we can build the best cake company and move closer to our individual dreams.",
    "Thursday": "Your hands are part of the Cake Album story. Be creative, be neat and never settle for \"good enough.\" The small detail you choose to perfect today may be the reason a customer recommends us tomorrow.",
    "Friday": "Speed matters, but quality matters too. Plan ahead, pick your toppers on time and communicate early when you need support. A happy customer should never suffer because we failed to work together behind the scenes.",
    "Saturday": "Today may be busy, but busy days are when champions show who they are. Help one another, protect quality and remember: we win as one Cake Album team. When the company grows, opportunities grow and the dreams we carry become more possible.",
    "Sunday": "Take pride in what you create. Somewhere today, people will gather around a cake you decorated, take pictures and create memories. Make it worthy of that moment. We are not simply decorating cakes — we are baking ideas to life.",
}

BAKING_WEEK = {
    "Monday": "Every beautiful Cake Album cake begins with you. Before the decoration, before the pictures and before the customer smiles, there is a baker who must get it right. Start the week with accuracy, discipline and pride. Our customer deserves the best from the very first layer.",
    "Tuesday": "Measure carefully. Bake correctly. Protect freshness. A customer may never see the kitchen, but they will taste every decision made inside it. Let every bite remind them why they chose Cake Album.",
    "Wednesday": "Wastage weakens the team and carelessness affects the customer. Use materials responsibly, record what you use and protect company resources as if they were your own. A stronger Cake Album creates stronger opportunities for all of us.",
    "Thursday": "Never pass a problem to the next department. If a layer is not right, speak up. If something needs correction, correct it. Quality is not somebody else's job. We are one team serving one customer.",
    "Friday": "Plan ahead and communicate. The decorator depends on you, packaging depends on production and the customer depends on all of us. When one department delays, the customer feels it. Let teamwork be our strength today.",
    "Saturday": "Pressure is not permission to lower standards. Even on our busiest day, the customer deserves a cake that is fresh, delicious and made with care. Excellence under pressure is what separates the best from the rest.",
    "Sunday": "Be proud of your craft. The cakes you bake help families celebrate birthdays, weddings and milestones. As we work together to grow Cake Album, may we also build a future where the dreams of every hardworking team member can grow.",
}

CUSTOMER_CARE_WEEK = {
    "Monday": "You are often the first voice of Cake Album. Before the customer tastes our cake, they experience your attitude. Respond with warmth, speed and care. Make every customer feel that choosing Cake Album was the right decision.",
    "Tuesday": "Listen carefully. Sometimes the customer is not simply ordering cake — they are trusting us with an important moment. Ask the right questions, record the correct details and protect their vision from the first message to delivery.",
    "Wednesday": "One wrong date, one missed instruction or one delayed response can affect the entire team. Accuracy is customer care. Double-check every order because excellence begins with correct information.",
    "Thursday": "Never allow a customer to feel ignored. Even when you do not have the final answer, communicate. A simple update can protect trust. Our customer is our priority and communication is one of the greatest ways we show it.",
    "Friday": "Sell with honesty and serve with heart. Do not only think about closing an order — think about creating a customer who will confidently return and recommend Cake Album to others.",
    "Saturday": "Busy inbox? Many calls? Stay focused. Every name on your screen represents a real person waiting for our service. Handle each customer with patience and remember that teamwork can turn a difficult day into a successful one.",
    "Sunday": "The relationships you build today can become Cake Album customers for years. Serve people well. As the company grows through happy customers, we create more opportunities for the team and move closer to the dreams we each carry.",
}

DESIGN_INNOVATION_WEEK = {
    "Monday": "Keith, every great design begins with an idea and the courage to create it. Your work helps Cake Album turn imagination into something real. Start the week thinking ahead because innovation gives the entire team an advantage.",
    "Tuesday": "A topper may look like one small part of a cake, but to the customer it may complete the entire vision. Treat every detail as important. The customer sees the final picture, so every contribution matters.",
    "Wednesday": "Plan early, communicate with decorators and never allow preventable delays to become customer problems. Innovation is not only creativity — it is creativity delivered on time.",
    "Thursday": "Challenge yourself to make today's work better than yesterday's. New ideas, cleaner designs and smarter processes are how Cake Album stays ahead. Your creativity can help build the company we all dream of.",
    "Friday": "When a topper is ready, communicate. When there is a delay, communicate early. Great teamwork happens when information moves as quickly as the work itself. Protect the customer's deadline.",
    "Saturday": "Urgent does not mean careless. Under pressure, stay creative, accurate and focused. The customer should see excellence — not the pressure that happened behind the scenes.",
    "Sunday": "Never underestimate the value of your ideas. Cake Album was built from ideas, dreams and people willing to work. Keep creating. As we build the company's dream together, we create space for your dreams to grow too.",
}

DELIVERY_WEEK = {
    "Monday": "You are the final face of Cake Album before the cake reaches the customer. Deliver with care, respect and professionalism. The customer should feel confident and valued when they see you arrive.",
    "Tuesday": "Drive safely and protect every cake. The team has spent hours planning, baking and decorating the order you carry. Handle it as a valuable promise made to our customer.",
    "Wednesday": "Communicate early. If there is traffic, a delay or a delivery concern, update the team. Silence creates confusion; communication protects the customer experience.",
    "Thursday": "Never compromise Cake Album procedures. Confirm payments correctly and work with Finance. Protecting company money protects salaries, jobs, growth and the future we are building together.",
    "Friday": "A polite greeting and respectful attitude can complete a beautiful customer experience. You are not \"just delivering.\" You are representing every Cake Album employee who worked on that order.",
    "Saturday": "Many deliveries require focus and planning. Follow your sequence, protect the cakes and communicate. The goal is simple: every cake reaches the correct customer safely and on time.",
    "Sunday": "Every successful delivery completes a promise. Be proud of being the person who brings the celebration to the customer's door. Together, every department makes that moment possible.",
}

GENERAL_MANAGER_WEEK = {
    "Monday": "Suzan, leadership means helping every department understand that we have one mission: a happy customer. Set the standard, communicate clearly and help the team begin the week with purpose.",
    "Tuesday": "Do not wait for small problems to become customer complaints. Observe the operation, ask questions and act early. Great management prevents problems before the customer experiences them.",
    "Wednesday": "Build people, not only processes. Correct where necessary, appreciate good work and help the team improve. Cake Album becomes stronger when our people become stronger.",
    "Thursday": "Every department must work as one system. Customer Care, Baking, Decoration, Innovation, Packaging and Delivery are connected. Help information move and remove the gaps that create delays.",
    "Friday": "Accountability and kindness can exist together. Hold the team to high standards while reminding them why those standards matter — our customers trust Cake Album with important moments.",
    "Saturday": "Pressure reveals leadership. Stay organized, prioritize urgent issues and keep the team focused on solutions. The customer should experience excellence regardless of how busy operations become.",
    "Sunday": "Leadership is helping people see that their work today can build a better tomorrow. Remind the team that Cake Album's growth can create opportunities, skills and possibilities that help all of us move closer to our dreams.",
}

PROCUREMENT_WEEK = {
    "Monday": "Teddy, great cakes require the right materials at the right time. Procurement is where preparation begins. Plan carefully because a missing item can eventually become a disappointed customer.",
    "Tuesday": "Buy with quality and responsibility in mind. The cheapest option is not always the best option and unnecessary spending weakens the company. Protect both quality and Cake Album resources.",
    "Wednesday": "Record what is requested, what is purchased and what is issued. Good records protect the company, protect employees and help us identify wastage early. Accountability helps everyone.",
    "Thursday": "Communicate shortages before they become emergencies. The team cannot plan with information they do not have. Early communication protects production and protects the customer's order.",
    "Friday": "Every bag of flour, box, board and ingredient has value. Treat company materials responsibly. When we reduce wastage, Cake Album becomes stronger and better able to invest in its people.",
    "Saturday": "Busy production requires smart planning. Look ahead at upcoming orders and prepare the team. Procurement should help us prevent last-minute panic.",
    "Sunday": "Your work supports every cake that leaves Cake Album. When resources are managed with integrity and wisdom, the company grows. And when we grow together, the dreams of hardworking people become more possible.",
}

PACKAGING_WEEK = {
    "Monday": "Packaging is the final quality checkpoint before the cake leaves Cake Album. Check the cake, the box, the delivery note and every special instruction carefully. A well-packaged cake protects the customer's order and the reputation of the entire team.",
    "Tuesday": "The customer may not see the work that happened behind the scenes, but they will notice neatness, cleanliness and professionalism. Package every cake as if it were being delivered to someone important to you.",
    "Wednesday": "Never rush past a mistake. Confirm the customer's name, order details, location, balance, cake size and special instructions before dispatch. One careful check can prevent a disappointed customer.",
    "Thursday": "Packaging connects production to delivery. Communicate clearly with decorators, Customer Care and the driver. When information is correct and the cake is secure, the customer receives the experience we promised.",
    "Friday": "Protect every cake from damage, heat, movement and contamination. The team has invested time, skill and materials into every order. Handle each cake with care because every order carries the Cake Album name.",
    "Saturday": "Busy days require greater focus, not lower standards. Confirm the right cake goes to the right customer, attach the correct delivery note and prepare each order on time. Teamwork keeps the customer from feeling the pressure behind the scenes.",
    "Sunday": "Be proud of your role. Packaging is not simply putting a cake in a box; it is preparing a customer's celebration for its final journey. When we protect quality and work together, Cake Album grows and the dreams we carry become more possible.",
}

PRODUCTION_PLANNING_WEEK = {
    "Monday": "Every order that reaches Baking, Piling, Covering or Decoration on time begins with the plan you set today. Assign clearly, check urgent orders early and give every department what they need to succeed. A well-planned Monday protects the whole week ahead.",
    "Tuesday": "You are the bridge between a customer's order and the hands that bring it to life. Confirm baker, piler, coverer and decorator assignments correctly, and never leave a topper or urgent request unplanned. Small oversights here become big problems downstream.",
    "Wednesday": "When inventory is tight or an order is urgent, do not guess — check, confirm and communicate. Protecting accuracy today prevents confusion for every department relying on your plan.",
    "Thursday": "Production Planning connects every part of Cake Album. Baking waits on your assignment, Decoration waits on your topper coordination, and the customer waits on all of us. Keep the information moving and keep it correct.",
    "Friday": "Plan ahead for the weekend rush. Confirm urgent orders, reserve inventory where needed and give each department enough notice to deliver excellent work, not rushed work.",
    "Saturday": "Busy days test a plan's strength. Stay organized, prioritize the most urgent orders and support any department that falls behind. A clear plan is how Cake Album stays calm during chaos.",
    "Sunday": "Your planning is quiet work — customers rarely see it, but they always feel it when it's done right. As Cake Album grows, so does the need for sharp, dependable planning. Keep building it well, and the opportunities will grow with you.",
}

FINANCE_WEEK = {
    "Monday": "Every deposit confirmed, every balance tracked and every payment approved keeps Cake Album moving. Start the week with accuracy — production cannot begin safely until you have confirmed the numbers are right.",
    "Tuesday": "Money mistakes are quiet at first but loud later. Double-check every confirmation, every deposit and every balance before it moves forward. Protecting the company's finances protects every job that depends on them.",
    "Wednesday": "Record everything and confirm everything. A missed balance or an unconfirmed payment can create confusion for Customer Care, Production and the customer. Accuracy today prevents problems tomorrow.",
    "Thursday": "Finance connects to every department — Customer Care needs your confirmation, Dispatch needs your clearance, and the business needs your discipline. Communicate clearly and quickly so no order is held up unnecessarily.",
    "Friday": "Reconcile carefully as the week's orders move toward delivery. Confirm what has been paid, flag what is still owed and keep the numbers honest. A trustworthy Finance team is the backbone of a trustworthy company.",
    "Saturday": "Busy days bring more transactions and more room for error. Slow down just enough to confirm each one correctly. Precision under pressure is what protects Cake Album's money and its reputation.",
    "Sunday": "The trust customers and teammates place in Cake Album depends on the integrity of what you do. Manage our finances with honesty and care, and know that a financially strong Cake Album creates more opportunity for everyone who works here.",
}

STUDIO_QC_WEEK = {
    "Monday": "You are the last check before a cake leaves Cake Album's hands. Look closely, check thoroughly and never let a preventable mistake reach the customer. Excellence stops here before it reaches them.",
    "Tuesday": "A small flaw missed today can become a customer's disappointment tomorrow. Check the size, the design, the message and the finish against what was promised. The customer's expectation is the standard you are protecting.",
    "Wednesday": "Do not rush the final check to save time. If something is wrong, send it back before it reaches Packaging. A short delay now is far better than an unhappy customer later.",
    "Thursday": "Studio and Final QC connect Decoration to Packaging and Packaging to the customer. Communicate clearly when something needs correction, and confirm clearly when a cake is ready. Your sign-off carries real weight.",
    "Friday": "Protect the standard even when the week is ending and orders are stacking up. A cake that passes your check should be a cake Cake Album is proud to deliver, without exception.",
    "Saturday": "Busy days bring more cakes through your hands and more chances for something to slip by. Stay sharp, stay thorough, and remember: you are the last line of defense for quality.",
    "Sunday": "Every cake that passes your inspection carries your seal of approval, even if the customer never knows your name. Take pride in that responsibility. A strong Cake Album is one where quality is protected at every single stage, including yours.",
}

DEPARTMENT_GREETINGS = {
    "Filling / Piling": DECORATOR_WEEK,
    "Coating / Covering": DECORATOR_WEEK,
    "Decoration": DECORATOR_WEEK,
    "Baking": BAKING_WEEK,
    "Customer Care": CUSTOMER_CARE_WEEK,
    "Design & Innovation": DESIGN_INNOVATION_WEEK,
    "Dispatch / Driver": DELIVERY_WEEK,
    "Owner / Admin": GENERAL_MANAGER_WEEK,
    "Procurement": PROCUREMENT_WEEK,
    "Packaging": PACKAGING_WEEK,
    "Production Planning": PRODUCTION_PLANNING_WEEK,
    "Finance": FINANCE_WEEK,
    "Studio / Final QC": STUDIO_QC_WEEK,
}


def page_header(title: str, subtitle: str = ""):
    st.markdown(f"<div class='ca-header'><h1>{title}</h1><p>{subtitle}</p></div>", unsafe_allow_html=True)
    render_staff_greeting()
    render_department_notifications()
    render_team_chat()
    render_idea_submission_widget()
    st.caption("If this page has been open a while, tap the button below to make sure you're seeing "
               "the latest assignments before acting on anything.")
    render_auto_refresh_toggle(key_suffix="_top")


def render_auto_refresh_toggle(key_suffix=""):
    """Manual refresh for queue tables and order data. Notifications themselves are already
    handled independently and reliably by the fragment above (refreshes every 10s on its own,
    without reloading the page) — this button is just for someone who wants to force queue
    tables to update right now, without the battery cost or visible flicker of an automatic
    full-page rerun every 30 seconds."""
    if st.button("🔄 Check now", key=f"manual_refresh_btn{key_suffix}_{st.session_state.get('_refresh_widget_calls', 0)}"):
        st.session_state["_refresh_widget_calls"] = st.session_state.get("_refresh_widget_calls", 0) + 1
        st.rerun()


def render_idea_submission_widget():
    """Lets anyone, on any page, contribute a creativity/innovation idea — reviewed centrally
    in Design & Innovation. Uses a per-run call counter for the widget keys since some pages
    (like Dispatch/Driver) render more than one page_header in the same script run."""
    call_n = st.session_state.get("_idea_widget_calls", 0) + 1
    st.session_state["_idea_widget_calls"] = call_n
    with st.expander("💡 Suggest an Idea (Creativity & Innovation)"):
        st.caption("Got an idea for a better process, a design touch, a way to save money or time? Share it here — Design & Innovation reviews every submission.")
        a, b = st.columns(2)
        idea_title = a.text_input("Idea title", key=f"idea_title_input_{call_n}")
        idea_category = b.selectbox("Category", ["Design Idea", "Process Improvement", "Cost Saving", "Customer Experience", "Other"], key=f"idea_category_input_{call_n}")
        idea_desc = st.text_area("Describe your idea", key=f"idea_desc_input_{call_n}")
        contributor = st.text_input("Your name", value=st.session_state.get("staff_name", ""), key=f"idea_contributor_input_{call_n}")
        if st.button("Submit Idea", key=f"idea_submit_btn_{call_n}"):
            if not idea_title.strip() or not idea_desc.strip():
                st.error("Give it a title and a short description.")
            else:
                with connect() as conn:
                    conn.execute("""INSERT INTO creativity_contributions(contributor_name, department, idea_title, idea_description, category, status, submitted_at)
                                    VALUES(?,?,?,?,?,?,?)""",
                                 (contributor.strip() or "Anonymous", st.session_state.get("department", ""), idea_title.strip(), idea_desc.strip(), idea_category, "Submitted", now_iso()))
                    conn.commit()
                st.success("Idea submitted — thank you! Design & Innovation will review it.")
                st.rerun()


def render_staff_greeting():
    """Personal welcome banner shown at the top of every department page.
    Uses a department- and day-specific motivational message where one has been provided,
    falling back to a generic message otherwise."""
    name = first_name(st.session_state.get("staff_name", "").strip())
    if not name or name == "—":
        return
    role_tag = " · Head of Department" if st.session_state.get("is_hod") else ""
    dept = st.session_state.get("department", "")
    today_name = datetime.now().strftime("%A")
    message = DEPARTMENT_GREETINGS.get(dept, {}).get(
        today_name,
        "Hope you're ready to achieve today's goal — keep pushing. The customer is king; quality comes first.")
    st.markdown(
        f"<div class='ca-greeting'>👋 <span><b>Hello {name}{role_tag}.</b> {message}</span></div>",
        unsafe_allow_html=True,
    )


def _notification_rows_for_user(notes, departments, staff_name):
    """Return unread notifications visible to this login.

    Department-wide notices have a blank target_person. Personal notices are only
    shown to the matching employee. Matching is case-insensitive and also accepts
    the first name because Cake Album staff accounts commonly use one name.
    """
    if notes.empty:
        return notes

    unread = notes[notes["notification_status"].fillna("").str.casefold() == "unread"].copy()
    unread = unread[unread["target_department"].isin(departments)]
    if unread.empty:
        return unread

    login = str(staff_name or "").strip().casefold()
    login_first = login.split()[0] if login else ""

    def visible_to_login(target):
        target = str(target or "").strip().casefold()
        if not target or target in {"none", "nan", "—"}:
            return True  # department-wide notification
        if not login:
            return False
        target_first = target.split()[0]
        return target == login or (login_first and target_first == login_first)

    return unread[unread["target_person"].apply(visible_to_login)]


NOTIFICATION_HTML_TEMPLATE = r"""
<div id="ca-notification-controls" style="font-family:sans-serif;margin:0.15rem 0 0.5rem 0;">
  <button id="ca-enable-notifications" style="display:none;padding:7px 14px;border-radius:7px;
    border:1px solid #4B2A5C;background:#F0E6F5;color:#1A1420;cursor:pointer;font-size:0.85rem;">
    &#128276; Turn on notifications on this device
  </button>
  <button id="ca-test-notification" style="display:none;margin-left:6px;padding:7px 14px;border-radius:7px;
    border:1px solid #4B2A5C;background:white;color:#1A1420;cursor:pointer;font-size:0.85rem;">
    Send test notification
  </button>
  <button id="ca-repair-phone" style="margin-left:6px;padding:7px 14px;border-radius:7px;
    border:1px solid #4B2A5C;background:#4B2A5C;color:white;cursor:pointer;font-size:0.85rem;">
    Repair phone notifications
  </button>
  <span id="ca-push-state" style="margin-left:8px;font-size:0.8rem;color:#4B2A5C;"></span>
  <span id="ca-notifications-blocked" style="display:none;color:#8C1D1D;font-size:0.85rem;">
    &#128277; Notifications are blocked. Open the padlock/site-info icon and set Notifications to Allow.
  </span>
  <span id="ca-notifications-unsupported" style="display:none;color:#8C1D1D;font-size:0.85rem;">
    This browser does not support notifications.
  </span>
  <div id="ca-ios-install" style="display:none;margin-top:6px;padding:8px 10px;border-radius:6px;
    background:#EAF3FF;color:#12395E;font-size:0.82rem;">
    <b>iPhone / iPad:</b> Apple only allows notifications for installed apps. In <b>Safari</b>, tap
    <b>Share &#8593;</b> &rarr; <b>Add to Home Screen</b>, then open Cake Album from the new home-screen
    icon and tap &#8220;Turn on notifications&#8221; there. Requires iOS 16.4 or newer.
  </div>
  <div id="ca-notification-diagnostic" style="display:none;margin-top:6px;padding:8px 10px;border-radius:6px;
    background:#FFF3CD;color:#664D03;font-size:0.8rem;font-family:monospace;white-space:pre-wrap;"></div>
</div>
<script>
(() => {
  const notifications = __PAYLOAD__;
  const unreadCount = __UNREAD__;
  const vapidPublicKey = __VAPIDKEY__;
  const username = __USERNAME__;
  const departments = __DEPARTMENTS__;
  const SW_URL_CANDIDATES = __SWURLS__;
  const MANIFEST_URL_CANDIDATES = __MANIFESTURLS__;
  let MANIFEST_URL = MANIFEST_URL_CANDIDATES[0];
  let ICON_URL = __ICONURL__;

  const enableButton = document.getElementById("ca-enable-notifications");
  const testButton = document.getElementById("ca-test-notification");
  const repairPhoneButton = document.getElementById("ca-repair-phone");
  const blocked = document.getElementById("ca-notifications-blocked");
  const unsupported = document.getElementById("ca-notifications-unsupported");
  const diagnostic = document.getElementById("ca-notification-diagnostic");
  const iosInstall = document.getElementById("ca-ios-install");
  const stateLabel = document.getElementById("ca-push-state");
  const storageKey = "cake_album_announced_notification_ids_v2";
  const sentSubKey = "cake_album_pushsub_sent_v1";

  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
                (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const isStandalone = window.matchMedia("(display-mode: standalone)").matches ||
                       window.navigator.standalone === true;

  // ---- PWA head tags (needed for Add to Home Screen + iOS notifications) ----
  function ensureHeadTag(selector, build) {
    if (document.head.querySelector(selector)) return;
    document.head.appendChild(build());
  }
  (async () => {
    for (const url of MANIFEST_URL_CANDIDATES) {
      try {
        const res = await fetch(url, { cache: "no-store" });
        const type = (res.headers.get("content-type") || "").toLowerCase();
        if (res.ok && type.includes("json")) { MANIFEST_URL = url; break; }
      } catch (e) { /* try the next one */ }
    }
    ensureHeadTag('link[rel="manifest"]', () => {
      const l = document.createElement("link"); l.rel = "manifest"; l.href = MANIFEST_URL; return l;
    });
  })();
  ensureHeadTag('meta[name="apple-mobile-web-app-capable"]', () => {
    const m = document.createElement("meta"); m.name = "apple-mobile-web-app-capable"; m.content = "yes"; return m;
  });
  ensureHeadTag('meta[name="apple-mobile-web-app-title"]', () => {
    const m = document.createElement("meta"); m.name = "apple-mobile-web-app-title"; m.content = "Cake Album"; return m;
  });
  ensureHeadTag('link[rel="apple-touch-icon"]', () => {
    const l = document.createElement("link"); l.rel = "apple-touch-icon"; l.href = ICON_URL; return l;
  });

  function showDiagnostic(text) { diagnostic.style.display = "block"; diagnostic.textContent = text; }

  document.title = unreadCount > 0
    ? "\ud83d\udd14 (" + unreadCount + ") Cake Album Operations"
    : "Cake Album Operations";

  // The server may expose the worker at more than one path depending on how
  // Streamlit is configured. Fetch each one and only register the URL that
  // genuinely returns JavaScript — registering an HTML page is what produced
  // "The script has an unsupported MIME type ('text/html')".
  async function firstJavascriptUrl(candidates) {
    const notes = [];
    for (const url of candidates) {
      try {
        const res = await fetch(url, { cache: "no-store" });
        const type = (res.headers.get("content-type") || "").toLowerCase();
        if (res.ok && (type.includes("javascript") || type.includes("ecmascript"))) return { url: url, notes: notes };
        notes.push(url + " -> " + res.status + " " + (type || "no content-type"));
      } catch (e) { notes.push(url + " -> " + e.message); }
    }
    return { url: null, notes: notes };
  }

  function waitForActive(reg) {
    // Do NOT use navigator.serviceWorker.ready here: when the worker is served
    // from /static/ it never controls this page, so ready would hang forever.
    return new Promise((resolve) => {
      if (reg.active) return resolve(reg);
      const worker = reg.installing || reg.waiting;
      if (!worker) return resolve(reg);
      worker.addEventListener("statechange", () => {
        if (worker.state === "activated" || worker.state === "redundant") resolve(reg);
      });
      setTimeout(() => resolve(reg), 8000);
    });
  }

  let swRegistration = null;
  async function getRegistration() {
    if (swRegistration) return swRegistration;
    if (!("serviceWorker" in navigator)) throw new Error("No service worker support");
    const found = await firstJavascriptUrl(SW_URL_CANDIDATES);
    if (!found.url) {
      throw new Error("The service worker file is not being served as JavaScript. Tried: " + found.notes.join(" | ")
        + ". Fix: make sure .streamlit/config.toml has [server] enableStaticServing = true and restart Streamlit.");
    }
    const scope = found.url.replace(/[^/]+$/, "");
    ICON_URL = scope + "icon-192.png";
    swRegistration = await navigator.serviceWorker.register(found.url, { scope: scope });
    await waitForActive(swRegistration);
    return swRegistration;
  }

  async function showNotification(item, isTest) {
    const title = isTest ? "Cake Album \u2014 Test successful" : "Cake Album \u2014 New job assigned";
    const body = isTest ? "Notifications are working on this device." : item.message;
    const tag = isTest ? "cake-album-test" : ("cake-album-" + item.id);
    const options = { body: body, tag: tag, renotify: !isTest, icon: ICON_URL, badge: ICON_URL };
    const log = ["Permission: " + Notification.permission,
                 "Service worker support: " + ("serviceWorker" in navigator),
                 "Push support: " + ("PushManager" in window),
                 "Standalone (installed): " + isStandalone];
    try {
      const reg = await getRegistration();
      log.push("SW scope: " + reg.scope);
      await reg.showNotification(title, options);
      log.push("showNotification via service worker: SUCCESS");
      if (isTest) showDiagnostic(log.join("\n"));
      return;
    } catch (e) { log.push("Service worker path failed: " + e.name + ": " + e.message); }
    try {
      const n = new Notification(title, options);
      n.onclick = () => { window.focus(); n.close(); };
      log.push("Direct constructor: SUCCESS");
      if (isTest) showDiagnostic(log.join("\n"));
    } catch (e) {
      log.push("Direct constructor also failed: " + e.name + ": " + e.message);
      showDiagnostic(log.join("\n"));
    }
  }

  function readSeen() {
    try { return new Set(JSON.parse(localStorage.getItem(storageKey) || "[]")); }
    catch (_) { return new Set(); }
  }

  function announceNew() {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    const seen = readSeen();
    let changed = false;
    notifications.forEach(item => {
      if (!seen.has(item.id)) { showNotification(item, false); seen.add(item.id); changed = true; }
    });
    if (changed) localStorage.setItem(storageKey, JSON.stringify([...seen].slice(-500)));
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; ++i) outputArray[i] = rawData.charCodeAt(i);
    return outputArray;
  }

  function b64urlEncode(text) {
    const bytes = new TextEncoder().encode(text);
    let bin = ""; bytes.forEach(b => bin += String.fromCharCode(b));
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  // Streamlit has no REST endpoint, so the subscription travels back to Python
  // once through the URL, and Python removes it again immediately.
  function sendSubscriptionToServer(subscription) {
    const payload = JSON.stringify({
      username: username, department: departments, subscription: subscription.toJSON()
    });
    const fingerprint = subscription.endpoint + "|" + username + "|" + departments;
    if (localStorage.getItem(sentSubKey) === fingerprint) return;
    localStorage.setItem(sentSubKey, fingerprint);
    const url = new URL(window.location.href);
    url.searchParams.set("push_sub", b64urlEncode(payload));
    window.location.replace(url.toString());
  }

  async function subscribeForRealPush() {
    if (!vapidPublicKey) { stateLabel.textContent = "(server push key missing \u2014 see console)"; return; }
    if (!("PushManager" in window)) return;
    try {
      const registration = await getRegistration();
      let subscription = await registration.pushManager.getSubscription();
      if (!subscription) {
        subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(vapidPublicKey)
        });
      }
      sendSubscriptionToServer(subscription);
      stateLabel.textContent = "\u2705 Background push active on this device";
    } catch (e) {
      stateLabel.textContent = "";
      showDiagnostic("Push subscription failed: " + e.name + ": " + e.message);
    }
  }

  function refreshControls() {
    enableButton.style.display = "none";
    testButton.style.display = "none";
    blocked.style.display = "none";
    unsupported.style.display = "none";
    iosInstall.style.display = "none";
    if (isIOS && !isStandalone) { iosInstall.style.display = "block"; return; }
    if (!("Notification" in window)) { unsupported.style.display = "inline"; return; }
    if (!window.isSecureContext) {
      blocked.textContent = "\ud83d\udd15 Notifications require HTTPS (or localhost). Open the app over https://";
      blocked.style.display = "inline";
      return;
    }
    if (Notification.permission === "default") enableButton.style.display = "inline-block";
    if (Notification.permission === "granted") testButton.style.display = "inline-block";
    if (Notification.permission === "denied") blocked.style.display = "inline";
  }

  enableButton.addEventListener("click", async () => {
    const permission = await Notification.requestPermission();
    refreshControls();
    if (permission === "granted") {
      await showNotification({}, true);
      announceNew();
      subscribeForRealPush();
    }
  });
  testButton.addEventListener("click", () => showNotification({}, true));
  repairPhoneButton.addEventListener("click", () => {
    const setupBase = SW_URL_CANDIDATES[0].replace(/service-worker\.js$/, "phone-push-setup.html");
    const setup = new URL(setupBase, window.location.origin);
    setup.searchParams.set("k", vapidPublicKey || "");
    setup.searchParams.set("u", username || "");
    setup.searchParams.set("d", departments || "");
    setup.searchParams.set("r", window.top.location.href);
    window.top.location.href = setup.toString();
  });

  refreshControls();
  announceNew();
  // Warm the service worker up on every page load so the very first real push
  // does not arrive before the worker exists.
  if ("serviceWorker" in navigator && window.isSecureContext) {
    getRegistration().catch((e) => showDiagnostic("Service worker not ready: " + e.message));
  }
  if ("Notification" in window && Notification.permission === "granted" && window.isSecureContext) {
    subscribeForRealPush();
  }
})();
</script>
"""


def save_push_subscription_from_query():
    """The browser cannot POST to Streamlit, so the service worker's push subscription is
    handed back through a one-shot ?push_sub=... URL parameter, stored here, then removed
    from the address bar so it is never re-processed."""
    try:
        params = st.query_params
        raw = params.get("push_sub")
    except Exception:
        return
    if not raw:
        return
    if isinstance(raw, list):
        raw = raw[0]
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        sub = payload["subscription"]
        endpoint = sub["endpoint"]
        with connect() as conn:
            conn.execute(
                """INSERT INTO push_subscriptions(username, department, endpoint, subscription_json, created_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(endpoint) DO UPDATE SET username=excluded.username,
                       department=excluded.department, subscription_json=excluded.subscription_json""",
                (payload.get("username", ""), payload.get("department", ""),
                 endpoint, json.dumps(sub), now_iso()),
            )
            conn.commit()
        print(f"[PUSH] Stored subscription for {payload.get('username')} ({payload.get('department')})")
    except Exception as e:
        print(f"[PUSH] Could not store subscription: {e}")
    finally:
        try:
            del st.query_params["push_sub"]
        except Exception:
            pass


def _render_department_notifications_body():
    """Render in-app and operating-system notifications for the logged-in employee."""
    save_push_subscription_from_query()
    dept = st.session_state.get("department")
    if not dept:
        return
    if not static_serving_enabled():
        st.warning("⚠️ Phone/laptop pop-up notifications are switched off on the server: Streamlit's "
                   "static file serving is not active yet, so the browser cannot load the notification "
                   "worker. Restart the app service once (the settings file has already been written) "
                   "and this message will disappear.")

    all_my_depts = st.session_state.get("departments") or [dept]
    staff_name = st.session_state.get("staff_name", "").strip()
    notes = load_table("notifications")
    mine_all_depts = _notification_rows_for_user(notes, all_my_depts, staff_name)
    mine = mine_all_depts[mine_all_depts["target_department"] == dept] if not mine_all_depts.empty else mine_all_depts

    # The browser keeps the IDs it has already announced. This survives Streamlit
    # reruns and prevents the same unread job from popping up every polling cycle.
    payload = []
    if not mine_all_depts.empty:
        for _, n in mine_all_depts.sort_values("created_at").iterrows():
            payload.append({
                "id": str(n.get("id")),
                "message": str(n.get("message", "New work has been assigned.")),
                "order_id": str(n.get("order_id", "") or ""),
            })

    unread_count = len(mine_all_depts)
    payload_json = json.dumps(payload).replace("</", "<\\/")
    username_json = json.dumps(st.session_state.get("username", ""))
    departments_json = json.dumps(", ".join(all_my_depts))
    vapid_public_key_json = json.dumps(VAPID_PUBLIC_KEY) if VAPID_PUBLIC_KEY else "null"

    notification_html = (
        NOTIFICATION_HTML_TEMPLATE
        .replace("__PAYLOAD__", payload_json)
        .replace("__UNREAD__", str(unread_count))
        .replace("__VAPIDKEY__", json.dumps(VAPID_PUBLIC_KEY) if VAPID_PUBLIC_KEY else "null")
        .replace("__USERNAME__", username_json)
        .replace("__DEPARTMENTS__", departments_json)
        .replace("__SWURLS__", json.dumps(SERVICE_WORKER_URL_CANDIDATES))
        .replace("__MANIFESTURLS__", json.dumps(MANIFEST_URL_CANDIDATES))
        .replace("__ICONURL__", json.dumps(ICON_URL))
    )

    # st.components.v1.html is an iframe. Chrome and Firefox reject notification
    # permission requests from cross-origin iframes. st.html executes in the top-level
    # app document, which is the required context for the Notifications API.
    if hasattr(st, "html"):
        try:
            st.html(notification_html, unsafe_allow_javascript=True)
        except TypeError:
            st.info("Desktop notifications require a newer Streamlit version. Update Streamlit, then restart the app.")
    else:
        st.info("Desktop notifications require a newer Streamlit version. Update Streamlit, then restart the app.")

    if mine.empty:
        return

    st.markdown("### 🔔 Notifications")
    for _, n in mine.sort_values("created_at", ascending=False).iterrows():
        person = disp(n.get("target_person"))
        message = str(n.get("message", ""))
        is_complaint = message.startswith("Complaint ")
        is_cake_chat = ("mentioned you on cake" in message.lower() or "replied on cake" in message.lower()
                        or "mentioned you in order" in message.lower())
        icon = "🚨" if is_complaint else ("💬" if is_cake_chat else "🔔")
        st.warning(f"{icon} {message}" + (f" (For: {person})" if person != "—" else ""))
        oid = str(n.get("order_id", "") or "").strip()
        if oid and is_cake_chat:
            if st.button("💬 Open cake chat & reply", key=f"notif_open_thread_{int(n['id'])}", width='stretch'):
                # Mark only this message as read, remember exactly which cake Brenda/Desmond
                # needs, then force the next rerun straight into that cake's conversation.
                with connect() as conn:
                    conn.execute(
                        "UPDATE notifications SET notification_status='Read', read_at=?, acknowledged_by=? WHERE id=?",
                        (now_iso(), staff_name or dept, int(n["id"])),
                    )
                    conn.commit()
                st.session_state["_open_order_thread"] = oid
                st.session_state["_force_page"] = "Team Chat & AI"
                st.rerun()

    ack_by = st.text_input("Acknowledged by", value=staff_name or dept, key=f"notif_ack_{dept}")
    if st.button("Mark all as read", key=f"notif_read_{dept}"):
        with connect() as conn:
            for nid in mine["id"].tolist():
                conn.execute(
                    "UPDATE notifications SET notification_status='Read', read_at=?, acknowledged_by=? WHERE id=?",
                    (now_iso(), ack_by, nid),
                )
            conn.commit()
        st.rerun()


# Poll only this notification area instead of refreshing the whole ERP page and
# interrupting forms. On older Streamlit versions, it still works on normal reruns.
if hasattr(st, "fragment"):
    render_department_notifications = st.fragment(run_every="30s")(_render_department_notifications_body)
else:
    render_department_notifications = _render_department_notifications_body


def _split_names(value):
    return [n.strip() for n in str(value or "").split(",") if n.strip()]


def _render_team_chat_body():
    """An internal chat with real back-and-forth conversations, including group chats:
    pick one or more colleagues, see the full thread between everyone involved, and
    reply right there - the reply automatically goes to everyone else in that same
    conversation. Each message still triggers a real push notification to everyone
    it's addressed to. Lives in the sidebar so it's reachable from every page."""
    my_username = st.session_state.get("username", "").strip()
    my_name = st.session_state.get("staff_name", "").strip()
    if not my_username:
        return

    accounts = load_table("staff_accounts")
    accounts = accounts[accounts["is_active"] != "No"] if not accounts.empty and "is_active" in accounts.columns else accounts
    people = sorted([n for n in accounts["full_name"].tolist() if n and n.strip() != my_name]) if not accounts.empty else []

    msgs = load_table("team_chat_messages")
    my_name_lower = my_name.lower()

    def _am_i_recipient(row):
        return any(r.lower() == my_name_lower for r in _split_names(row["recipient"]))

    def _involves_me(row):
        return str(row["sender"]).strip().lower() == my_name_lower or _am_i_recipient(row)

    def _conversation_key(row):
        # Everyone in this message besides me - sorted so the same group of people
        # always lands on the same thread, regardless of who happens to send any
        # particular message within it.
        everyone = set([str(row["sender"]).strip()] + _split_names(row["recipient"]))
        everyone.discard(my_name)
        return tuple(sorted(everyone))

    involving_me = msgs[msgs.apply(_involves_me, axis=1)].copy() if not msgs.empty else msgs
    unread_to_me = msgs[msgs.apply(_am_i_recipient, axis=1) & (msgs["read_at"].isna() | (msgs["read_at"] == ""))] if not msgs.empty else msgs
    unread_count = len(unread_to_me)

    if not involving_me.empty:
        involving_me["_conv_key"] = involving_me.apply(_conversation_key, axis=1)

    unread_by_conv = {}
    if not unread_to_me.empty:
        for _, m in unread_to_me.iterrows():
            key = _conversation_key(m)
            unread_by_conv[key] = unread_by_conv.get(key, 0) + 1

    conv_last_activity = {}
    if not involving_me.empty:
        for _, m in involving_me.iterrows():
            key = m["_conv_key"]
            if key not in conv_last_activity or m["created_at"] > conv_last_activity[key]:
                conv_last_activity[key] = m["created_at"]
    conv_keys_sorted = sorted(conv_last_activity.keys(), key=lambda k: conv_last_activity[k], reverse=True)

    # Real option values are always the stable tuple of names (never includes the
    # unread count) - format_func handles the "(N new)" display text separately, so a
    # message being marked read mid-session never invalidates the current selection.
    new_convo_value = "__new__"
    dropdown_values = [new_convo_value] + conv_keys_sorted

    def _format_conv_option(value):
        if value == new_convo_value:
            return "+ New conversation"
        names = ", ".join(first_name(n) for n in value)
        n = unread_by_conv.get(value, 0)
        return f"{names} ({n} new)" if n else names

    with st.sidebar:
        st.markdown("---")
        header = f"💬 Team Chat ({unread_count} new)" if unread_count else "💬 Team Chat"
        with st.expander(header, expanded=(unread_count > 0)):
            if "chat_conv_pick" not in st.session_state and len(dropdown_values) > 1:
                st.session_state["chat_conv_pick"] = dropdown_values[1]
            chat_conv = st.selectbox("Conversation", dropdown_values, format_func=_format_conv_option, key="chat_conv_pick")

            if chat_conv == new_convo_value:
                chosen = st.multiselect("Start a conversation with (pick one or more)", people, key="chat_new_recipients")
                chat_conv = tuple(sorted(chosen)) if chosen else None
                if not people:
                    st.caption("No other active staff found to message.")

            thread = pd.DataFrame()
            if chat_conv:
                thread = involving_me[involving_me["_conv_key"] == chat_conv].sort_values("created_at") if not involving_me.empty else involving_me
                names_label = ", ".join(first_name(n) for n in chat_conv)
                st.markdown(f"**Conversation with {names_label}**")
                if thread.empty:
                    st.caption("No messages yet — say hello below.")
                else:
                    for _, m in thread.tail(20).iterrows():
                        is_me = str(m["sender"]).strip().lower() == my_name_lower
                        who = "You" if is_me else first_name(m["sender"])
                        st.markdown(f"**{who}** · {m['created_at']}")
                        if str(m.get("message", "") or "").strip():
                            st.caption(m["message"])
                        # Team Chat photo attachments are intentionally rendered small in the
                        # sidebar. The bytes have already gone through the same iPhone/Android
                        # image normalization used by Customer Care reference photos.
                        attachment_b64 = str(m.get("attachment_base64", "") or "")
                        if attachment_b64:
                            try:
                                photo_bytes = base64.b64decode(attachment_b64)
                                st.image(_web_safe_image_payload(photo_bytes, max_dimension=900, jpeg_quality=75),
                                         caption=str(m.get("attachment_filename", "") or "Photo"), width='stretch')
                            except Exception:
                                st.caption("📷 Photo attachment could not be displayed.")
                    unread_ids = thread[
                        thread.apply(_am_i_recipient, axis=1) &
                        (thread["read_at"].isna() | (thread["read_at"] == ""))
                    ]["id"].tolist()
                    if unread_ids:
                        with connect() as conn:
                            conn.executemany("UPDATE team_chat_messages SET read_at=? WHERE id=?",
                                              [(now_iso(), int(i)) for i in unread_ids])
                            conn.commit()

                msg_gen = st.session_state.get("chat_message_gen", 0)
                message_text = st.text_area("Reply" if not thread.empty else "Message",
                                             key=f"chat_message_text_{msg_gen}", height=80,
                                             placeholder="Type your message…")
                chat_photo = st.file_uploader(
                    "📷 Attach a picture (optional)",
                    type=["jpg", "jpeg", "png", "heic", "heif"],
                    key=f"chat_photo_{msg_gen}",
                    help="Works with iPhone and Android photos. Large phone pictures are automatically resized/compressed before sending.",
                )
                if chat_photo is not None:
                    try:
                        preview_bytes, _, _ = _prepare_uploaded_reference_image(chat_photo, max_dimension=900, jpeg_quality=75)
                        st.image(preview_bytes, caption="Photo ready to send", width='stretch')
                    except Exception:
                        st.warning("This photo could not be previewed, but you can try sending it.")

                if st.button("Send", key="chat_send_btn", width='stretch'):
                    if message_text.strip() or chat_photo is not None:
                        last_id = int(thread.iloc[-1]["id"]) if not thread.empty else None
                        recipient_str = ", ".join(chat_conv)
                        attachment_filename = attachment_mime = attachment_base64 = ""
                        if chat_photo is not None:
                            try:
                                photo_bytes, photo_suffix, photo_mime = _prepare_uploaded_reference_image(
                                    chat_photo, max_dimension=1200, jpeg_quality=78
                                )
                                attachment_filename = f"{Path(chat_photo.name).stem}{photo_suffix}"
                                attachment_mime = photo_mime
                                attachment_base64 = base64.b64encode(photo_bytes).decode()
                            except Exception as exc:
                                st.error(f"Could not attach this picture: {exc}")
                                return

                        saved_message = message_text.strip() or "📷 Photo"
                        with connect() as conn:
                            conn.execute("""INSERT INTO team_chat_messages(
                                            sender, sender_department, recipient, message, created_at, reply_to_id,
                                            attachment_filename, attachment_mime, attachment_base64)
                                            VALUES(?,?,?,?,?,?,?,?,?)""",
                                         (my_name, st.session_state.get("department"), recipient_str, saved_message, now_iso(), last_id,
                                          attachment_filename, attachment_mime, attachment_base64))
                            conn.commit()
                        push_text = message_text.strip() or "📷 Sent you a picture"
                        if chat_photo is not None and message_text.strip():
                            push_text = "📷 " + push_text
                        send_push_to_people(list(chat_conv), f"💬 New message from {my_name}", push_text)
                        # Generation-based keys clear both the text and file uploader only AFTER
                        # the database write succeeds; iPhone users do not lose a selected photo
                        # because of an unrelated rerun.
                        st.session_state["chat_message_gen"] = msg_gen + 1
                        st.rerun()
                    else:
                        st.error("Type a message or attach a picture first.")


if hasattr(st, "fragment"):
    render_team_chat = st.fragment(run_every="30s")(_render_team_chat_body)
else:
    render_team_chat = _render_team_chat_body


# -----------------------------
# Database helpers
# -----------------------------

@st.cache_resource
def _configure_sqlite_once():
    """Configure SQLite once for a multi-user Streamlit server.

    WAL lets readers continue while another user is writing, and NORMAL synchronous mode
    removes avoidable disk waits while keeping SQLite's durability guarantees appropriate
    for this ERP. This is especially important when several departments are logged in at once.
    """
    try:
        with sqlite3.connect(DATABASE_FILE, timeout=15) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=15000")
    except Exception as e:
        print(f"[DB] SQLite performance setup skipped: {e}", flush=True)
    return True


_configure_sqlite_once()


def connect():
    conn = sqlite3.connect(DATABASE_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# -----------------------------
# Authentication
# -----------------------------

LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_MINUTES = 15
SESSION_TIMEOUT_HOURS = 24


def hash_password(password: str, salt: str = None):
    salt = salt or secrets_mod.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return digest, salt


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    digest, _ = hash_password(password, salt)
    return secrets_mod.compare_digest(digest, expected_hash)


def ensure_bootstrap_admin():
    """If there are no staff accounts yet (brand new install), create one bootstrap
    Owner/Admin account so someone can log in and start adding real staff accounts.
    Uses INSERT OR IGNORE so this is safe even if two sessions run this at the same
    moment on first startup (a real race condition seen on Streamlit Cloud)."""
    with connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM staff_accounts").fetchone()[0]
        if count == 0:
            digest, salt = hash_password("admin123")
            conn.execute(
                "INSERT OR IGNORE INTO staff_accounts(username, full_name, password_hash, salt, department, departments, is_hod, is_active, created_at, created_by) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("admin", "Administrator", digest, salt, "Owner / Admin", "Owner / Admin", "Yes", "Yes", now_iso(), "System Bootstrap"))
            conn.commit()


DEFAULT_STAFF_PASSWORD = "CakeAlbum2026"

# (username, full_name, department_list, is_hod)
DEFAULT_STAFF_ROSTER = [
    # Decorators — Piling, Covering, Decoration (they stop at Decorating; Studio/QC is separate)
    ("zakia", "Zakia Nanfuka", ["Filling / Piling", "Coating / Covering", "Decoration"], False),
    ("eriya", "Eriya Olwe", ["Filling / Piling", "Coating / Covering", "Decoration"], True),   # HOD
    ("lawrence", "Lawrence Nsubuga", ["Filling / Piling", "Coating / Covering", "Decoration"], False),
    ("bobi", "Bobi", ["Filling / Piling", "Coating / Covering", "Decoration"], False),
    ("angel", "Angel Nakilembe", ["Filling / Piling", "Coating / Covering", "Decoration"], False),
    ("desmond", "Desmond Okurut", ["Filling / Piling", "Coating / Covering", "Decoration"], False),
    ("zaitun", "Zaitun", ["Filling / Piling", "Coating / Covering", "Decoration"], False),
    ("aisha", "Aisha", ["Filling / Piling", "Coating / Covering", "Decoration"], False),
    # Baking
    ("billy", "Billy", ["Baking"], True),        # Assistant HOD — given HOD visibility as backup to Uncle Joe
    ("unclejoe", "Uncle Joe", ["Baking"], True),  # HOD - keep as "Uncle Joe", do not split into first/last
    ("ronnie", "Ronnie", ["Baking"], False),
    ("martin", "Martin", ["Baking"], False),
    ("andre", "Andre", ["Baking"], False),
    # Customer Care
    ("suzankiberu", "Suzan Kiberu", ["Customer Care"], True),  # HOD / Ass. CEO
    ("brenda", "Brenda Nakilyowa", ["Customer Care"], False),
    ("doreen", "Doreen", ["Customer Care"], False),
    # Design & Innovation
    ("keith", "Keith Abaho", ["Design & Innovation"], True),  # HOD
    # Delivery
    ("silas", "Silas Turyasingula", ["Dispatch / Driver"], False),
    ("hashim", "Hashim (surname pending)", ["Dispatch / Driver"], False),  # placed under Delivery — please confirm department + surname
    # Studio / Final QC — was previously unstaffed; now covered
    ("cornelius", "Cornelius Kayobyo", ["Studio / Final QC"], True),  # also does Social Media, which has no in-app feature yet
    # General Manager — oversight across the business, given Owner/Admin-level access
    ("suzanmumbejja", "Suzan Nasuuna", ["Owner / Admin"], True),  # name updated from "Suzan Mumbejja" — please confirm which surname is correct
    # Procurement
    ("teddy", "Teddy", ["Procurement"], False),
    # Finance — was missing from the original roster; placeholder until you tell me the real name(s)
    ("finance", "Finance Team (name pending)", ["Finance"], False),
    # Production Planning — also missing from the original roster; placeholder until you tell me the real name(s)
    ("production", "Production Planning (name pending)", ["Production Planning"], False),
    ("faith", "Faith N.", ["Packaging"], False),
]

# Corrects full names for accounts that may have already been created under the old roster,
# so re-running this on an existing database still applies the confirmed surnames.
STAFF_NAME_CORRECTIONS = {
    "eriya": "Eriya Olwe",
    "lawrence": "Lawrence Nsubuga",
    "angel": "Angel Nakilembe",
    "desmond": "Desmond Okurut",
    "brenda": "Brenda Nakilyowa",
    "keith": "Keith Abaho",
    "silas": "Silas Turyasingula",
    "suzanmumbejja": "Suzan Nasuuna",
    "unclejoe": "Uncle Joe",  # explicitly requested to stay as "Uncle Joe", not split into first/last
}


def apply_staff_name_corrections():
    with connect() as conn:
        for username, corrected_name in STAFF_NAME_CORRECTIONS.items():
            conn.execute("UPDATE staff_accounts SET full_name=? WHERE username=?", (corrected_name, username))
        conn.commit()


def ensure_default_staff_roster():
    """One-time setup: creates the real staff accounts from the agreed roster, skipping
    any username that already exists (so this is safe to run on every startup).
    Uses INSERT OR IGNORE as a second layer of protection against the same kind of
    race condition fixed in ensure_bootstrap_admin()."""
    with connect() as conn:
        existing = {r[0] for r in conn.execute("SELECT username FROM staff_accounts").fetchall()}
        digest, salt = hash_password(DEFAULT_STAFF_PASSWORD)
        for username, full_name, departments, is_hod in DEFAULT_STAFF_ROSTER:
            if username in existing:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO staff_accounts(username, full_name, password_hash, salt, department, departments, is_hod, is_active, created_at, created_by) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (username, full_name, digest, salt, departments[0], ",".join(departments),
                 "Yes" if is_hod else "No", "Yes", now_iso(), "System Roster Setup"))
        conn.commit()


def authenticate(username: str, password: str):
    """Returns (success, message, account_row_or_None)."""
    username = username.strip().lower()
    if not username or not password:
        return False, "Enter both username and password.", None
    with connect() as conn:
        row = conn.execute("SELECT * FROM staff_accounts WHERE username=?", (username,)).fetchone()
        if row is None:
            return False, "Incorrect username or password.", None
        if row["is_active"] != "Yes":
            return False, "This account has been deactivated. Contact your Owner/Admin.", None
        if row["locked_until"]:
            try:
                locked_until = datetime.fromisoformat(row["locked_until"])
                if datetime.now() < locked_until:
                    mins_left = int((locked_until - datetime.now()).total_seconds() / 60) + 1
                    return False, f"Account locked after too many failed attempts. Try again in {mins_left} minute(s).", None
            except Exception:
                pass
        if not verify_password(password, row["salt"], row["password_hash"]):
            attempts = (row["failed_attempts"] or 0) + 1
            if attempts >= LOGIN_MAX_ATTEMPTS:
                locked_until = (datetime.now() + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)).isoformat()
                conn.execute("UPDATE staff_accounts SET failed_attempts=?, locked_until=? WHERE username=?", (attempts, locked_until, username))
                conn.commit()
                return False, f"Incorrect password. Account locked for {LOGIN_LOCKOUT_MINUTES} minutes after {LOGIN_MAX_ATTEMPTS} failed attempts.", None
            conn.execute("UPDATE staff_accounts SET failed_attempts=? WHERE username=?", (attempts, username))
            conn.commit()
            remaining = LOGIN_MAX_ATTEMPTS - attempts
            return False, f"Incorrect username or password. {remaining} attempt(s) left before lockout.", None
        conn.execute("UPDATE staff_accounts SET failed_attempts=0, locked_until=NULL, last_login_at=? WHERE username=?", (now_iso(), username))
        conn.commit()
        return True, "OK", row



def safe_add_column(conn, table, col_name, col_def):
    """Adds a column if it doesn't already exist. Safe to call even if another
    concurrent session is doing the exact same thing at the same moment — a real
    race condition seen on Streamlit Cloud's cold start, where the app can briefly
    run its startup code more than once at nearly the same time."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e).lower():
            raise


def ensure_base_schema():
    """Create the base 'orders' table and all core support tables if this is a fresh
    database. Previously the app assumed these already existed, which meant a fresh
    deployment could never start."""
    with connect() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS orders(
            order_id TEXT PRIMARY KEY,
            customer_name TEXT, customer_number TEXT,
            flavours TEXT, design_description TEXT,
            due_date TEXT, expected_time TEXT,
            price_ugx REAL, deposit REAL, balance REAL,
            payment_method TEXT, payment_status TEXT,
            location TEXT, order_channel TEXT,
            workflow_status TEXT, current_owner TEXT, next_action TEXT,
            priority TEXT DEFAULT 'Normal',
            balance_to_collect REAL, balance_collection_status TEXT,
            finance_confirmation_status TEXT, payment_confirmed_at TEXT,
            delivery_status TEXT, follow_up_status TEXT, follow_up_completed_at TEXT,
            issue_flag TEXT DEFAULT 'No', issue_notes TEXT,
            cake_size_value REAL, cake_size_unit TEXT, cake_shape TEXT,
            number_of_layers INTEGER, reference_image_path TEXT,
            baker_assigned TEXT, piler_assigned TEXT, coverer_assigned TEXT,
            decorator_assigned TEXT, driver_assigned TEXT,
            baking_started_at TEXT, baking_completed_at TEXT, baking_status TEXT,
            decorating_started_at TEXT, decorating_completed_at TEXT, decoration_status TEXT,
            packaging_status TEXT, packaging_completed_at TEXT,
            qc_status TEXT, qc_completed_at TEXT,
            production_planned_at TEXT, delivered_at TEXT,
            order_created_at TEXT, last_updated_at TEXT, last_updated_by TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS audit_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, action_type TEXT,
            stage TEXT, action_details TEXT, performed_by TEXT, performed_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS stage_quality_checks(
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, from_stage TEXT, to_stage TEXT,
            check_type TEXT, checked_by TEXT, check_status TEXT, issue_category TEXT,
            issue_description TEXT, responsible_department TEXT, responsible_person TEXT,
            checked_at TEXT, returned_at TEXT, resolution_status TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS complaints(
            id INTEGER PRIMARY KEY AUTOINCREMENT, complaint_id TEXT, order_id TEXT,
            customer_name TEXT, complaint_category TEXT, complaint_details TEXT, severity TEXT,
            responsible_department TEXT, responsible_person TEXT, loss_value_ugx REAL DEFAULT 0,
            repayment_status TEXT DEFAULT 'Pending Review', repayment_notes TEXT,
            repayment_recorded_by TEXT, repayment_recorded_at TEXT,
            opened_at TEXT, complaint_status TEXT,
            resolution_action TEXT, resolved_at TEXT, customer_confirmation TEXT)""")
        existing_comp = {r[1] for r in conn.execute("PRAGMA table_info(complaints)").fetchall()}
        for col_name, col_def in {
            "responsible_person": "TEXT", "loss_value_ugx": "REAL DEFAULT 0",
            "repayment_status": "TEXT DEFAULT 'Pending Review'", "repayment_notes": "TEXT",
            "repayment_recorded_by": "TEXT", "repayment_recorded_at": "TEXT",
        }.items():
            if col_name not in existing_comp:
                safe_add_column(conn, "complaints", col_name, col_def)
        conn.execute("""CREATE TABLE IF NOT EXISTS delivery_runs(
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, driver_name TEXT, run_status TEXT,
            run_started_at TEXT, run_completed_at TEXT, created_at TEXT, created_by TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS delivery_run_orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, order_id TEXT, stop_sequence INTEGER,
            delivery_status TEXT, arrival_time TEXT, finance_confirmation_requested_at TEXT,
            delivery_completed_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS order_material_requirements(
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, requested_by TEXT, item_name TEXT,
            quantity_required REAL, unit TEXT, requirement_status TEXT, requested_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS material_issues(
            id INTEGER PRIMARY KEY AUTOINCREMENT, requirement_id INTEGER, quantity_issued REAL,
            issued_by TEXT, issued_to TEXT, issue_status TEXT, issued_at TEXT, notes TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS procurement_requisitions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, requirement_id INTEGER, order_id TEXT, item_name TEXT,
            quantity_required REAL, requisition_status TEXT, requested_at TEXT, updated_by TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS cash_clearances(
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, driver_name TEXT, order_ids TEXT,
            expected_cash REAL, actual_cash REAL, variance REAL, cleared_by TEXT, cleared_at TEXT, notes TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS creativity_contributions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, contributor_name TEXT, department TEXT, idea_title TEXT,
            idea_description TEXT, category TEXT, status TEXT DEFAULT 'Submitted', submitted_at TEXT,
            reviewed_by TEXT, review_notes TEXT, reviewed_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS reassignment_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, stage TEXT NOT NULL, role_label TEXT,
            staff_column TEXT, current_value TEXT, proposed_value TEXT, requested_by TEXT, reason TEXT,
            status TEXT DEFAULT 'Pending', requested_at TEXT, decided_by TEXT, decided_at TEXT, decision_notes TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS baking_batches(
            id INTEGER PRIMARY KEY AUTOINCREMENT, batch_number TEXT UNIQUE, batch_date TEXT, flavour TEXT,
            cake_size_value REAL, cake_shape TEXT, total_layers_requested INTEGER, actual_layers_baked INTEGER,
            status TEXT DEFAULT 'Pending', assigned_baker TEXT, mixer_assigned TEXT, oven_person_assigned TEXT,
            created_by TEXT, created_at TEXT, baking_started_at TEXT, completed_at TEXT)""")
        safe_add_column(conn, "baking_batches", "product_type", "TEXT DEFAULT 'Cake'")
        conn.execute("""CREATE TABLE IF NOT EXISTS baking_batch_orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER NOT NULL, order_id TEXT NOT NULL, layers_needed INTEGER,
            baked_status TEXT DEFAULT 'Pending', actual_layers_baked INTEGER, baked_at TEXT, baked_by TEXT)""")
        existing_bbo = {r[1] for r in conn.execute("PRAGMA table_info(baking_batch_orders)").fetchall()}
        for col_name, col_def in [("baked_status", "TEXT DEFAULT 'Pending'"), ("actual_layers_baked", "INTEGER"),
                                    ("baked_at", "TEXT"), ("baked_by", "TEXT")]:
            if col_name not in existing_bbo:
                safe_add_column(conn, "baking_batch_orders", col_name, col_def)
        existing_bb = {r[1] for r in conn.execute("PRAGMA table_info(baking_batches)").fetchall()}
        for col_name, col_def in [("oven_start_temp_c", "INTEGER"), ("oven_stop_temp_c", "INTEGER"),
                                    ("oven_started_by", "TEXT"), ("notes", "TEXT")]:
            if col_name not in existing_bb:
                safe_add_column(conn, "baking_batches", col_name, col_def)
        conn.execute("""CREATE TABLE IF NOT EXISTS push_subscriptions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, department TEXT,
            endpoint TEXT NOT NULL UNIQUE, subscription_json TEXT NOT NULL, created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS piler_daily_accountability(
            id INTEGER PRIMARY KEY AUTOINCREMENT, piler_name TEXT, accountability_date TEXT,
            item_name TEXT, quantity_used REAL, unit TEXT, recorded_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS team_chat_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT, sender TEXT NOT NULL, sender_department TEXT,
            recipient TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT)""")
        safe_add_column(conn, "team_chat_messages", "reply_to_id", "INTEGER")
        # Optional photo attachment for Team Chat. Photos use the same iPhone/Android
        # normalization path as Customer Care reference images.
        safe_add_column(conn, "team_chat_messages", "attachment_filename", "TEXT")
        safe_add_column(conn, "team_chat_messages", "attachment_mime", "TEXT")
        safe_add_column(conn, "team_chat_messages", "attachment_base64", "TEXT")
        conn.execute("""CREATE TABLE IF NOT EXISTS order_videos(
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, filename TEXT, mime_type TEXT,
            data_base64 TEXT NOT NULL, file_size_bytes INTEGER, uploaded_at TEXT)""")
        # New phone-friendly storage: keep large videos on disk instead of embedding them in SQLite.
        # Existing base64 videos continue to work unchanged.
        safe_add_column(conn, "order_videos", "file_path", "TEXT")
        conn.execute("""CREATE TABLE IF NOT EXISTS oven_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, flavour TEXT, product_type TEXT,
            start_temp_c REAL, stop_temp_c REAL, oven_start_at TEXT, oven_stop_at TEXT,
            recorded_by_start TEXT, recorded_by_stop TEXT, notes TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS baked_cookie_inventory(
            id INTEGER PRIMARY KEY AUTOINCREMENT, date_baked TEXT, flavour TEXT, size_category TEXT,
            quantity_available INTEGER, baker TEXT, storage_location TEXT, inventory_status TEXT DEFAULT 'Available',
            reserved_order_id TEXT, created_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS staff_accounts(
            username TEXT PRIMARY KEY, full_name TEXT NOT NULL, password_hash TEXT NOT NULL, salt TEXT NOT NULL,
            department TEXT NOT NULL, departments TEXT, is_hod TEXT DEFAULT 'No', is_active TEXT DEFAULT 'Yes',
            failed_attempts INTEGER DEFAULT 0, locked_until TEXT,
            created_at TEXT NOT NULL, created_by TEXT, last_login_at TEXT)""")
        existing_acct = {r[1] for r in conn.execute("PRAGMA table_info(staff_accounts)").fetchall()}
        if "departments" not in existing_acct:
            safe_add_column(conn, "staff_accounts", "departments", "TEXT")
        conn.execute("UPDATE staff_accounts SET departments = department WHERE departments IS NULL OR departments = ''")
        conn.commit()


def ensure_release_2_schema():
    """Add Release 2 fields/tables safely to the database."""
    ensure_base_schema()
    with connect() as conn:
        existing = {r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()}
        additions = {
            "order_type": "TEXT DEFAULT 'Normal Order'",
            "payment_arrangement": "TEXT DEFAULT 'Deposit'",
            "payment_status": "TEXT DEFAULT 'Pending'",
            "system_suggested_layers": "INTEGER",
            "final_approved_layers": "INTEGER",
            "flavour_availability_status": "TEXT",
            "inventory_check_required": "TEXT DEFAULT 'No'",
            "inventory_reservation_id": "INTEGER",
            "baking_plan_date": "TEXT",
            "baking_extra_buffer": "INTEGER DEFAULT 0",
            "order_quantity": "INTEGER DEFAULT 1",
            "unit_price_ugx": "REAL",
            "is_bulk_order": "TEXT DEFAULT 'No'",
            "delivery_window_start": "TEXT",
            "delivery_window_end": "TEXT",
            "cake_format": "TEXT DEFAULT 'Full Cake'",
            "icing_type": "TEXT DEFAULT 'Buttercream'",
            "urgency_level": "TEXT DEFAULT 'Normal'",
            "cash_cleared_status": "TEXT DEFAULT 'Not Applicable'",
            "cash_cleared_at": "TEXT",
            "cash_cleared_by": "TEXT",
            "satisfaction_rating": "INTEGER",
            "product_type": "TEXT DEFAULT 'Cake'",
            "size_category": "TEXT",
            "dozens_quantity": "REAL",
            "sold_from_inventory": "TEXT DEFAULT 'No'",
            "reference_image_base64": "TEXT",
            "cake_category": "TEXT",
            "cake_height_inches": "REAL",
            "mixer_assigned": "TEXT",
            "oven_person_assigned": "TEXT",
            "baking_batch_number": "TEXT",
            "flavour_preference_note": "TEXT",
            "centerpiece_team_assigned": "TEXT",
            "side_cake_team_assigned": "TEXT",
            "delivery_date": "TEXT",
            "is_multi_tier": "TEXT DEFAULT 'No'",
            "tier_count": "INTEGER DEFAULT 1",
            "tier_details_json": "TEXT",
            "side_cake_count": "INTEGER DEFAULT 0",
            "side_cake_details_json": "TEXT",
            "reference_images_json": "TEXT",
            "reference_videos_json": "TEXT",
            "inventory_batch_id": "INTEGER",
        }
        for name, definition in additions.items():
            if name not in existing:
                safe_add_column(conn, "orders", name, definition)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS baked_cake_inventory(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_baked TEXT NOT NULL,
            flavour TEXT NOT NULL,
            cake_size_value REAL,
            cake_size_unit TEXT DEFAULT 'Inches',
            cake_shape TEXT,
            number_of_layers INTEGER,
            quantity_available INTEGER NOT NULL DEFAULT 1,
            layers_available INTEGER,
            baker TEXT,
            storage_location TEXT,
            inventory_status TEXT DEFAULT 'Available',
            reserved_order_id TEXT,
            reserved_at TEXT,
            created_at TEXT NOT NULL
        )""")
        existing_inv = {r[1] for r in conn.execute("PRAGMA table_info(baked_cake_inventory)").fetchall()}
        if "layers_available" not in existing_inv:
            safe_add_column(conn, "baked_cake_inventory", "layers_available", "INTEGER")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS extra_baking_assignments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_date TEXT NOT NULL,
            flavour TEXT NOT NULL,
            cake_size_value REAL,
            cake_shape TEXT,
            layers_per_cake INTEGER NOT NULL,
            cake_units INTEGER NOT NULL,
            total_layers INTEGER NOT NULL,
            assigned_baker TEXT NOT NULL,
            reason TEXT,
            assignment_status TEXT DEFAULT 'Assigned',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            inventory_record_id INTEGER
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS layer_inventory_usage(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inventory_id INTEGER NOT NULL,
            order_id TEXT,
            stage TEXT NOT NULL,
            layers_used INTEGER NOT NULL,
            used_by TEXT NOT NULL,
            used_at TEXT NOT NULL,
            notes TEXT
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS layer_inventory_reconciliation(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reconciliation_date TEXT NOT NULL,
            confirmed_by TEXT NOT NULL,
            opening_layers INTEGER NOT NULL,
            layers_used INTEGER NOT NULL,
            closing_layers INTEGER NOT NULL,
            procurement_balance INTEGER,
            comments TEXT,
            confirmed_at TEXT NOT NULL
        )""")
        topper_cols = {
            "topper_required": "TEXT DEFAULT 'No'", "topper_count": "INTEGER DEFAULT 0",
            "topper_wording": "TEXT", "topper_notes": "TEXT",
            "topper_1_wording": "TEXT", "topper_1_notes": "TEXT",
            "topper_2_wording": "TEXT", "topper_2_notes": "TEXT",
            "topper_3_wording": "TEXT", "topper_3_notes": "TEXT",
            "topper_status": "TEXT DEFAULT 'Not Required'", "topper_assigned_to": "TEXT",
            "topper_target_at": "TEXT", "topper_ready_at": "TEXT",
            "topper_received_by_decorator": "TEXT", "topper_received_at": "TEXT", "topper_pickup_note": "TEXT",
            "sticker_required": "TEXT DEFAULT 'No'", "sticker_count": "INTEGER DEFAULT 0",
            "sticker_notes": "TEXT", "sticker_1_notes": "TEXT", "sticker_2_notes": "TEXT",
            "sticker_status": "TEXT DEFAULT 'Not Required'", "sticker_assigned_to": "TEXT",
            "sticker_ready_at": "TEXT",
            "sticker_received_by_decorator": "TEXT", "sticker_received_at": "TEXT", "sticker_pickup_note": "TEXT",
            "skip_baking": "TEXT DEFAULT 'No'",
            "piling_started_at": "TEXT", "piling_completed_at": "TEXT",
            "covering_started_at": "TEXT", "covering_completed_at": "TEXT",
        }
        order_cols_24 = {r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()}
        for name, definition in topper_cols.items():
            if name not in order_cols_24:
                safe_add_column(conn, "orders", name, definition)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS notifications(
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, target_department TEXT NOT NULL,
            target_person TEXT, message TEXT NOT NULL, notification_status TEXT DEFAULT 'Unread',
            created_at TEXT NOT NULL, read_at TEXT, acknowledged_by TEXT
        )""")
        existing_notif = {r[1] for r in conn.execute("PRAGMA table_info(notifications)").fetchall()}
        if "acknowledged_by" not in existing_notif:
            safe_add_column(conn, "notifications", "acknowledged_by", "TEXT")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_flavour_availability(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_date TEXT NOT NULL,
            flavour TEXT NOT NULL,
            availability_status TEXT DEFAULT 'Available',
            notes TEXT,
            updated_by TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(plan_date, flavour)
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS stage_material_usage(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            item_name TEXT NOT NULL,
            colour TEXT,
            size TEXT,
            quantity REAL NOT NULL,
            unit TEXT NOT NULL,
            material_action TEXT NOT NULL,
            recorded_by TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        )""")
        existing_smu = {r[1] for r in conn.execute("PRAGMA table_info(stage_material_usage)").fetchall()}
        if "colour" not in existing_smu:
            safe_add_column(conn, "stage_material_usage", "colour", "TEXT")
        if "size" not in existing_smu:
            safe_add_column(conn, "stage_material_usage", "size", "TEXT")
        if "base_quantity" not in existing_smu:
            safe_add_column(conn, "stage_material_usage", "base_quantity", "REAL")
        if "multiplier" not in existing_smu:
            safe_add_column(conn, "stage_material_usage", "multiplier", "REAL DEFAULT 1")
        if "edited_by" not in existing_smu:
            safe_add_column(conn, "stage_material_usage", "edited_by", "TEXT")
        if "edited_at" not in existing_smu:
            safe_add_column(conn, "stage_material_usage", "edited_at", "TEXT")
        conn.commit()


def suggested_layers_for_price(price):
    try:
        price = float(price)
    except Exception:
        return 1
    if price >= 200000:
        return 4
    if price >= 150000:
        return 4
    if price >= 100000:
        return 3
    return 1


def due_alert(row):
    """Return alert label and severity from due date + expected time."""
    try:
        due = datetime.fromisoformat(f"{row.get('due_date')}T{str(row.get('expected_time'))[:8]}")
        mins = (due - datetime.now()).total_seconds() / 60
        if mins < 0:
            return "🚨 OVERDUE", "error"
        if mins <= 30:
            return "🚨 DUE NOW", "error"
        if mins <= 60:
            return "⚠️ DUE WITHIN 1 HOUR", "warning"
        if due.date() == date.today():
            return "⏰ DUE TODAY", "warning"
    except Exception:
        pass
    return "", ""


def show_due_alert(row):
    label, severity = due_alert(row)
    if label:
        msg = f"{label} — {disp(row.get('customer_name'))} · {disp(row.get('order_id'))} · Current stage: {disp(row.get('workflow_status'))}"
        if severity == "error":
            st.error(msg)
        else:
            st.warning(msg)


MATERIAL_VARIANTS = {
    "Maimun Colors 240ml": ["Redvelvet", "Blueberry", "Fondant Black", "Coconut", "Lemon Yellow", "Orange Oil",
        "Hot Pink", "Orange Emulsion", "Royal Blue", "Bubblegum", "Chocolate", "Strawberry", "Garden Green"],
    "Maimun Colours 50ml": ["Redvelvet", "Blueberry", "Fondant Black", "Hot Pink", "Coconut", "Lemon Yellow",
        "Orange Oil", "Royal Blue", "Bubblegum", "Chocolate", "Strawberry", "Mint Flavor", "Butter Scotch",
        "Raspberry", "Garden Green"],
    "Pradip": ["Coconut", "Chocolate", "Banana", "Bubblegum", "Strawberry", "Pepper Mint", "Cappuccino"],
    "Flowers": ["Red Roses", "White Roses", "Blue Roses", "Cream Roses", "Pink Roses", "Peach Roses",
        "Yellow Roses", "Hot Pink Roses", "Black Roses", "Grey Roses", "Hydrangeas", "Red Rose Buds",
        "White Rose Buds", "Pink Rose Buds", "Purple Roses", "Peonies", "Small Roses", "Set Fillers",
        "Lillies", "Orchids"],
    "Balls": ["Gold", "White", "Blue", "Black", "Silver", "Orange", "Pink", "Navy Blue", "Turquoise", "Red",
        "Red Shining", "Green", "Green Shining", "Purple", "Pink Shining", "Blue Shining", "Gold Shining",
        "Yellow", "Nude", "Cream", "Transparent Pink", "Transparent White", "Transparent Colourless"],
    "Palm Leaf": ["Gold", "Silver", "Purple", "Red", "Blue", "Yellow", "Cream", "Black", "Pink"],
    "Pearls": ["White", "Gold", "Black", "Pink", "Red", "Blue", "Green", "Purple"],
    "Butterflies": ["Gold", "Silver"],
    "Crowns": ["Gold", "Silver"],
    "Topper Paper": ["Gold Plain", "Gold Glitter", "Gold Shine", "Silver Plain", "Silver Glitter", "Silver Shine",
        "White Plain", "White Glitter", "White Shine",
        "Blue Plain", "Blue Glitter", "Blue Shine", "Pink Plain", "Pink Glitter", "Pink Shine",
        "Yellow Plain", "Yellow Glitter", "Yellow Shine", "Green Plain", "Green Glitter", "Green Shine",
        "Black Plain", "Black Glitter", "Black Shine", "Red Plain", "Red Glitter", "Red Shine",
        "Purple Plain", "Purple Glitter", "Purple Shine",
        "Brown", "Maroon", "Turquoise", "Peach", "Navy Blue", "Cream"],
    "Chocolates": ["Diary White", "Diary Dark", "Bounty", "Monti", "Candy", "Kit Kat", "Snickers",
        "Macarons", "Rainbows"],
    "Cake Boxes": ["10\"", "12\"", "14\"", "14\" Long", "17\"", "12 Cupcake", "6 Cupcake", "Bento", "Transparent"],
    "Cake Boards": ["10\"", "12\"", "14\"", "17\""],
    "Wrapping Paper": ["10\"", "12\"", "14\"", "16\"", "17\"", "Other"],
}
MATERIAL_VARIANTS = {
    item: sorted([v for v in variants if v != "Other"]) + (["Other"] if "Other" in variants else [])
    for item, variants in MATERIAL_VARIANTS.items()
}

STAGE_MATERIALS = {
    "Baking": [
        "Flour", "Sugar", "Eggs", "Raisins", "Caramel", "Mixed Spice", "Nutmeg",  # baking-only
        "Vanilla Extract", "Lemon Emulsion", "Icing Sugar", "Prestige",  # Baking-only (Decor no longer uses these)
        "Glucose", "Milk", "Yogurt", "Cooking Oil", "Soap", "Lemons", "Oranges", "Coconut Cream", "Vinegar",
        "Gypsy", "Kimbo", "Dark Cocoa Powder", "Milk Powder Flavor", "Baking Powder", "Moulds",
        "Cling Film", "Foil", "Baking Paper", "Wax Paper", "Rice Paper", "Wafer Paper",
        "Maimun Colors 240ml", "Maimun Colours 50ml", "Pradip",
        "Cupcake Cups for Cupcake Bouquets", "Cupcake Cups",
        "Other",
    ],
    "Filling / Piling": ["Icing Sugar", "Eggs", "Buttercream", "Cake Boards", "Dowels", "Other"],
    "Coating / Covering": ["Fondant", "Icing Sugar", "Glucose", "Glycerine", "Pradip", "Gelatin", "Colour", "CMC", "Other"],
    "Decoration": [
        "Icing Sugar", "Eggs",  # used by both Baking and Decor
        "Corn Flour",  # decor-only
        "Chocolates", "Maimun Colors 240ml", "Maimun Colours 50ml", "Pradip",
        "Fondant", "Buttercream", "Waffle Paper", "Ice Cream Cones", "Pearls", "Candles", "Gold Leaves",
        "Flowers", "Balls", "Palm Leaf", "Butterflies", "Crowns", "Topper Paper", "Ribbons",
        "Super Glue", "Scissors", "Cutters", "Rolling Pin",
        "Stickers", "Cake Album Stickers", "Cookie Stickers",
        "Other",
    ],
    "Packaging": ["Cake Boxes", "Wrapping Paper", "Envelopes", "Packing Bags", "Sticker", "Ribbon", "Bag", "Tape", "Other"],
    "Design & Innovation": ["Super Glue", "Stick Glue", "Topper Paper", "Sticker Paper", "Other"],
}
# Sort every material list alphabetically (ascending) for faster scanning — "Other" always stays
# last since it's a fallback/free-text option, not a real item to pick from.
STAGE_MATERIALS = {
    stage: sorted([i for i in items if i != "Other"]) + (["Other"] if "Other" in items else [])
    for stage, items in STAGE_MATERIALS.items()
}


def available_inventory_view():
    inv = load_table("baked_cake_inventory")
    if inv.empty:
        return inv
    if "layers_available" not in inv.columns:
        inv["layers_available"] = inv["number_of_layers"].fillna(0).astype(int) * inv["quantity_available"].fillna(0).astype(int)
    return inv[(inv["inventory_status"].isin(["Available", "Reserved"])) & (inv["layers_available"].fillna(0).astype(int) > 0)].copy()


def render_customer_care_inventory_view():
    st.markdown("### Available Baked Cake Inventory — View Only")
    st.caption("Customer Care can view available sizes/flavours for urgent clients. Production Planning reserves inventory.")
    inv = available_inventory_view()
    table(inv.sort_values(["date_baked","flavour"]) if not inv.empty else inv,
          ["id","date_baked","flavour","cake_size_value","cake_shape","layers_available","quantity_available","baker","storage_location","inventory_status"])


def record_layer_usage(inventory_id, order_id, stage, layers_used, used_by, notes=""):
    with connect() as conn:
        row = conn.execute("SELECT layers_available, number_of_layers, quantity_available FROM baked_cake_inventory WHERE id=?", (int(inventory_id),)).fetchone()
        if row is None:
            raise ValueError("Inventory record not found.")
        current_layers = row["layers_available"]
        if current_layers is None:
            current_layers = int(row["number_of_layers"] or 0) * int(row["quantity_available"] or 0)
        new_balance = max(int(current_layers) - int(layers_used), 0)
        new_status = "Available" if new_balance > 0 else "Used"
        conn.execute("UPDATE baked_cake_inventory SET layers_available=?, inventory_status=? WHERE id=?",
                     (new_balance, new_status, int(inventory_id)))
        conn.execute("""INSERT INTO layer_inventory_usage(inventory_id,order_id,stage,layers_used,used_by,used_at,notes)
                        VALUES(?,?,?,?,?,?,?)""",
                     (int(inventory_id), order_id, stage, int(layers_used), used_by, now_iso(), notes))
        conn.commit()


def topper_target_datetime(row):
    try:
        due = datetime.fromisoformat(f"{row.get('due_date')}T{str(row.get('expected_time'))[:8]}")
        return due - pd.Timedelta(hours=2)
    except Exception:
        return None

def topper_urgency(row):
    if str(row.get("topper_required")) != "Yes":
        return "Not Required"
    if str(row.get("topper_status")) in ["Ready","Received by Decorator"]:
        return "Completed"
    target = topper_target_datetime(row)
    if target is None: return "🟢 NORMAL TIME"
    delta = target.to_pydatetime() - datetime.now() if hasattr(target,"to_pydatetime") else target - datetime.now()
    mins = delta.total_seconds()/60
    if mins < 0: return "⚠️ DELAYED / OVERDUE"
    if mins <= 30: return "🚨 DUE NOW"
    if mins <= 120: return "🟡 DUE SOON"
    return "🟢 NORMAL TIME"

def create_notification(order_id, target_department, target_person, message):
    """Save the in-app notification immediately; send web-push in the background.

    Web-push is an external network call and can take seconds per device. It must never hold
    up Customer Care, a baker, piler, coverer or decorator while they are moving a cake.
    """
    with connect() as conn:
        conn.execute("""INSERT INTO notifications(order_id,target_department,target_person,message,notification_status,created_at)
                        VALUES(?,?,?,?,?,?)""",(order_id,target_department,target_person,message,"Unread",now_iso()))
        conn.commit()
    threading.Thread(
        target=send_push_notification,
        args=(target_department, "Cake Album Operations", message),
        daemon=True,
        name=f"push-{str(order_id)[-8:]}"
    ).start()


def send_push_notification(target_department, title, body):
    """Sends a real push notification (works even with the browser closed) to every phone/desktop
    subscribed for this department. Logs every step so failures are actually traceable via
    `journalctl -u cakealbum` — this must never raise and break the app's core notification
    system, but it should never fail silently either. flush=True on every line because print()
    output can otherwise sit in a buffer indefinitely when running as a background service,
    never actually reaching journalctl in real time."""
    print(f"[PUSH] send_push_notification called for department='{target_department}'", flush=True)
    if not VAPID_PRIVATE_KEY_FILE.exists():
        print(f"[PUSH] SKIPPED — VAPID_PRIVATE_KEY_FILE not found at {VAPID_PRIVATE_KEY_FILE}", flush=True)
        return
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        print("[PUSH] SKIPPED — pywebpush is not installed in this environment", flush=True)
        return
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        try:
            all_subs = conn.execute("SELECT endpoint, department, subscription_json FROM push_subscriptions").fetchall()
        except sqlite3.OperationalError as e:
            print(f"[PUSH] SKIPPED — push_subscriptions table not queryable: {e}", flush=True)
            return
    print(f"[PUSH] {len(all_subs)} total subscription(s) in the database", flush=True)
    subs = [s for s in all_subs if target_department in [d.strip() for d in (s["department"] or "").split(",")]]
    print(f"[PUSH] {len(subs)} subscription(s) match department '{target_department}'", flush=True)
    dead_endpoints = []
    for sub in subs:
        endpoint_short = sub["endpoint"][:60]
        try:
            webpush(
                subscription_info=json.loads(sub["subscription_json"]),
                data=json.dumps({"title": title, "body": body}),
                vapid_private_key=str(VAPID_PRIVATE_KEY_FILE),
                vapid_claims=dict(VAPID_CLAIMS),
                ttl=86400,           # keep the message queued for 24h if the phone is offline
                headers={"Urgency": "high"},
            )
            print(f"[PUSH] SUCCESS sending to {endpoint_short}...", flush=True)
        except WebPushException as e:
            status = e.response.status_code if e.response is not None else "no response"
            body_text = e.response.text if e.response is not None else ""
            print(f"[PUSH] FAILED (WebPushException) sending to {endpoint_short}... — status={status} body={body_text[:200]}", flush=True)
            if e.response is not None and e.response.status_code in (404, 410):
                dead_endpoints.append(sub["endpoint"])
        except Exception as e:
            print(f"[PUSH] FAILED (unexpected {type(e).__name__}) sending to {endpoint_short}... — {e}", flush=True)
    if dead_endpoints:
        with connect() as conn:
            conn.executemany("DELETE FROM push_subscriptions WHERE endpoint=?", [(e,) for e in dead_endpoints])
            conn.commit()
        print(f"[PUSH] Removed {len(dead_endpoints)} dead subscription(s)", flush=True)

MATERIAL_COLOURS = ["N/A", "White", "Red", "Pink", "Purple", "Blue", "Green", "Yellow",
                    "Gold", "Silver", "Black", "Brown", "Ivory / Cream", "Multicolour", "Other"]


def _split_people(value):
    """'Ann, Ben ; Cee' -> ['Ann', 'Ben', 'Cee'] (blanks and placeholders dropped)."""
    raw = str(value or "").replace(";", ",")
    return [p.strip() for p in raw.split(",") if p.strip() and p.strip().lower() not in ("nan", "none", "—", "-")]


def _person_matches(person, username):
    """Match a roster name against a push subscription username tolerantly, because staff
    log in with a username while assignments store the full name (e.g. 'Ann Mwangi' vs 'ann')."""
    p = str(person or "").strip().lower()
    u = str(username or "").strip().lower()
    if not p or not u:
        return False
    if p == u:
        return True
    p_parts = set(p.replace(".", " ").replace("_", " ").split())
    u_parts = set(u.replace(".", " ").replace("_", " ").split())
    return bool(p_parts & u_parts)


def send_push_to_people(people, title, body, url=None):
    """Push straight to named individuals (mixers, oven crew, assemblers) instead of blasting a
    whole department. Never raises — notifications must not be able to break the workflow."""
    names = [n for n in people if str(n).strip()]
    if not names:
        return 0
    print(f"[PUSH] send_push_to_people called for {names}")
    if not VAPID_PRIVATE_KEY_FILE.exists():
        print(f"[PUSH] SKIPPED — VAPID_PRIVATE_KEY_FILE not found at {VAPID_PRIVATE_KEY_FILE}")
        return 0
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        print("[PUSH] SKIPPED — pywebpush is not installed in this environment")
        return 0
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        try:
            all_subs = conn.execute("SELECT endpoint, username, subscription_json FROM push_subscriptions").fetchall()
        except sqlite3.OperationalError as e:
            print(f"[PUSH] SKIPPED — push_subscriptions table not queryable: {e}")
            return 0
    subs = [s for s in all_subs if any(_person_matches(n, s["username"]) for n in names)]
    print(f"[PUSH] {len(subs)} subscription(s) match {names}")
    sent, dead_endpoints = 0, []
    for sub in subs:
        endpoint_short = sub["endpoint"][:60]
        try:
            webpush(
                subscription_info=json.loads(sub["subscription_json"]),
                data=json.dumps({"title": title, "body": body, "url": url or "/"}),
                vapid_private_key=str(VAPID_PRIVATE_KEY_FILE),
                vapid_claims=dict(VAPID_CLAIMS),
                ttl=86400,
                headers={"Urgency": "high"},
            )
            sent += 1
            print(f"[PUSH] SUCCESS sending to {endpoint_short}...")
        except WebPushException as e:
            status = e.response.status_code if e.response is not None else "no response"
            print(f"[PUSH] FAILED sending to {endpoint_short}... — status={status}")
            if e.response is not None and e.response.status_code in (404, 410):
                dead_endpoints.append(sub["endpoint"])
        except Exception as e:
            print(f"[PUSH] FAILED (unexpected {type(e).__name__}) sending to {endpoint_short}... — {e}")
    if dead_endpoints:
        with connect() as conn:
            conn.executemany("DELETE FROM push_subscriptions WHERE endpoint=?", [(e,) for e in dead_endpoints])
            conn.commit()
        print(f"[PUSH] Removed {len(dead_endpoints)} dead subscription(s)")
    return sent


def notify_people(order_id, target_department, people, message):
    """In-app notification per named person + a push to just those people's phones."""
    names = _split_people(", ".join([str(p) for p in people if str(p).strip()]))
    if not names:
        return
    with connect() as conn:
        conn.executemany(
            """INSERT INTO notifications(order_id,target_department,target_person,message,notification_status,created_at)
               VALUES(?,?,?,?,?,?)""",
            [(order_id, target_department, n, message, "Unread", now_iso()) for n in names])
        conn.commit()
    send_push_to_people(names, "Cake Album Operations", message)


def notify_cake_finished(order_id, message, mixers=None, oven_crew=None, assemblers=None):
    """Fired every time ONE cake is marked finished in Baking — batch tick-off or individual
    bake. Mixers and oven crew get told their cake is out; assemblers (Filling / Piling) get
    told a cake is landing on their bench, per cake rather than per batch."""
    crew = _split_people(", ".join(filter(None, [
        ", ".join(_split_people(mixers)) if mixers else "",
        ", ".join(_split_people(oven_crew)) if oven_crew else "",
    ])))
    if crew:
        notify_people(order_id, "Baking", crew, message)
    assembly_people = _split_people(assemblers) if assemblers else []
    if assembly_people:
        notify_people(order_id, "Filling / Piling", assembly_people, message)


# Families whose variants are sizes (stored in the 'size' column) rather than colours/flavors ('colour' column)
MATERIAL_SIZE_FAMILIES = {"Cake Boxes", "Cake Boards", "Wrapping Paper"}



# ---------------------------------------------------------------------------
# TEAM COLLABORATION  —  Order-threaded comments + AI Assistant
#
#  * order_comments        : one chat thread per order (plus a general team thread)
#  * @mentions             : in-app notification + real web push to that person
#  * AI Assistant          : answers questions about live orders using the same
#                            data the dashboards use. Configure with env vars:
#                              AI_API_KEY   (or LOVABLE_API_KEY / OPENAI_API_KEY)
#                              AI_BASE_URL  (default: Lovable AI Gateway)
#                              AI_MODEL     (default: google/gemini-3.7-flash)
# ---------------------------------------------------------------------------
GENERAL_THREAD_ID = "__general__"

AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://ai.gateway.lovable.dev/v1").rstrip("/")
AI_MODEL = os.environ.get("AI_MODEL", "google/gemini-3.7-flash")


def _ai_api_key():
    for name in ("AI_API_KEY", "LOVABLE_API_KEY", "OPENAI_API_KEY"):
        val = os.environ.get(name)
        if val:
            return val
    return None


def ensure_collaboration_schema():
    """Comments live in their own table so nothing about the order pipeline changes."""
    with connect() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS order_comments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            author_username TEXT,
            author_name TEXT,
            author_department TEXT,
            message TEXT NOT NULL,
            mentions TEXT,
            is_ai TEXT DEFAULT 'No',
            created_at TEXT NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_order_comments_order ON order_comments(order_id, id)")
        conn.commit()


def _all_mentionable_people():
    """[(username, full_name, departments)] for every active account."""
    try:
        with connect() as conn:
            rows = conn.execute(
                "SELECT username, full_name, departments, department FROM staff_accounts WHERE is_active='Yes'"
            ).fetchall()
        return [(r["username"], r["full_name"], r["departments"] or r["department"] or "") for r in rows]
    except Exception:
        return []


def parse_mentions(message: str):
    """Return the list of usernames genuinely mentioned with @. Accepts @username and
    @firstname, matched case-insensitively, so people can type the name they know."""
    tokens = {t.lower() for t in re.findall(r"@([A-Za-z0-9_.\-]+)", message or "")}
    if not tokens:
        return []
    hits = []
    for username, full_name, _departments in _all_mentionable_people():
        uname = (username or "").lower()
        first = (full_name or "").strip().split(" ")[0].lower()
        if uname in tokens or (first and first in tokens):
            hits.append(username)
    return sorted(set(hits))


def add_order_comment(order_id, message, author_username, author_name, author_department, is_ai=False):
    """Save one order-thread message and alert the right people.

    A cake thread now behaves like a real conversation: explicit @mentions are notified,
    and when somebody replies without typing an @name, the author of the most recent human
    message is notified automatically. This fixes the old one-way flow where Brenda could
    receive Desmond's mention but Desmond would not know she had replied unless she manually
    typed @desmond again.
    """
    ensure_collaboration_schema()
    mentions = [] if is_ai else parse_mentions(message)

    # Remember the previous human speaker BEFORE inserting the new message. They become the
    # automatic reply target unless the new author is replying to themselves or already
    # mentioned them explicitly.
    reply_username = None
    if not is_ai and order_id != GENERAL_THREAD_ID:
        try:
            with connect() as conn:
                prev = conn.execute(
                    """SELECT author_username FROM order_comments
                       WHERE order_id=? AND is_ai!='Yes' AND COALESCE(author_username,'')!=''
                       ORDER BY id DESC LIMIT 1""", (order_id,)
                ).fetchone()
            if prev:
                candidate = str(prev["author_username"] or "").strip()
                if candidate and candidate.lower() != str(author_username or "").strip().lower():
                    reply_username = candidate
        except Exception as e:
            print(f"[COMMENTS] Could not resolve reply target: {e}", flush=True)

    with connect() as conn:
        conn.execute("""INSERT INTO order_comments(order_id, author_username, author_name,
                        author_department, message, mentions, is_ai, created_at)
                        VALUES(?,?,?,?,?,?,?,?)""",
                     (order_id, author_username, author_name, author_department,
                      message, ",".join(mentions), "Yes" if is_ai else "No", now_iso()))
        conn.commit()

    if is_ai:
        return []

    recipients = set(mentions)
    if reply_username:
        recipients.add(reply_username)
    recipients.discard(str(author_username or "").strip())
    recipients = sorted(r for r in recipients if r)
    if not recipients:
        return []

    explicit = set(mentions)
    people = _all_mentionable_people()
    by_username = {u: (fn, dp) for u, fn, dp in people}
    for uname in recipients:
        full_name, departments = by_username.get(uname, (uname, ""))
        dept = (departments.split(",")[0] or "").strip() if departments else ""
        if uname in explicit:
            body = f"{first_name(author_name)} mentioned you on cake {order_id}: {message[:120]}"
        else:
            body = f"{first_name(author_name)} replied on cake {order_id}: {message[:120]}"
        try:
            with connect() as conn:
                conn.execute("""INSERT INTO notifications(order_id,target_department,target_person,
                                message,notification_status,created_at) VALUES(?,?,?,?,?,?)""",
                             (order_id, dept, full_name, body, "Unread", now_iso()))
                conn.commit()
        except Exception as e:
            print(f"[COMMENTS] Could not create cake-chat notification for {uname}: {e}", flush=True)

    # Phone push carries the order reference too. The in-app notification always provides a
    # reliable Open & Reply button; the push URL is a useful hint for installed/PWA clients.
    try:
        body = f"{first_name(author_name)} sent a message on cake {order_id}: {message[:120]}"
        send_push_to_people(recipients, "Cake message — reply needed", body, url=f"/?thread={order_id}")
    except Exception as e:
        print(f"[COMMENTS] Push for cake conversation failed: {e}", flush=True)
    return recipients

def load_order_comments(order_id, limit=200):
    ensure_collaboration_schema()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM order_comments WHERE order_id=? ORDER BY id DESC LIMIT ?", (order_id, limit)
        ).fetchall()
    return list(reversed(rows))


def render_order_comments(order_id, key_suffix="", title="💬 Team Comments", expanded=False):
    """Chat thread for one order. Safe to drop anywhere an order is shown."""
    if not order_id:
        return
    # Widget keys MUST stay stable across reruns, otherwise Streamlit rebuilds the
    # buttons with new keys and the click is never registered (comment never posts).
    # Only de-duplicate when the exact same thread is drawn twice in one script run.
    run_id = st.session_state.get("_script_run_id", 0)
    seen = st.session_state.get("_cmt_seen_keys")
    if not isinstance(seen, dict) or seen.get("__run__") != run_id:
        seen = {"__run__": run_id}
    stem = f"cmt_{str(order_id).replace(' ', '_')}_{key_suffix}"
    dup = seen.get(stem, 0)
    seen[stem] = dup + 1
    st.session_state["_cmt_seen_keys"] = seen
    base = stem if dup == 0 else f"{stem}_{dup}"
    rows = load_order_comments(order_id)
    label = f"{title} ({len(rows)})" if rows else title
    with st.expander(label, expanded=expanded):
        if not rows:
            st.caption("No comments yet. Use @name to alert a colleague — they get a phone notification.")
        for r in rows:
            who = "🤖 ERP Assistant" if r["is_ai"] == "Yes" else f"{first_name(r['author_name'])}"
            dept = f" · {r['author_department']}" if r["author_department"] else ""
            stamp = (r["created_at"] or "")[:16].replace("T", " ")
            st.markdown(
                f"<div style='border-left:3px solid #C9A227;padding:4px 10px;margin:6px 0;'>"
                f"<div style='font-size:.75rem;color:#777;'>{who}{dept} · {stamp}</div>"
                f"<div style='white-space:pre-wrap;'>{_highlight_mentions(r['message'])}</div></div>",
                unsafe_allow_html=True)

        # Keep the composer inside a form. Streamlit otherwise reruns the script while a
        # phone user is interacting with the field, which can make the keyboard/cursor appear
        # to "sleep" or disappear on low-end Android devices. A form holds the text locally
        # until the worker explicitly taps Post or Ask AI. A single-line input is also much
        # easier to focus reliably on a small touchscreen than the old text area.
        with st.form(f"{base}_composer_form", clear_on_submit=True):
            msg = st.text_input(
                "Message / Ask AI",
                placeholder="Tap here and type…  Use @name to notify someone",
                key=f"{base}_msg",
            )
            cols = st.columns(2)
            post_clicked = cols[0].form_submit_button("💬 Post", width='stretch')
            ai_clicked = cols[1].form_submit_button("🤖 Ask AI", width='stretch')

        if post_clicked:
            if not (msg or "").strip():
                st.warning("Type something first.")
            else:
                mentioned = add_order_comment(
                    order_id, msg.strip(),
                    st.session_state.get("username", ""),
                    st.session_state.get("staff_name", ""),
                    st.session_state.get("department", ""))
                if mentioned:
                    st.success(f"Posted — notified {', '.join(mentioned)}.")
                else:
                    st.success("Posted.")
                st.rerun()

        if ai_clicked:
            if not (msg or "").strip():
                st.warning("Type your question first.")
            else:
                with st.spinner("Thinking..."):
                    answer = ai_assistant_answer(msg.strip(), order_id=order_id)
                add_order_comment(order_id, msg.strip(), st.session_state.get("username", ""),
                                  st.session_state.get("staff_name", ""), st.session_state.get("department", ""))
                add_order_comment(order_id, answer, "ai", "ERP Assistant", "", is_ai=True)
                st.rerun()


def _highlight_mentions(text: str) -> str:
    safe = (text or "").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"@([A-Za-z0-9_.\-]+)",
                  r"<span style='color:#7B2D5E;font-weight:700;'>@\1</span>", safe)


# ------------------------------- AI Assistant -------------------------------
def _ai_context_snapshot(order_id=None, max_orders=60):
    """A compact, factual snapshot of live operations for the model to reason over."""
    try:
        df = load_orders()
    except Exception:
        return "No order data available."
    if df is None or df.empty:
        return "There are no orders in the system."
    cols = [c for c in ["order_id", "customer_name", "product_type", "flavours", "cake_size_value",
                        "workflow_status", "due_date", "urgency_level", "baker_assigned",
                        "piler_assigned", "coverer_assigned", "decorator_assigned", "driver_assigned"]
            if c in df.columns]
    live = df
    if "workflow_status" in df.columns:
        live = df[df["workflow_status"].fillna("") != "Follow-up Done"]
    lines = []
    if "workflow_status" in df.columns:
        counts = df["workflow_status"].fillna("Unknown").value_counts().head(20)
        lines.append("Order counts by stage: " + "; ".join(f"{k}={v}" for k, v in counts.items()))
    snapshot = live[cols].head(max_orders) if cols else live.head(max_orders)
    lines.append("Active orders (CSV):")
    lines.append(snapshot.to_csv(index=False))
    if order_id and order_id != GENERAL_THREAD_ID and "order_id" in df.columns:
        focus = df[df["order_id"] == order_id]
        if not focus.empty:
            lines.append(f"The user is currently looking at order {order_id}:")
            lines.append(focus.head(1).to_csv(index=False))
        recent = load_order_comments(order_id, limit=15)
        if recent:
            lines.append("Recent comments on this order:")
            for r in recent:
                lines.append(f"- {r['author_name']}: {r['message']}")
    return "\n".join(lines)[:14000]


def ai_assistant_answer(question: str, order_id=None) -> str:
    """Ask the configured AI gateway. Never raises — returns a readable message instead."""
    key = _ai_api_key()
    if not key:
        return ("AI assistant is not configured on this server. Add an API key, e.g. "
                "`Environment=AI_API_KEY=...` in /etc/systemd/system/cakealbum.service, then restart the service.")
    try:
        import requests
    except ImportError:
        return "The `requests` package is not installed on this server (pip install requests)."

    headers = {"Content-Type": "application/json"}
    if "gateway.lovable.dev" in AI_BASE_URL:
        headers["Lovable-API-Key"] = key
    else:
        headers["Authorization"] = f"Bearer {key}"

    system = (
        f"You are the Cake Album Operations assistant, embedded in a bakery ERP. Today's date is {date.today().isoformat()}. "
        "Answer strictly from the operations snapshot provided. Be short and practical: "
        "name orders, stages, staff and dates. An order is overdue if its due_date is before today's date "
        "and its workflow_status isn't a finished/delivered stage - work this out yourself by comparing dates, "
        "don't say you lack the information if due_date is present in the snapshot. "
        "If the snapshot genuinely does not contain the answer, say so. Never invent order IDs or customers."
    )
    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Operations snapshot:\n{_ai_context_snapshot(order_id)}\n\nQuestion: {question}"},
        ],
    }
    try:
        resp = requests.post(f"{AI_BASE_URL}/chat/completions", headers=headers, json=payload, timeout=60)
    except Exception as e:
        return f"Could not reach the AI service: {e}"
    if resp.status_code == 429:
        return "The AI service is rate-limited right now. Please try again in a moment."
    if resp.status_code == 402:
        return "AI credits are exhausted. Top up the workspace credits to keep using the assistant."
    if resp.status_code >= 400:
        print(f"[AI] {resp.status_code} {resp.text[:300]}", flush=True)
        return f"AI request failed ({resp.status_code}). Details are in the server log."
    try:
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return "The AI service returned an unexpected response."


def render_team_hub():
    """Standalone page: general team chat, per-order threads and the AI assistant."""
    page_header("Team Chat & AI Assistant", "Talk to each other on the order itself — and ask the ERP anything.")
    ensure_collaboration_schema()
    df = load_orders()

    # If the worker arrived from a cake-message notification, do not make them search for
    # the order again. Put the exact conversation at the top, already expanded and ready
    # for typing — WhatsApp-like: notification -> conversation -> reply.
    open_thread = st.session_state.get("_open_order_thread")
    if open_thread:
        focus = df[df["order_id"].astype(str) == str(open_thread)] if not df.empty and "order_id" in df.columns else pd.DataFrame()
        st.markdown("## 💬 Reply to this cake")
        if not focus.empty:
            order_card(focus.iloc[0], show_image=False)
        else:
            st.info(f"Cake {open_thread} is not in the current active order list, but its conversation is still available below.")
        render_order_comments(str(open_thread), key_suffix="notification_reply", title="💬 Cake Conversation", expanded=True)
        if st.button("✅ Done with this conversation", key="close_notification_thread", width='stretch'):
            st.session_state.pop("_open_order_thread", None)
            st.rerun()
        st.divider()

    tab_general, tab_order, tab_ai = st.tabs(["🗣️ Team Chat", "📦 Order Threads", "🤖 Ask the ERP"])

    with tab_general:
        st.caption("Everyone sees this. Use @name to alert a specific person on their phone.")
        render_order_comments(GENERAL_THREAD_ID, key_suffix="general", title="💬 Team Chat", expanded=True)

    with tab_order:
        if df is None or df.empty:
            st.info("No orders yet.")
        else:
            row = select_order(df, key="team_hub_order_pick", label="Pick an order to discuss")
            if row is not None:
                order_card(row, show_image=False)
                render_order_comments(row["order_id"], key_suffix="hub", expanded=True)

    with tab_ai:
        st.caption("Ask about live orders: “Which cakes are late today?”, “What is Ann baking?”, “How many orders are in Decoration?”")
        question = st.text_area("Your question", key="ai_hub_q", height=90)
        if st.button("Ask", key="ai_hub_ask"):
            if not (question or "").strip():
                st.warning("Type a question first.")
            else:
                with st.spinner("Checking live operations data..."):
                    answer = ai_assistant_answer(question.strip())
                st.session_state["_ai_last_answer"] = answer
        if st.session_state.get("_ai_last_answer"):
            st.markdown("#### Answer")
            st.markdown(st.session_state["_ai_last_answer"])
        if not _ai_api_key():
            st.warning("AI key not configured on this server yet — see the setup note in the answer box after asking.")


def render_stage_material_planning(stage, row, default_by, key_prefix=None):
    order_key = str(row.get("order_id") if hasattr(row, "get") else row.order_id).replace(" ", "_")
    key_prefix = f"mat_{stage}_{order_key}" if key_prefix is None else f"mat_{stage}_{order_key}_{key_prefix}"

    # Fetch only this cake/stage instead of loading the full materials table on every phone.
    with connect() as conn:
        usage = pd.read_sql_query(
            "SELECT * FROM stage_material_usage WHERE order_id=? AND stage=? ORDER BY id DESC",
            conn, params=(row.order_id, stage)
        )

    st.markdown("### Materials Planning / Usage")
    if usage.empty:
        st.warning("⚠️ Add at least one material before starting this stage.")
    else:
        st.success(f"✅ {len(usage)} material entr{'y' if len(usage)==1 else 'ies'} saved — Start can proceed.")

    # The old control looked like an action button even though the fields were already
    # elsewhere on the page. On small phones that made staff think nothing happened.
    # This expander makes the interaction explicit: tap it, the entry form opens, save it.
    with st.expander("➕ ADD MATERIAL / USAGE", expanded=usage.empty):
        st.caption("Tap here, enter what this cake needs/uses, then press SAVE MATERIAL.")
        a, b = st.columns(2)
        item_choice = a.selectbox("Material item", STAGE_MATERIALS.get(stage, ["Other"]), key=f"{key_prefix}_item")
        item = st.text_input("Specify material name", key=f"{key_prefix}_item_other") if item_choice == "Other" else item_choice
        is_size_family = item_choice in MATERIAL_SIZE_FAMILIES
        if item_choice in MATERIAL_VARIANTS:
            label = "Size" if is_size_family else "Colour / Flavor / Variant"
            variant = b.selectbox(label, MATERIAL_VARIANTS[item_choice], key=f"{key_prefix}_variant")
        else:
            colour_choice = b.selectbox("Colour (if applicable)", MATERIAL_COLOURS, key=f"{key_prefix}_colour")
            variant = "" if colour_choice == "N/A" else colour_choice
        action = st.selectbox("Action", ["Used", "Needed", "Request from Procurement"], key=f"{key_prefix}_action")
        skip_measuring = stage == "Decoration" and str(item_choice).strip().lower() in ("fondant", "buttercream")
        if skip_measuring:
            st.caption("Decorators don't measure Fondant/Buttercream by quantity — just log that it was used.")
            qty, unit, multiplier, total_qty = 1.0, "not measured", 1.0, 1.0
        elif is_size_family:
            qty = st.number_input("Quantity (pieces)", min_value=0.0, step=1.0, key=f"{key_prefix}_qty")
            unit, multiplier, total_qty = "pieces", 1.0, qty
        else:
            d, e = st.columns(2)
            qty = d.number_input("Quantity / Weight per unit", min_value=0.0, step=0.5, key=f"{key_prefix}_qty")
            unit = e.selectbox("Unit", ["kg", "grams", "pieces", "trays", "boxes", "litres", "ml", "teaspoon", "tablespoon"], key=f"{key_prefix}_unit")
            multiplier = st.number_input("Multiplier (batches, e.g. x3)", min_value=0.0, step=0.5, value=1.0, key=f"{key_prefix}_multiplier")
            total_qty = qty * multiplier
            if multiplier != 1:
                st.caption(f"= {qty:g} {unit} × {multiplier:g} = **{total_qty:g} {unit} total**")
        by = st.text_input("Recorded by", value=str(default_by or stage), key=f"{key_prefix}_by")
        if st.button("✅ SAVE MATERIAL", key=f"{key_prefix}_add", width='stretch'):
            if not str(item).strip():
                st.error("Specify the material name.")
            elif total_qty <= 0:
                st.error("Enter a quantity/weight greater than zero.")
            else:
                colour_val = variant if (variant and not is_size_family) else ""
                size_val = variant if (variant and is_size_family) else ""
                item_label = f"{item} ({variant})" if variant else item
                with connect() as conn:
                    conn.execute("""INSERT INTO stage_material_usage(order_id,stage,item_name,colour,size,quantity,unit,material_action,recorded_by,recorded_at,base_quantity,multiplier)
                                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                                 (row.order_id, stage, item, colour_val, size_val, total_qty, unit, action, by, now_iso(), qty, multiplier))
                    if action == "Request from Procurement":
                        conn.execute("""INSERT INTO order_material_requirements(order_id,requested_by,item_name,quantity_required,unit,requirement_status,requested_at)
                                        VALUES(?,?,?,?,?,?,?)""",
                                     (row.order_id, by, item_label, total_qty, unit, "Submitted", now_iso()))
                    conn.commit()
                audit_log(row.order_id, "Stage Material Recorded", stage, f"{item_label}: {qty:g} x {multiplier:g} = {total_qty:g} {unit} — {action}", by)
                st.success("Material saved — Start Baking is now unlocked.")
                st.rerun()

    if not usage.empty:
        with st.expander("📋 MATERIALS ALREADY SAVED", expanded=False):
            table(usage, ["id","item_name","colour","size","base_quantity","multiplier","quantity","unit","material_action","recorded_by","recorded_at"])

    if not usage.empty and st.session_state.get("is_hod"):
        st.markdown("##### 👑 HOD: Correct or Remove an Entry")
        st.caption("If a team member made a mistake logging a material — wrong quantity, wrong item, wrong unit — fix it here. Team members should ask their HOD to make this correction.")
        pick_id = st.selectbox("Select entry to correct", usage["id"].tolist(), key=f"{key_prefix}_hod_pick")
        entry = usage[usage["id"] == pick_id].iloc[0]
        ca, cb, cc = st.columns(3)
        fix_item = ca.text_input("Material name", value=disp(entry.get("item_name")), key=f"{key_prefix}_hod_item")
        fix_qty = cb.number_input("Quantity (final total)", min_value=0.0, step=0.5, value=float(entry.get("quantity") or 0), key=f"{key_prefix}_hod_qty")
        fix_unit = cc.selectbox("Unit", ["kg", "grams", "pieces", "trays", "boxes", "litres", "ml", "teaspoon", "tablespoon"],
                                 index=(["kg", "grams", "pieces", "trays", "boxes", "litres", "ml", "teaspoon", "tablespoon"].index(entry.get("unit")) if entry.get("unit") in ["kg", "grams", "pieces", "trays", "boxes", "litres", "ml", "teaspoon", "tablespoon"] else 0),
                                 key=f"{key_prefix}_hod_unit")
        hod_name = st.text_input("Corrected by (HOD)", value=st.session_state.get("staff_name", ""), key=f"{key_prefix}_hod_by")
        fix_col, del_col = st.columns(2)
        if fix_col.button("💾 Save Correction", key=f"{key_prefix}_hod_save", width='stretch'):
            with connect() as conn:
                conn.execute("UPDATE stage_material_usage SET item_name=?, quantity=?, unit=?, edited_by=?, edited_at=? WHERE id=?",
                             (fix_item.strip(), fix_qty, fix_unit, hod_name, now_iso(), int(pick_id)))
                conn.commit()
            audit_log(row.order_id, "Material Entry Corrected by HOD", stage, f"Entry #{pick_id} → {fix_item}: {fix_qty} {fix_unit}", hod_name)
            st.success("Entry corrected."); st.rerun()
        if del_col.button("🗑️ Delete Entry", key=f"{key_prefix}_hod_delete", width='stretch'):
            with connect() as conn:
                conn.execute("DELETE FROM stage_material_usage WHERE id=?", (int(pick_id),))
                conn.commit()
            audit_log(row.order_id, "Material Entry Deleted by HOD", stage, f"Entry #{pick_id} ({disp(entry.get('item_name'))}) removed", hod_name)
            st.success("Entry deleted."); st.rerun()
    elif not usage.empty:
        st.caption("Spotted a mistake in one of these entries? Ask your Head of Department to correct or remove it.")
    return not usage.empty


FOLLOWUP_CALL_TODAY_HOURS = 24
FOLLOWUP_OVERDUE_HOURS = 48


def render_followup_alert_board(df):
    """Alerts Customer Care to delivered orders still waiting on a follow-up call,
    aged by how long since delivery — so nobody forgets to call a customer back."""
    if df.empty or "workflow_status" not in df.columns:
        return
    pending = df[df["workflow_status"] == "Follow-up Pending"].copy()
    if pending.empty:
        return
    rows = []
    for _, r in pending.iterrows():
        elapsed_min = minutes_elapsed_since(r.get("delivered_at"))
        hours = elapsed_min / 60 if elapsed_min is not None else None
        if hours is None:
            label = "Just delivered"
        elif hours >= FOLLOWUP_OVERDUE_HOURS:
            label = f"🔴 Overdue — {hours/24:.1f} day(s)"
        elif hours >= FOLLOWUP_CALL_TODAY_HOURS:
            label = f"🟠 Call today — {hours:.0f}h since delivery"
        else:
            label = f"🟢 Due soon — {hours:.0f}h since delivery"
        rows.append({
            "Alert": label, "Order": r.get("order_id"), "Customer": r.get("customer_name"),
            "Phone": r.get("customer_number"), "Delivered At": r.get("delivered_at"),
            "Hours Since Delivery": round(hours, 1) if hours is not None else "",
        })
    rows.sort(key=lambda x: x["Hours Since Delivery"] if isinstance(x["Hours Since Delivery"], (int, float)) else -1, reverse=True)
    overdue_count = sum(1 for r in rows if "Overdue" in r["Alert"])
    st.markdown(f"### 📞 Follow-Up Calls Due — {len(rows)} pending" + (f", {overdue_count} overdue" if overdue_count else ""))
    st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')


def render_due_alert_board(df):
    alerts = []
    for _, r in df.iterrows():
        label, severity = due_alert(r)
        if label and str(r.get("workflow_status")) not in ["Follow-up Done"]:
            alerts.append({
                "Alert": label, "Order": r.get("order_id"), "Customer": r.get("customer_name"),
                "Due": r.get("due_date"), "Time": r.get("expected_time"),
                "Stage": r.get("workflow_status"), "Owner": r.get("current_owner"),
                "Next Action": r.get("next_action")
            })
    if alerts:
        st.markdown("### 🚨 Due-Time Alerts")
        st.dataframe(pd.DataFrame(alerts), hide_index=True, width='stretch')

def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def db_columns(table: str):
    with connect() as conn:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


HEAVY_ORDER_MEDIA_COLUMNS = {"reference_image_base64", "reference_images_json"}


@st.cache_data(show_spinner=False)
def load_orders_cached(db_mtime: float):
    """Load operational fields only.

    Customer photos used to be stored as base64 inside the orders table, so SELECT * caused
    every logged-in phone to repeatedly pull every historical cake image into memory. That
    made ordinary queue checks and form reruns progressively slower as the database grew.
    Images are now fetched lazily only for the one order that is actually opened.
    """
    with sqlite3.connect(DATABASE_FILE, timeout=15) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
        light_cols = [c for c in cols if c not in HEAVY_ORDER_MEDIA_COLUMNS]
        quoted = ",".join(f'"{c}"' for c in light_cols)
        return pd.read_sql_query(f"SELECT {quoted} FROM orders", conn)


@st.cache_data(show_spinner=False)
def load_order_media_cached(order_id: str, db_mtime: float):
    with sqlite3.connect(DATABASE_FILE, timeout=15) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
        wanted = [c for c in ("reference_image_base64", "reference_images_json", "reference_image_path") if c in cols]
        if not wanted:
            return {}
        quoted = ",".join(f'"{c}"' for c in wanted)
        row = conn.execute(f"SELECT {quoted} FROM orders WHERE order_id=?", (order_id,)).fetchone()
        return dict(zip(wanted, row)) if row else {}


def load_orders():
    mtime = DATABASE_FILE.stat().st_mtime if DATABASE_FILE.exists() else 0
    return load_orders_cached(mtime)


def load_order_media(order_id):
    mtime = DATABASE_FILE.stat().st_mtime if DATABASE_FILE.exists() else 0
    return load_order_media_cached(str(order_id), mtime)


def refresh_data():
    load_orders_cached.clear()
    load_order_media_cached.clear()


def audit_log(order_id: str | None, action_type: str, stage: str, details: str, performed_by: str):
    with connect() as conn:
        conn.execute(
            """INSERT INTO audit_logs(order_id, action_type, stage, action_details, performed_by, performed_at)
               VALUES(?,?,?,?,?,?)""",
            (order_id, action_type, stage, details, performed_by or "System", now_iso()),
        )
        conn.commit()


def update_order(order_id: str, updates: dict, updated_by: str = "System", action_type: str = "Order Update", stage: str = "") -> bool:
    updates = dict(updates)
    updates.setdefault("last_updated_at", now_iso())
    updates.setdefault("last_updated_by", updated_by)
    allowed = set(db_columns("orders"))
    updates = {k: v for k, v in updates.items() if k in allowed}
    if not updates:
        return False
    clause = ", ".join([f"{k} = ?" for k in updates.keys()])
    values = list(updates.values()) + [order_id]
    with connect() as conn:
        cur = conn.execute(f"UPDATE orders SET {clause} WHERE order_id = ?", values)
        conn.execute(
            """INSERT INTO audit_logs(order_id, action_type, stage, action_details, performed_by, performed_at)
               VALUES(?,?,?,?,?,?)""",
            (order_id, action_type, stage, str(updates), updated_by or "System", now_iso()),
        )
        conn.commit()
        ok = cur.rowcount > 0
    refresh_data()
    return ok


def insert_order(order: dict):
    allowed = set(db_columns("orders"))
    order = {k: v for k, v in order.items() if k in allowed}
    cols = list(order.keys())
    placeholders = ",".join(["?"] * len(cols))
    with connect() as conn:
        conn.execute(f"INSERT INTO orders({','.join(cols)}) VALUES({placeholders})", [order[c] for c in cols])
        conn.execute(
            """INSERT INTO audit_logs(order_id, action_type, stage, action_details, performed_by, performed_at)
               VALUES(?,?,?,?,?,?)""",
            (order.get("order_id"), "Order Created", "Customer Care", "New order created", order.get("last_updated_by") or "Customer Care", now_iso()),
        )
        conn.commit()
    refresh_data()


def insert_stage_check(order_id, from_stage, to_stage, checked_by, check_status, issue_category="", issue_description="", responsible_department="", responsible_person=""):
    with connect() as conn:
        conn.execute(
            """INSERT INTO stage_quality_checks(
                order_id, from_stage, to_stage, check_type, checked_by, check_status,
                issue_category, issue_description, responsible_department, responsible_person,
                checked_at, returned_at, resolution_status)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                order_id, from_stage, to_stage, f"{from_stage} to {to_stage}", checked_by,
                check_status, issue_category, issue_description, responsible_department,
                responsible_person, now_iso(), now_iso() if check_status == "Rejected" else None,
                "Correction Required" if check_status == "Rejected" else "Not Required",
            ),
        )
        conn.execute(
            """INSERT INTO audit_logs(order_id, action_type, stage, action_details, performed_by, performed_at)
               VALUES(?,?,?,?,?,?)""",
            (order_id, "Stage QC " + check_status, to_stage, issue_description or f"{from_stage} accepted by {to_stage}", checked_by, now_iso()),
        )
        conn.commit()
    refresh_data()


def create_material_requirement(order_id, requested_by, item_name, qty, unit):
    with connect() as conn:
        conn.execute(
            """INSERT INTO order_material_requirements(order_id, requested_by, item_name, quantity_required, unit, requirement_status, requested_at)
               VALUES(?,?,?,?,?,?,?)""",
            (order_id, requested_by, item_name, qty, unit, "Submitted", now_iso()),
        )
        conn.commit()


def load_table(table):
    with sqlite3.connect(DATABASE_FILE) as conn:
        return pd.read_sql_query(f"SELECT * FROM {table}", conn)


# -----------------------------
# General helpers
# -----------------------------

def generate_order_id():
    """Allocate the next daily order number atomically.

    Two Customer Care users can submit at almost the same moment. The old SELECT-max logic
    allowed both sessions to choose the same next ID. This tiny SQLite transaction serializes
    only the counter update (milliseconds), preventing collisions without slowing normal reads.
    """
    date_str = datetime.now().strftime("%Y%m%d")
    prefix = f"CA-{date_str}-"
    with connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS order_id_sequences(order_date TEXT PRIMARY KEY, last_num INTEGER NOT NULL)")
        conn.execute("BEGIN IMMEDIATE")
        # Bootstrap from any IDs that already exist for today, then increment once.
        row = conn.execute("SELECT last_num FROM order_id_sequences WHERE order_date=?", (date_str,)).fetchone()
        if row is None:
            existing = conn.execute("SELECT order_id FROM orders WHERE order_id LIKE ?", (f"{prefix}%",)).fetchall()
            max_num = 1999
            for item in existing:
                oid = item[0]
                suffix = str(oid)[len(prefix):]
                if suffix.isdigit():
                    max_num = max(max_num, int(suffix))
            conn.execute("INSERT INTO order_id_sequences(order_date,last_num) VALUES(?,?)", (date_str, max_num))
        conn.execute("UPDATE order_id_sequences SET last_num=last_num+1 WHERE order_date=?", (date_str,))
        next_num = conn.execute("SELECT last_num FROM order_id_sequences WHERE order_date=?", (date_str,)).fetchone()[0]
        conn.commit()
    return f"{prefix}{int(next_num)}"


def fmt_ugx(v):
    try:
        return f"UGX {float(v):,.0f}"
    except Exception:
        return "UGX 0"


def disp(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or str(v) == "nan" or v == "":
        return "—"
    return v


def first_name(value):
    """Display exactly one given name per person while retaining full names internally.
    Special-cased so 'Uncle Joe' stays whole rather than showing just 'Uncle'."""
    shown = disp(value)
    if shown == "—":
        return shown
    people = [person.strip() for person in str(shown).replace(";", ",").split(",") if person.strip()]
    cleaned = []
    for person in people:
        clean_person = person.replace("(", " ").replace(")", " ").strip()
        if clean_person.lower().startswith("uncle "):
            token = " ".join(clean_person.split()[:2])
        else:
            token = clean_person.split()[0] if clean_person else person
        cleaned.append(token)
    return ", ".join(cleaned)


STAFF_DISPLAY_COLUMNS = {
    "baker_assigned", "piler_assigned", "coverer_assigned",
    "decorator_assigned", "driver_assigned", "assigned_baker",
    "topper_assigned_to", "baker", "performed_by", "created_by",
    "requested_by", "issued_by", "received_by", "checked_by",
    "acknowledged_by", "responsible_person", "contributor_name",
}


def staff_display_frame(df):
    shown = df.copy()
    for column in STAFF_DISPLAY_COLUMNS.intersection(shown.columns):
        shown[column] = shown[column].apply(first_name)
    return shown


def col(df, name):
    return df[name] if name in df.columns else pd.Series([""] * len(df), index=df.index)


def filter_orders(df, statuses):
    if df.empty or "workflow_status" not in df.columns:
        return df.iloc[0:0]
    return df[df["workflow_status"].isin(statuses)].copy()


def order_label(row):
    window = ""
    if disp(row.get("delivery_window_start")) != "—" and disp(row.get("delivery_window_end")) != "—":
        window = f" · Window {row.get('delivery_window_start')}–{row.get('delivery_window_end')}"
    return f"{row.get('order_id')} · {row.get('customer_name')} · Due {disp(row.get('due_date'))}{window}"


def select_order(df, key, label="Select an order"):
    if df.empty:
        st.success("Nothing in this queue right now.")
        return None
    d = df.copy()
    d["_label"] = d.apply(order_label, axis=1)
    choice = st.selectbox(label, d["_label"].tolist(), key=key)
    return d[d["_label"] == choice].iloc[0]


def order_card(row, extra=None, show_image=True):
    show_due_alert(row)
    ptype = row.get("product_type") or "Cake"
    is_short_pipeline = ptype in SHORT_PIPELINE_PRODUCTS
    badge_text, badge_colour = PRODUCT_BADGE.get(ptype, PRODUCT_BADGE["Cake"])
    product_badge = f"<span style='background:{badge_colour};color:#fff;padding:2px 10px;border-radius:20px;font-size:.75rem;font-weight:700;'>{badge_text}</span>"
    size_mode = PRODUCT_SIZE_MODE.get(ptype, "inches")
    size = ""
    if size_mode == "inches" and (disp(row.get("cake_size_value")) != "—" or disp(row.get("cake_shape")) != "—"):
        val = disp(row.get("cake_size_value"))
        shape = disp(row.get("cake_shape"))
        height_val = disp(row.get("cake_height_inches"))
        height_txt = f" × {height_val}'' high" if height_val not in ("—", "0", "0.0") else ""
        size = f"<b>Size:</b> {val}'' {shape}{height_txt}<br>" if ptype == "Cake" else f"<b>Size:</b> {val}''{height_txt}<br>"
    elif size_mode in ("category_small_med_big", "category_small_med_large") and disp(row.get("size_category")) != "—":
        size = f"<b>Size:</b> {disp(row.get('size_category'))}<br>"
    elif size_mode == "dozens" and disp(row.get("dozens_quantity")) != "—":
        pieces = row.get("dozens_quantity")
        dozens_txt = f"{float(pieces)/12:g} dozen" if pieces else "—"
        size = f"<b>Quantity:</b> {disp(pieces)} pieces ({dozens_txt})<br>"
    if row.get("sold_from_inventory") == "Yes":
        size += "<b>🍪 Sold from shelf inventory</b><br>"
    is_urgent = str(row.get("urgency_level")) == "Urgent"
    urgency = disp(row.get("urgency_level"))
    urgency_html = f"<b>Urgency:</b> {'🚨 ' if urgency=='Urgent' else ''}{urgency}<br>" if urgency != "—" else ""
    qty = disp(row.get("order_quantity"))
    unit_word = "item(s)" if is_short_pipeline else "cake(s)"
    qty_html = ""
    if size_mode != "dozens" and qty not in ("—", "1"):
        qty_html = f"<b>Quantity:</b> {qty} {unit_word}{' (BULK)' if str(row.get('is_bulk_order'))=='Yes' else ''}<br>"
    window_html = ""
    if disp(row.get("delivery_window_start")) != "—" and disp(row.get("delivery_window_end")) != "—":
        window_html = f"<b>Delivery Window:</b> {row.get('delivery_window_start')} – {row.get('delivery_window_end')}<br>"
    card_style = "class='ca-card'" if not is_urgent else "class='ca-card' style='border:3px solid #C81E1E;background:#FBEAEA;'"
    html = [f"<div {card_style}>"]
    if is_urgent:
        html.append("<div style='background:#C81E1E;color:#fff;font-weight:900;padding:4px 10px;border-radius:6px;display:inline-block;margin-bottom:8px;'>🚨 URGENT — HANDLE WITH PRIORITY UNTIL DELIVERED</div><br>")
    html.append(f"{product_badge}<br><br><b style='font-size:1.1rem'>{disp(row.get('customer_name'))}</b> · {disp(row.get('order_id'))}<br><br>")
    html.append(urgency_html)
    if disp(row.get("cake_category")) not in ("—", "N/A"):
        html.append(f"<b>Occasion:</b> {disp(row.get('cake_category'))}<br>")
    html.append(qty_html)
    if str(row.get("is_multi_tier")) == "Yes":
        html.append(f"<b>Centerpiece Flavours:</b> {disp(row.get('flavours'))} <i>— see Tier Breakdown below for each tier's own flavours</i><br>{size}")
    else:
        html.append(f"<b>Flavours:</b> {disp(row.get('flavours'))}<br>{size}")
    if ptype == "Cake":
        html.append(f"<b>Layers:</b> {disp(row.get('number_of_layers'))}<br>")
    html.append(f"<b>Design:</b> {disp(row.get('design_description'))}<br>")
    if ptype == "Cake" and disp(row.get("icing_type")) != "—":
        html.append(f"<b>🎂 Icing / Covering:</b> {disp(row.get('icing_type'))}<br>")
    if disp(row.get("piler_assigned")) != "—":
        html.append(f"<b>Piler:</b> {disp(row.get('piler_assigned'))}<br>")
    if disp(row.get("flavour_preference_note")) != "—":
        html.append(f"<b>🍰 Flavour Preference Note:</b> {disp(row.get('flavour_preference_note'))}<br>")
    if disp(row.get("centerpiece_team_assigned")) != "—" or disp(row.get("side_cake_team_assigned")) != "—":
        html.append(f"<b>💍 Centerpiece Team:</b> {disp(row.get('centerpiece_team_assigned'))}<br>")
        html.append(f"<b>💍 Side Cake Team:</b> {disp(row.get('side_cake_team_assigned'))}<br>")
    due_weekday = ""
    try:
        due_weekday = f" ({datetime.strptime(str(row.get('due_date')), '%Y-%m-%d').strftime('%A')})" if disp(row.get("due_date")) != "—" else ""
    except Exception:
        pass
    html.append(f"<b>Due:</b> {disp(row.get('due_date'))}{due_weekday} | <b>Time:</b> {disp(row.get('expected_time'))}<br>")
    delivery_date_val = row.get("delivery_date")
    if delivery_date_val and disp(delivery_date_val) != "—" and str(delivery_date_val) != str(row.get("due_date")):
        try:
            delivery_weekday = datetime.strptime(str(delivery_date_val), '%Y-%m-%d').strftime('%A')
            html.append(f"<b>🚚 Delivery Date:</b> {disp(delivery_date_val)} ({delivery_weekday}) <i>— different from due date above</i><br>")
        except Exception:
            html.append(f"<b>🚚 Delivery Date:</b> {disp(delivery_date_val)}<br>")
    html.append(window_html)
    html.append(f"<b>Location:</b> {disp(row.get('location'))}<br>")
    if extra:
        for label, value in extra:
            html.append(f"<b>{label}:</b> {disp(value)}<br>")
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)

    if str(row.get("is_multi_tier")) == "Yes" and row.get("tier_details_json"):
        try:
            tiers = json.loads(row["tier_details_json"])
            if tiers:
                st.markdown("**🎂 Tier Breakdown**")
                tier_df = pd.DataFrame(tiers)
                tier_df.columns = [c.title() for c in tier_df.columns]
                if not show_image and "Price" in tier_df.columns:
                    tier_df = tier_df.drop(columns=["Price"])
                st.dataframe(tier_df, hide_index=True, width='stretch')
        except Exception:
            pass
    if row.get("side_cake_details_json"):
        try:
            side_cakes = json.loads(row["side_cake_details_json"])
            if side_cakes:
                st.markdown("**🍰 Side Cake Breakdown**")
                side_df = pd.DataFrame(side_cakes)
                side_df.columns = [c.replace("_", " ").title() for c in side_df.columns]
                if not show_image and "Price" in side_df.columns:
                    side_df = side_df.drop(columns=["Price"])
                st.dataframe(side_df, hide_index=True, width='stretch')
        except Exception:
            pass

    if not show_image:
        return
    render_reference_images(row)

    order_id_for_videos = row.get("order_id") if hasattr(row, "get") else getattr(row, "order_id", None)
    if order_id_for_videos:
        with connect() as _vconn:
            video_count = _vconn.execute("SELECT COUNT(*) FROM order_videos WHERE order_id=?", (order_id_for_videos,)).fetchone()[0]
        if video_count:
            st.markdown(f"**🎥 Customer Reference Video(s)** — {video_count} uploaded")
            if st.toggle("Load video preview(s)", value=False, key=f"load_videos_{order_id_for_videos}"):
                with connect() as _vconn:
                    _vconn.row_factory = sqlite3.Row
                    vid_rows = _vconn.execute(
                        "SELECT filename, mime_type, data_base64, file_path FROM order_videos WHERE order_id=? ORDER BY id", (order_id_for_videos,)
                    ).fetchall()
                for vr in vid_rows:
                    try:
                        saved_path = str(vr["file_path"] or "") if "file_path" in vr.keys() else ""
                        if saved_path and Path(saved_path).exists():
                            st.video(saved_path, format=vr["mime_type"] or "video/mp4")
                        elif vr["data_base64"]:
                            # Backward compatibility with older orders stored in SQLite.
                            st.video(base64.b64decode(vr["data_base64"]), format=vr["mime_type"] or "video/mp4")
                        else:
                            st.caption(f"Video file is no longer available: {vr['filename']}")
                    except Exception:
                        st.caption(f"Couldn't preview {vr['filename']} — the phone/browser may not support this codec.")

    if order_id_for_videos:
        try:
            render_order_comments(order_id_for_videos, key_suffix="card")
        except Exception as _e:
            print(f"[COMMENTS] card thread failed: {_e}", flush=True)






def _prepare_uploaded_reference_image(uploaded_file, max_dimension=1600, jpeg_quality=82):
    """Return (bytes, extension, mime) optimized for phone/web display.

    iPhone photos can be very large and may arrive as HEIC/HEIF. If Pillow can open
    the image, normalize orientation and convert it to a compact JPEG (PNG stays PNG
    only when transparency matters). This dramatically reduces both DB size and the
    amount Streamlit sends back through the websocket when an order is displayed.
    """
    raw = bytes(uploaded_file.getbuffer())
    original_name = str(getattr(uploaded_file, "name", "image.jpg"))
    suffix = Path(original_name).suffix.lower()
    content_type = str(getattr(uploaded_file, "type", "") or "").lower()

    if PIL_AVAILABLE:
        try:
            import io
            with Image.open(io.BytesIO(raw)) as im:
                im = ImageOps.exif_transpose(im)
                im.thumbnail((max_dimension, max_dimension))
                has_alpha = "A" in im.getbands()
                out = io.BytesIO()
                if has_alpha and suffix == ".png":
                    im.save(out, format="PNG", optimize=True)
                    return out.getvalue(), ".png", "image/png"
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                elif im.mode == "L":
                    im = im.convert("RGB")
                im.save(out, format="JPEG", quality=jpeg_quality, optimize=True, progressive=True)
                return out.getvalue(), ".jpg", "image/jpeg"
        except Exception:
            # If HEIC support is unavailable, leave the original bytes intact rather
            # than rejecting the customer's photo. Safari can still handle many HEICs.
            pass

    if suffix in (".png",):
        return raw, ".png", "image/png"
    if suffix in (".heic", ".heif") or "heic" in content_type or "heif" in content_type:
        return raw, suffix if suffix in (".heic", ".heif") else ".heic", content_type or "image/heic"
    return raw, suffix if suffix in (".jpg", ".jpeg") else ".jpg", content_type or "image/jpeg"



def _prepare_uploaded_reference_video(uploaded_file, order_id, index):
    """Save a phone video using the same reliable byte-buffer pattern as iPhone photos.

    Safari/iOS can expose videos with unusual MIME values or even without a useful file
    extension. We therefore do not depend on the browser's extension filter. We read the
    bytes exactly as the working photo uploader does, infer a safe suffix from the MIME
    type when needed, then write the native file to disk. Conversion, if available, stays
    asynchronous after the order is committed.
    """
    original_name = str(getattr(uploaded_file, "name", "") or "video")
    content_type = str(getattr(uploaded_file, "type", "") or "").lower().strip()
    suffix = Path(original_name).suffix.lower()

    mime_to_suffix = {
        "video/quicktime": ".mov",
        "video/mp4": ".mp4",
        "video/x-m4v": ".m4v",
        "video/webm": ".webm",
        "video/3gpp": ".3gp",
        "video/hevc": ".hevc",
        "video/h265": ".hevc",
    }
    allowed_suffixes = {".mp4", ".mov", ".m4v", ".webm", ".3gp", ".hevc", ".h265"}
    if suffix not in allowed_suffixes:
        suffix = mime_to_suffix.get(content_type, ".mov" if "quicktime" in content_type else ".mp4")

    # Same pattern as _prepare_uploaded_reference_image(): read the UploadedFile buffer
    # into bytes first. This avoids Safari/UploadedFile stream-position inconsistencies.
    raw = bytes(uploaded_file.getbuffer())
    if not raw:
        raise ValueError("The selected video is empty. Please select it again from Photos.")

    raw_path = REFERENCE_VIDEO_DIR / f"{order_id}_{index}_original{suffix}"
    raw_path.write_bytes(raw)

    mime = {
        ".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/x-m4v",
        ".webm": "video/webm", ".3gp": "video/3gpp", ".hevc": "video/mp4", ".h265": "video/mp4",
    }.get(suffix, content_type or "video/mp4")
    return str(raw_path), mime, len(raw)


def _optimize_order_video_async(video_id, source_path, source_mime=""):
    """Convert iPhone MOV/M4V to broadly playable MP4 without delaying order entry."""
    try:
        src = Path(source_path)
        if not src.exists():
            return
        suffix = src.suffix.lower()
        if suffix not in (".mov", ".m4v", ".hevc") and "quicktime" not in str(source_mime or "").lower():
            return
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return
        out_path = REFERENCE_VIDEO_DIR / f"video_{int(video_id)}.mp4"
        cmd = [
            ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
            "-vf", "scale=1280:-2:force_original_aspect_ratio=decrease",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out_path),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300)
        if not out_path.exists() or out_path.stat().st_size <= 0:
            return
        with connect() as conn:
            conn.execute("UPDATE order_videos SET file_path=?, mime_type=?, file_size_bytes=? WHERE id=?",
                         (str(out_path), "video/mp4", out_path.stat().st_size, int(video_id)))
            conn.commit()
        try:
            src.unlink()
        except Exception:
            pass
    except Exception as exc:
        print(f"[VIDEO] Background iPhone conversion failed: {exc}", flush=True)

def _web_safe_image_payload(data_uri_or_bytes, max_dimension=1400, jpeg_quality=80):
    """Shrink legacy/full-size images before handing them to Streamlit's frontend."""
    try:
        raw = data_uri_or_bytes
        if isinstance(raw, str) and raw.startswith("data:image"):
            raw = base64.b64decode(raw.split(",", 1)[1])
        elif isinstance(raw, str):
            return raw
        else:
            raw = bytes(raw)
        if PIL_AVAILABLE:
            import io
            with Image.open(io.BytesIO(raw)) as im:
                im = ImageOps.exif_transpose(im)
                im.thumbnail((max_dimension, max_dimension))
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                elif im.mode == "L":
                    im = im.convert("RGB")
                out = io.BytesIO()
                im.save(out, format="JPEG", quality=jpeg_quality, optimize=True, progressive=True)
                return out.getvalue()
        return raw
    except Exception:
        return data_uri_or_bytes


def render_reference_images(row):
    """Show customer reference media without flooding mobile Safari.

    Only the first reference image is rendered by default. Extra images are opt-in,
    and legacy large images are downscaled before they are sent to the browser.
    """
    images_shown = False
    media = {}
    try:
        if not row.get("reference_images_json") and not row.get("reference_image_base64"):
            media = load_order_media(row.get("order_id")) if row.get("order_id") else {}
    except Exception:
        media = {}

    images_json = row.get("reference_images_json") or media.get("reference_images_json")
    if images_json:
        try:
            all_imgs = json.loads(images_json)
            if all_imgs:
                st.markdown(f"**📷 Customer Reference Image(s)** — {len(all_imgs)} uploaded")
                st.image(_web_safe_image_payload(all_imgs[0]), caption="Reference 1", width=420)
                images_shown = True
                if len(all_imgs) > 1:
                    oid = str(row.get("order_id") or "order")
                    show_more = st.toggle(
                        f"Show {len(all_imgs)-1} more reference image(s)",
                        value=False,
                        key=f"show_more_imgs_{oid}",
                    )
                    if show_more:
                        for i, data_uri in enumerate(all_imgs[1:], start=2):
                            st.image(_web_safe_image_payload(data_uri), caption=f"Reference {i}", width=420)
        except Exception:
            pass

    if not images_shown:
        img_b64 = row.get("reference_image_base64") or media.get("reference_image_base64")
        path = row.get("reference_image_path") or media.get("reference_image_path")
        if img_b64 and isinstance(img_b64, str) and img_b64.startswith("data:image"):
            st.markdown("**📷 Customer Reference Image**")
            st.image(_web_safe_image_payload(img_b64), caption="What the customer wants — refer to this at every stage", width=420)
            images_shown = True
        elif path and isinstance(path, str) and Path(path).exists():
            st.markdown("**📷 Customer Reference Image**")
            try:
                st.image(_web_safe_image_payload(Path(path).read_bytes()), caption="What the customer wants — refer to this at every stage", width=420)
            except Exception:
                st.image(path, caption="What the customer wants — refer to this at every stage", width=420)
            images_shown = True
    return images_shown

def table(df, columns):
    if df.empty:
        st.info("No records to show.")
        return
    cols = [c for c in columns if c in df.columns]
    st.dataframe(staff_display_frame(df[cols]), hide_index=True, width='stretch')


def render_queue_table(df_subset, title="Jobs In Queue", extra_columns=None, base_cols_override=None):
    """Show every job currently sitting at this stage, not just the one selected below,
    so staff can see the full workload instead of assuming there is only one job.
    Urgent orders are pinned to the top and clearly marked — Streamlit's tables can't
    show actual red cell backgrounds (they're canvas-rendered, not real HTML), so this
    is the reliable way to make urgency impossible to miss."""
    base_cols = base_cols_override or ["#", "🚨", "order_id", "product_type", "customer_name", "order_type", "urgency_level", "priority",
                 "due_date", "expected_time", "workflow_status"]
    cols = base_cols + (extra_columns or [])
    urgent_count = int((df_subset["urgency_level"] == "Urgent").sum()) if df_subset is not None and not df_subset.empty and "urgency_level" in df_subset.columns else 0
    title_suffix = f" — 🚨 {urgent_count} URGENT" if urgent_count > 0 else ""
    st.markdown(f"#### 📋 {title} — {len(df_subset)} job(s) waiting{title_suffix}")
    if df_subset is None or df_subset.empty:
        st.caption("Nothing waiting here right now — you're all caught up.")
        return
    ordered = df_subset.copy()
    ordered["_is_urgent"] = (ordered["urgency_level"] == "Urgent") if "urgency_level" in ordered.columns else False
    sort_cols = ["_is_urgent"]
    sort_asc = [False]
    if "due_date" in ordered.columns:
        sort_cols.append("due_date"); sort_asc.append(True)
    if "expected_time" in ordered.columns:
        sort_cols.append("expected_time"); sort_asc.append(True)
    ordered = ordered.sort_values(sort_cols, ascending=sort_asc)
    if "flavour_combination" in cols and "flavours" in ordered.columns:
        ordered["flavour_combination"] = ordered["flavours"].apply(lambda f: f"({f})" if f and str(f).strip() and str(f) != "nan" else "—")
    ordered.insert(0, "#", range(1, len(ordered) + 1))
    ordered.insert(1, "🚨", ordered["_is_urgent"].map(lambda u: "🚨 URGENT" if u else ""))
    table(ordered, cols)


DEPARTMENT_STAGE_STATUSES = {
    "Baking": ["Production Planned", "Baking", "Baking Correction Required"],
    "Filling / Piling": ["Piling Incoming", "Piling", "Piling Correction Required"],
    "Coating / Covering": ["Covering Incoming", "Covering", "Covering Correction Required"],
    "Decoration": ["Decorating Incoming", "Decorating", "Decoration Correction Required"],
    "Studio / Final QC": ["Studio Check"],
    "Packaging": ["Ready for Packaging", "Packaging"],
    "Design & Innovation": [],  # handled separately via topper_required flag
}

DEPARTMENT_STAFF_COLUMN = {
    "Baking": "baker_assigned",
    "Filling / Piling": "piler_assigned",
    "Coating / Covering": "coverer_assigned",
    "Decoration": "decorator_assigned",
    "Studio / Final QC": None,
    "Packaging": None,
}


def can_act_on(row, staff_column):
    """Everyone in the department can VIEW any job, but only a person actually assigned
    to it can act on it — or a Head of Department, or anyone if nobody's assigned yet.
    Assignment can now be more than one person (comma-separated), for bulk orders that
    need multiple hands — being any one of the assigned people is enough to act."""
    if st.session_state.get("is_hod"):
        return True
    assigned_raw = str(row.get(staff_column) or "").strip()
    if not assigned_raw or assigned_raw == "—":
        return True
    assigned_list = [a.strip() for a in assigned_raw.split(",") if a.strip()]
    me = st.session_state.get("staff_name", "").strip()
    return me in assigned_list


def render_multi_assign(row, staff_column, role_label, staff_options, widget_key):
    """Only a Head of Department can change who's assigned to a job — everyone else can see
    the assignment but not touch it. If the HOD is handing off their own personal share of the
    work to someone else, that specific change needs Production Planning's confirmation before
    it takes effect, rather than happening immediately."""
    current = [s.strip() for s in str(row.get(staff_column) or "").split(",") if s.strip()]
    st.markdown(f"##### 👥 {role_label}(s) Assigned to This Job")
    if not st.session_state.get("is_hod"):
        st.caption(f"Currently assigned: **{', '.join(current) if current else 'nobody yet'}**. "
                   f"Only your Head of Department can change work assignments.")
        return
    st.caption("As HOD, you can add or remove names below. If you remove yourself from work you're personally on, "
               "that change needs Production Planning to confirm before it takes effect.")
    my_name = st.session_state.get("staff_name", "").strip()
    updated = st.multiselect(f"{role_label}(s) on this job", staff_options, default=[c for c in current if c in staff_options], format_func=first_name, key=f"multiassign_{widget_key}")
    if st.button(f"Update {role_label} Assignment", key=f"multiassign_btn_{widget_key}"):
        removed = set(current) - set(updated)
        added = set(updated) - set(current)
        hod_stepping_down = my_name in current and my_name in removed
        by = my_name or "HOD"
        if hod_stepping_down:
            joined_proposed = ", ".join(updated) if updated else ""
            with connect() as conn:
                conn.execute("""INSERT INTO reassignment_requests(order_id, stage, role_label, staff_column, current_value,
                                proposed_value, requested_by, reason, status, requested_at)
                                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                             (row.order_id, role_label, role_label, staff_column, ", ".join(current), joined_proposed,
                              by, "HOD handing off personal work assignment", "Pending", now_iso()))
                conn.commit()
            create_notification(row.order_id, "Production Planning", None,
                                 f"👑 {by} (HOD, {role_label}) wants to hand off their own work on {row.order_id} to "
                                 f"{joined_proposed or 'nobody'} — needs your confirmation before it takes effect.")
            st.warning("Since this removes you from work you're personally assigned to, it's been sent to Production Planning for confirmation — it won't take effect until they approve it.")
        else:
            joined = ", ".join(updated) if updated else ""
            update_order(row.order_id, {staff_column: joined}, by, f"{role_label} Assignment Updated", role_label)
            for person in added:
                create_notification(row.order_id, row.get("current_owner") or role_label, person,
                                     f"You've been assigned to {row.order_id} ({disp(row.get('customer_name'))}) as {role_label} by your HOD.")
            st.success("Assignment updated." + (f" Notified: {', '.join(added)}." if added else ""))
            st.rerun()


def render_hod_overview(department_name, df):
    """Head-of-Department view: every job currently in this department's stages,
    with who is assigned to each, so a HOD can follow up on delays."""
    if not st.session_state.get("is_hod"):
        return
    st.markdown("### 👑 Head of Department — All Jobs In This Department")
    st.caption("Visible only to Heads of Department. Shows every job currently in your department, and who is working on it.")
    statuses = DEPARTMENT_STAGE_STATUSES.get(department_name, [])
    staff_col = DEPARTMENT_STAFF_COLUMN.get(department_name)
    if not statuses or df.empty:
        st.info("No jobs currently in this department.")
        return
    subset = df[df["workflow_status"].isin(statuses)].copy() if "workflow_status" in df.columns else df.iloc[0:0]
    cols = ["order_id", "customer_name", "order_type", "urgency_level", "priority", "due_date",
            "expected_time", "workflow_status", "next_action"]
    if staff_col and staff_col in subset.columns:
        cols.insert(1, staff_col)
    table(subset, cols)
    with st.expander("Not sure who's dealing with a delayed job? Check here"):
        st.caption("Jobs above with a blank/'—' staff column have not been picked up yet — that's usually the delay.")


def escalate_urgency_on_rejection(order_id, by):
    """Core rule: once a cake is rejected for poor work, its level shifts from Normal to Urgent."""
    update_order(order_id, {
        "urgency_level": "Urgent",
        "priority": "Critical",
        "order_type": "Urgent / Abrupt Order",
    }, by, "Urgency Escalated", "Quality Rejection")


def kpi(label, value, note=""):
    st.markdown(f"<div class='ca-kpi'><div class='label'>{label}</div><div class='value'>{value}</div><div class='note'>{note}</div></div>", unsafe_allow_html=True)


def get_staff_names(department: str, fallback: list):
    """Active staff whose account covers this department, by full name. Falls back to the
    placeholder list only if no real accounts exist yet for this department."""
    with connect() as conn:
        rows = conn.execute("SELECT full_name, departments FROM staff_accounts WHERE is_active='Yes'").fetchall()
    names = sorted({
        r["full_name"] for r in rows
        if r["departments"] and department in [d.strip() for d in r["departments"].split(",")]
    })
    return names if names else fallback


def staff_lists():
    return (
        get_staff_names("Baking", FALLBACK_BAKERS),
        get_staff_names("Filling / Piling", FALLBACK_PILERS),
        get_staff_names("Coating / Covering", FALLBACK_COVERERS),
        get_staff_names("Decoration", FALLBACK_DECORATORS),
        get_staff_names("Dispatch / Driver", FALLBACK_DRIVERS),
    )

def staff_lists_fast():
    """Load all assignment rosters with one tiny DB query instead of five connections."""
    with connect() as conn:
        rows = conn.execute("SELECT full_name, departments FROM staff_accounts WHERE is_active='Yes'").fetchall()
    def names_for(dept, fallback):
        names = sorted({
            r["full_name"] for r in rows
            if r["departments"] and dept in [d.strip() for d in str(r["departments"]).split(",")]
        })
        return names if names else list(fallback)
    return (
        names_for("Baking", FALLBACK_BAKERS),
        names_for("Filling / Piling", FALLBACK_PILERS),
        names_for("Coating / Covering", FALLBACK_COVERERS),
        names_for("Decoration", FALLBACK_DECORATORS),
        names_for("Dispatch / Driver", FALLBACK_DRIVERS),
    )


def staff_workload_counts_from_df(df, staff_column):
    """Fast per-rerun workload count using the already-loaded lightweight orders dataframe."""
    if df.empty or staff_column not in df.columns:
        return {}
    active = df[df["workflow_status"].isin(ACTIVE_ASSIGNMENT_STATUSES)]
    counts = {}
    for value in active[staff_column].fillna("").astype(str):
        for name in [n.strip() for n in value.replace(";", ",").split(",") if n.strip()]:
            counts[name] = counts.get(name, 0) + 1
    return counts


ACTIVE_ASSIGNMENT_STATUSES = [
    "Deposit Confirmed", "Production Planned", "Baking", "Baking Correction Required",
    "Piling Incoming", "Piling", "Piling Correction Required",
    "Covering Incoming", "Covering", "Covering Correction Required",
    "Decorating Incoming", "Decorating", "Decoration Correction Required", "Studio Check",
]


def staff_workload_counts(staff_column):
    """Counts how many currently-active orders each person already has assigned to them
    on a given role column, so whoever's assigning work can see who's free and who's
    already stretched thin. Only counts orders still genuinely in progress - finished
    or cancelled work doesn't count against anyone."""
    df = load_orders()
    if df.empty or staff_column not in df.columns:
        return {}
    active = df[df["workflow_status"].isin(ACTIVE_ASSIGNMENT_STATUSES)]
    counts = {}
    for _, row in active.iterrows():
        names = str(row.get(staff_column) or "")
        for name in [n.strip() for n in names.split(",") if n.strip()]:
            counts[name] = counts.get(name, 0) + 1
    return counts


def format_name_with_workload(name, counts):
    n = counts.get(name, 0)
    label = first_name(name)
    if n == 0:
        return f"{label} (free)"
    return f"{label} ({n} active)"


# -----------------------------
# Pages
# -----------------------------

def render_customer_profile_lookup(df):
    with st.expander("🔍 Customer Profile & History — search past orders, spend, and complaints"):
        search = st.text_input("Search by customer name or phone number", key="cust_search")
        if not search.strip():
            st.caption("Type a name or phone number above to look up a customer's full history.")
            return
        s = search.strip().lower()
        if df.empty:
            st.info("No orders in the system yet.")
            return
        matches = df[
            df["customer_name"].astype(str).str.lower().str.contains(s, na=False)
            | df["customer_number"].astype(str).str.contains(s, na=False)
        ]
        if matches.empty:
            st.info("No orders found for that name or number.")
            return

        distinct = matches[["customer_name", "customer_number"]].drop_duplicates()
        if len(distinct) > 1:
            st.caption(f"{len(distinct)} different customers match this search — showing combined results below. "
                       f"Search the full phone number to narrow it down to one person.")

        total_orders = len(matches)
        total_spend = float(pd.to_numeric(matches.get("price_ugx"), errors="coerce").fillna(0).sum())
        total_balance_due = float(pd.to_numeric(matches.get("balance"), errors="coerce").fillna(0).sum())
        last_order_at = matches["order_created_at"].max() if "order_created_at" in matches.columns else None

        comp = load_table("complaints")
        cust_complaints = comp[comp["customer_name"].isin(matches["customer_name"].unique())] if not comp.empty else comp.iloc[0:0]

        c1, c2, c3, c4 = st.columns(4)
        with c1: kpi("Total Orders", total_orders)
        with c2: kpi("Total Spend", fmt_ugx(total_spend))
        with c3: kpi("Balance Outstanding", fmt_ugx(total_balance_due))
        with c4: kpi("Complaints Filed", len(cust_complaints))

        if total_orders >= 2:
            st.success(f"🔁 Repeat customer — {total_orders} orders on file.")
        else:
            st.caption("First-time customer — only 1 order on file.")
        if last_order_at and str(last_order_at) != "nan":
            st.caption(f"Most recent order placed: {last_order_at}")

        st.markdown("#### Order History")
        table(matches.sort_values("order_created_at", ascending=False),
              ["order_id", "product_type", "order_type", "urgency_level", "order_quantity", "price_ugx", "balance",
               "payment_arrangement", "workflow_status", "order_created_at"])

        st.markdown("#### Complaint History")
        if cust_complaints.empty:
            st.caption("No complaints on file for this customer.")
        else:
            table(cust_complaints, ["complaint_id", "order_id", "complaint_category", "severity",
                  "complaint_status", "opened_at", "resolved_at"])


def render_customer_care():
    page_header("📋 Customer Care", "Create normal or urgent orders with flexible payment arrangements and guided layer planning.")
    df = load_orders()
    render_due_alert_board(df)
    render_followup_alert_board(df)

    c1, c2, c3, c4 = st.columns(4)
    with c1: kpi("Total Orders", f"{len(df):,}")
    with c2: kpi("Urgent Orders", int((col(df,"order_type") == "Urgent / Abrupt Order").sum()))
    with c3: kpi("In Production", int(col(df,"workflow_status").isin(["Production Planned","Baking","Piling","Covering","Decorating"]).sum()))
    with c4:
        comp = load_table("complaints")
        kpi("Complaints Open", int((comp["complaint_status"] != "Closed").sum()) if not comp.empty else 0)

    render_customer_profile_lookup(df)
    render_followup_complaints_section(df)
    # Rendering the complete historical image gallery on every Customer Care rerun was one
    # of the biggest sources of slowness. Streamlit still executes collapsed expanders, so
    # the gallery must be an explicit opt-in load.
    if st.toggle("🖼️ Load All Orders Image Gallery", value=False, key="cc_load_gallery"):
        render_order_gallery(df, "🖼️ All Orders — Images & Copyable Details")

    if st.session_state.get("is_hod"):
        with st.expander("👑 HOD: Correct a Wrongly Entered Order"):
            st.caption("For mistakes made at order entry — wrong name, phone, flavour, price, size, date, etc. This edits the order in place; it never has to move through the workflow again.")
            cc_search = st.text_input("Search by Order ID or customer name", key="cc_hod_search")
            if cc_search.strip():
                s = cc_search.strip().lower()
                cc_matches = df[
                    df["order_id"].astype(str).str.lower().str.contains(s, na=False) |
                    df["customer_name"].astype(str).str.lower().str.contains(s, na=False)
                ] if not df.empty else df
                if cc_matches.empty:
                    st.info("No matching orders found.")
                else:
                    table(cc_matches, ["order_id", "customer_name", "customer_number", "flavours", "price_ugx",
                                        "cake_size_value", "cake_height_inches", "due_date", "workflow_status"])
                    cc_pick = st.selectbox("Select an order to correct", cc_matches["order_id"].tolist(), key="cc_hod_pick")
                    cc_row = cc_matches[cc_matches["order_id"] == cc_pick].iloc[0]
                    a, b = st.columns(2)
                    cc_name = a.text_input("Customer name", value=disp(cc_row.get("customer_name")) if disp(cc_row.get("customer_name")) != "—" else "", key="cc_hod_name")
                    cc_phone = b.text_input("Customer phone", value=disp(cc_row.get("customer_number")) if disp(cc_row.get("customer_number")) != "—" else "", key="cc_hod_phone")
                    a, b = st.columns(2)
                    cc_flavours = a.text_input("Flavours", value=disp(cc_row.get("flavours")) if disp(cc_row.get("flavours")) != "—" else "", key="cc_hod_flavours")
                    cc_price = b.number_input("Price (UGX)", min_value=0.0, step=5000.0, value=float(cc_row.get("price_ugx") or 0), key="cc_hod_price")
                    st.caption("⚠️ Size, height, and shape below are what Production actually plans against — if the client changed the cake size, these must be corrected here too, not just the price.")
                    a, b, c = st.columns(3)
                    cc_size = a.number_input("Size (inches)", min_value=0.0, step=0.5, value=float(cc_row.get("cake_size_value") or 0), key="cc_hod_size")
                    cc_height = b.number_input("Height (inches)", min_value=0.0, step=0.5, value=float(cc_row.get("cake_height_inches") or 0), key="cc_hod_height")
                    cc_shape = c.selectbox("Shape", ["Round", "Rectangle", "Square", "Heart", "Custom"],
                                            index=(["Round", "Rectangle", "Square", "Heart", "Custom"].index(cc_row.get("cake_shape"))
                                                   if cc_row.get("cake_shape") in ["Round", "Rectangle", "Square", "Heart", "Custom"] else 0),
                                            key="cc_hod_shape")
                    a, b = st.columns(2)
                    cc_location = a.text_input("Delivery / pickup location", value=disp(cc_row.get("location")) if disp(cc_row.get("location")) != "—" else "", key="cc_hod_location")
                    cc_due_date = b.text_input("Due date (YYYY-MM-DD)", value=disp(cc_row.get("due_date")) if disp(cc_row.get("due_date")) != "—" else "", key="cc_hod_due_date")
                    cc_design = st.text_area("Design description / notes", value=disp(cc_row.get("design_description")) if disp(cc_row.get("design_description")) != "—" else "", key="cc_hod_design")
                    cc_by = st.text_input("Corrected by", value=st.session_state.get("staff_name", "Customer Care HOD"), key="cc_hod_by")
                    if st.button("💾 Save Correction", key="cc_hod_save", width='stretch'):
                        update_order(cc_row["order_id"], {
                            "customer_name": cc_name.strip(), "customer_number": cc_phone.strip(),
                            "flavours": cc_flavours.strip(), "price_ugx": cc_price,
                            "cake_size_value": cc_size, "cake_height_inches": cc_height, "cake_shape": cc_shape,
                            "location": cc_location.strip(), "due_date": cc_due_date.strip(),
                            "design_description": cc_design.strip(),
                        }, cc_by, "Manual Entry Correction by Customer Care HOD", "Customer Care")
                        create_notification(cc_row["order_id"], cc_row.get("current_owner") or "Production Planning", None,
                                             f"✏️ {cc_row['order_id']} ({cc_name.strip()}) was corrected by Customer Care — "
                                             f"please re-check size/height/flavours/price before continuing this job.")
                        st.success(f"Order {cc_row['order_id']} corrected — the department currently holding this job has been notified to double-check it."); st.rerun()
            else:
                st.caption("Type something above to search.")

        st.markdown("#### 👑 HOD: Move a Cake to a Different Department or Person")
        st.caption("If a cake needs to move somewhere it isn't currently sitting — for example from Baking straight to "
                   "Piling — or needs reassigning to a different person, do that here.")
        render_order_lookup_and_fix(df)

    st.markdown("### Product Line")
    product_type = st.selectbox("What is this order for?", PRODUCT_TYPES, key="nc_product_type",
                                 help="Cookies, Cake Loaves, Cake Layers, and Cupcakes skip Piling/Covering/Decoration/Studio QC and go straight from Baking to Packaging.")

    cake_category = "N/A"
    if product_type == "Cake":
        cake_category = st.selectbox("Cake Category / Occasion", CAKE_CATEGORIES, key="nc_cake_category")

    size_mode = PRODUCT_SIZE_MODE.get(product_type, "inches")
    size_value, shape, cake_format, icing_type = 0.0, "N/A", "Full Cake", "N/A"
    cake_height = 0.0
    size_category = "N/A"

    st.markdown("### Product Details")
    st.markdown("### Customer-selected Flavours")
    st.caption("Choose up to four flavours. Leave unused slots as None.")
    _fc1, _fc2, _fc3, _fc4 = st.columns(4)
    if product_type == "Cookies":
        _flavour_options = ["None"] + COOKIE_FLAVOURS
        st.caption("Cookies come in Coconut or Ginger only.")
    else:
        _flavour_options = ["None"] + [f for f in STANDARD_FLAVOURS if f != "Other"] + ["Other"]
    selected_flavours = []
    for _idx, _col in enumerate([_fc1, _fc2, _fc3, _fc4], start=1):
        _choice = _col.selectbox(f"Flavour {_idx}", _flavour_options, key=f"nc_flavour_slot_{_idx}")
        if _choice == "Other":
            _choice = _col.text_input(f"Specify flavour {_idx}", key=f"nc_flavour_slot_other_{_idx}")
        if _choice and _choice != "None":
            selected_flavours.append(_choice.strip())
    flavours = ", ".join(selected_flavours)
    if size_mode == "inches":
        a, b, c = st.columns(3)
        size_value = a.number_input("Pan/Circle Size (inches)", min_value=0.0, step=0.5, value=8.0, key="nc_size_inches")
        cake_height = b.number_input("Height (inches)", min_value=0.0, step=0.5, value=6.0, key="nc_cake_height")
        if product_type == "Cake":
            shape = c.selectbox("Cake Shape", ["Round", "Rectangle", "Square", "Heart", "Custom"], key="nc_shape")
            icing_type = st.selectbox(
                "Icing / Finish Type *", ["Buttercream", "Whipped Cream", "Fondant", "Other"], key="nc_icing",
                help="Fondant cakes need at least 1 hour of decoration time. Buttercream/Whipped Cream can move faster.")
    elif size_mode == "category_small_med_big":
        size_category = st.selectbox("Size", ["Small", "Medium", "Big"], key="nc_size_cat_cookie")
    elif size_mode == "category_small_med_large":
        size_category = st.selectbox("Size", ["Small", "Medium", "Large"], key="nc_size_cat_loaf")

    is_multi_tier = "No"
    tier_count = 1
    tier_details = []
    tier_total = 0.0
    side_cake_count = 0
    side_cake_details = []
    side_cake_total = 0.0
    if product_type == "Cake":
        st.markdown("### Tier Configuration")
        is_multi_tier = st.selectbox("Is this a multi-tier cake? (e.g. wedding, birthday, baptism, or any other tiered cake)", ["No", "Yes"], key="nc_multi_tier")
        if is_multi_tier == "Yes":
            tier_count = st.selectbox("Number of full tiers", [2, 3, 4, 5, 6], key="nc_tier_count")
            st.caption("Every full tier must contain at least two flavours and may contain up to four. Enter the pan diameter and finished height in inches.")
            for i in range(1, int(tier_count) + 1):
                with st.expander(f"Full Tier {i}", expanded=True):
                    flavour_cols = st.columns(4)
                    tier_flavours = []
                    tier_options = ["None"] + [f for f in STANDARD_FLAVOURS if f != "Other"] + ["Other"]
                    for j, fcol in enumerate(flavour_cols, start=1):
                        default_index = 1 if j <= 2 else 0
                        choice = fcol.selectbox(f"Flavour {j}", tier_options, index=default_index, key=f"nc_tier_{i}_flavour_{j}")
                        if choice == "Other":
                            choice = fcol.text_input(f"Specify flavour {j}", key=f"nc_tier_{i}_flavour_other_{j}")
                        if choice and choice != "None":
                            tier_flavours.append(choice.strip())
                    a, b, c = st.columns(3)
                    t_size_choice = a.selectbox("Pan diameter (inches)", STANDARD_CAKE_SIZES, key=f"nc_tier_{i}_size")
                    t_size = a.number_input("Custom diameter (inches)", min_value=1.0, step=0.5, value=8.0, key=f"nc_tier_{i}_size_other") if t_size_choice == "Custom" else float(t_size_choice)
                    t_height = b.number_input("Finished height (inches)", min_value=1.0, step=0.5, value=6.5, key=f"nc_tier_{i}_height")
                    t_price = c.number_input("Tier price (UGX)", min_value=0, step=5000, key=f"nc_tier_{i}_price")
                    tier_details.append({"tier": i, "flavours": tier_flavours, "pan_diameter_inches": t_size, "height_inches": t_height, "price": t_price})
                    tier_total += t_price
            st.markdown("### Side Cakes")
            side_cake_count = st.selectbox(f"How many side cakes accompany this {cake_category.lower()} cake?", list(range(0, 11)), key="nc_side_cake_count")
            for i in range(1, int(side_cake_count) + 1):
                with st.expander(f"Side Cake {i}", expanded=True):
                    flavour_cols = st.columns(4)
                    side_flavours = []
                    side_options = ["None"] + [f for f in STANDARD_FLAVOURS if f != "Other"] + ["Other"]
                    for j, fcol in enumerate(flavour_cols, start=1):
                        choice = fcol.selectbox(f"Flavour {j}", side_options, key=f"nc_side_{i}_flavour_{j}")
                        if choice == "Other":
                            choice = fcol.text_input(f"Specify flavour {j}", key=f"nc_side_{i}_flavour_other_{j}")
                        if choice and choice != "None":
                            side_flavours.append(choice.strip())
                    a, b, c = st.columns(3)
                    sc_size_choice = a.selectbox("Pan diameter (inches)", STANDARD_CAKE_SIZES, key=f"nc_side_{i}_size")
                    sc_size = a.number_input("Custom diameter (inches)", min_value=1.0, step=0.5, value=8.0, key=f"nc_side_{i}_size_other") if sc_size_choice == "Custom" else float(sc_size_choice)
                    sc_height = b.number_input("Finished height (inches)", min_value=1.0, step=0.5, value=6.5, key=f"nc_side_{i}_height")
                    sc_price = c.number_input("Side-cake price (UGX)", min_value=0, step=5000, key=f"nc_side_{i}_price")
                    side_cake_details.append({"side_cake": i, "flavours": side_flavours, "pan_diameter_inches": sc_size, "height_inches": sc_height, "price": sc_price})
                    side_cake_total += sc_price
            st.info(f"Full tiers: UGX {tier_total:,.0f} · Side cakes: UGX {side_cake_total:,.0f} · {cake_category} cake total: UGX {tier_total + side_cake_total:,.0f}")

    sold_from_inventory = "No"
    inventory_batch_id = None
    if product_type == "Cookies":
        st.markdown("### Sell From Baked Cookie Inventory")
        st.caption("Cookies are usually sold off the shelf from what's already baked. Pick a batch below, or choose to bake fresh for this order.")
        cookie_inv = load_table("baked_cookie_inventory")
        available_cookies = cookie_inv[(cookie_inv["inventory_status"] == "Available") & (pd.to_numeric(cookie_inv["quantity_available"], errors="coerce").fillna(0) > 0)] if not cookie_inv.empty else cookie_inv
        if size_category != "N/A" and not available_cookies.empty:
            available_cookies = available_cookies[available_cookies["size_category"] == size_category]
        table(available_cookies, ["id", "date_baked", "flavour", "size_category", "quantity_available", "baker", "storage_location"])
        sell_choice = st.radio("Fulfill this order from inventory, or bake fresh?",
                                ["Sell from shelf (inventory)", "Bake fresh for this order"], key="nc_cookie_sell")
        if sell_choice.startswith("Sell") and not available_cookies.empty:
            sold_from_inventory = "Yes"
            inventory_batch_id = st.selectbox("Inventory batch", available_cookies["id"].tolist(), key="nc_cookie_batch")
        elif sell_choice.startswith("Sell"):
            st.warning("No matching inventory available for that size — this order will be baked fresh instead.")

    st.markdown("### Order Price")
    dozens_qty = 0
    if is_multi_tier == "Yes":
        st.caption("Price is the sum of all tier prices entered above.")
        price = tier_total + side_cake_total
        total_price = tier_total + side_cake_total
        order_quantity = 1
        is_bulk_order = "No"
        st.info(f"**Total Cost (tiers and side cakes):** UGX {total_price:,.0f}")
    elif product_type == "Cupcakes":
        st.caption("Cupcakes are sold by the dozen — pick a quantity and the price per dozen; the total is calculated automatically.")
        a, b = st.columns(2)
        dozens_choice = a.selectbox("Quantity", DOZEN_OPTIONS, key="nc_dozens_choice")
        if dozens_choice == "Custom":
            dozens_qty = a.number_input("Custom quantity (pieces)", min_value=1, step=1, value=12, key="nc_dozens_custom")
        else:
            dozens_qty = DOZEN_COUNTS[dozens_choice]
        price = b.number_input("Price per Dozen (UGX) *", min_value=0, step=5000, key="nc_price")
        total_price = price * (dozens_qty / 12)
        order_quantity = dozens_qty
        is_bulk_order = "Yes" if dozens_qty > 12 else "No"
        st.info(f"**Total Cost:** {dozens_qty} pieces ({dozens_choice}) × UGX {price:,.0f}/dozen = **UGX {total_price:,.0f}**")
    else:
        is_bulk_order = st.selectbox("Bulk / Corporate Order (multiple identical items, same price)?",
                                      ["No", "Yes"], key="nc_is_bulk")
        if is_bulk_order == "Yes":
            st.caption("Enter the client once, set quantity, and the per-item price is multiplied automatically.")
            a,b = st.columns(2)
            order_quantity = a.number_input("Quantity", min_value=1, step=1, value=1, key="nc_qty")
            price = b.number_input("Price per Item (UGX) *", min_value=0, step=5000, key="nc_price")
            total_price = price * order_quantity
            st.info(f"**Total Cost:** {order_quantity} × UGX {price:,.0f} = **UGX {total_price:,.0f}**")
        else:
            order_quantity = 1
            price = st.number_input("Price (UGX) *", min_value=0, step=5000, key="nc_price")
            total_price = price
    suggested = suggested_layers_for_price(price) if product_type == "Cake" else 0

    st.markdown("### Due Date & Delivery")
    a,b = st.columns(2)
    due_date = a.date_input("Due Date (cake ready by)", help="When the cake itself needs to be finished — the baking/production timeline.", key="nc_due_date")
    expected_time = b.time_input("Expected Time", value=dtime(12,0), key="nc_expected_time")
    st.caption(f"📅 {due_date.strftime('%A')}" if due_date else "")

    st.caption("The actual delivery day can be different from when the cake is finished — e.g. cake ready today, delivered tomorrow.")
    a,b = st.columns(2)
    delivery_date = a.date_input("Delivery Date", value=due_date, help="The day the cake actually goes out — may be the same as the due date, or later.", key="nc_delivery_date")
    st.caption(f"📅 {delivery_date.strftime('%A')}" if delivery_date else "")

    st.markdown("### Customer Details")
    st.caption("Search for a returning customer to pick their name and number automatically — cuts down on typos and duplicate entries. Pick \"+ New Customer\" if they're not in here yet.")
    past_customers = df
    customer_options = ["+ New Customer"]
    customer_lookup = {}
    if not past_customers.empty and "customer_name" in past_customers.columns:
        seen_pairs = set()
        for _, prow in past_customers.iterrows():
            cname = disp(prow.get("customer_name"))
            cphone = disp(prow.get("customer_number"))
            if cname != "—" and (cname, cphone) not in seen_pairs:
                seen_pairs.add((cname, cphone))
                label = f"{cname} — {cphone}" if cphone != "—" else cname
                customer_options.append(label)
                customer_lookup[label] = (cname, cphone if cphone != "—" else "")
    # Returning-customer autofill needs to update the actual widget state, not only the
    # `value=` argument. Streamlit keeps a widget's existing session value once its key has
    # been created, so the old implementation visibly changed the dropdown but left the name
    # and phone boxes unchanged. These stable widget keys plus an on_change callback make the
    # selected customer's details appear immediately.
    gen = st.session_state.get("nc_customer_field_gen", 0)
    name_key, phone_key = f"nc_customer_name_input_{gen}", f"nc_customer_phone_input_{gen}"

    def _apply_customer_pick():
        picked = st.session_state.get("nc_customer_pick", "+ New Customer")
        if picked != "+ New Customer" and picked in customer_lookup:
            cname, cphone = customer_lookup[picked]
            st.session_state[name_key] = cname
            st.session_state[phone_key] = cphone
        else:
            st.session_state[name_key] = load_draft_field("nc_customer_name")
            st.session_state[phone_key] = load_draft_field("nc_customer_phone")

    customer_pick = st.selectbox(
        "Customer",
        [customer_options[0]] + sorted(customer_options[1:]),
        key="nc_customer_pick",
        on_change=_apply_customer_pick,
    )

    if name_key not in st.session_state or phone_key not in st.session_state:
        if customer_pick != "+ New Customer" and customer_pick in customer_lookup:
            prefill_name, prefill_phone = customer_lookup[customer_pick]
        else:
            prefill_name = load_draft_field("nc_customer_name")
            prefill_phone = load_draft_field("nc_customer_phone")
        st.session_state.setdefault(name_key, prefill_name)
        st.session_state.setdefault(phone_key, prefill_phone)

    a,b = st.columns(2)
    customer_name = a.text_input(
        "Customer Name *", key=name_key,
        on_change=lambda: save_draft_field("nc_customer_name", st.session_state.get(name_key, "")))
    customer_number = b.text_input(
        "Customer Phone *", key=phone_key,
        on_change=lambda: save_draft_field("nc_customer_phone", st.session_state.get(phone_key, "")))

    # Topper controls live OUTSIDE the main Streamlit form so they can react immediately.
    # Streamlit form widgets only update after form submission, which prevented the old
    # Yes/No -> count -> detail fields from appearing dynamically.
    topper_required = "No"
    topper_count = 0
    topper_1_wording = topper_1_notes = ""
    topper_2_wording = topper_2_notes = ""
    topper_3_wording = topper_3_notes = ""
    topper_wording = topper_notes = ""

    if product_type == "Cake":
        st.markdown("### Topper Requirements")
        topper_required = st.selectbox(
            "Does this cake need a topper?",
            ["No", "Yes"],
            key="nc_topper_required",
        )
        if topper_required == "Yes":
            topper_count = st.selectbox(
                "How many toppers does this cake need?",
                [1, 2, 3],
                key="nc_topper_count",
            )
            st.caption("Enter the wording/design details for each topper below.")
            topper_values = {}
            for topper_no in range(1, int(topper_count) + 1):
                st.markdown(f"**Topper {topper_no}**")
                wording = st.text_input(
                    f"Words on Topper {topper_no}",
                    key=f"nc_topper_{topper_no}_wording",
                )
                notes = st.text_area(
                    f"Topper {topper_no} Style / Design Notes",
                    key=f"nc_topper_{topper_no}_notes",
                )
                topper_values[topper_no] = (wording, notes)

            topper_1_wording, topper_1_notes = topper_values.get(1, ("", ""))
            topper_2_wording, topper_2_notes = topper_values.get(2, ("", ""))
            topper_3_wording, topper_3_notes = topper_values.get(3, ("", ""))

            topper_wording_parts, topper_notes_parts = [], []
            for topper_no, (wording, notes) in topper_values.items():
                if str(wording).strip():
                    topper_wording_parts.append(f"Topper {topper_no}: {str(wording).strip()}")
                if str(notes).strip():
                    topper_notes_parts.append(f"Topper {topper_no}: {str(notes).strip()}")
            topper_wording = " | ".join(topper_wording_parts)
            topper_notes = " | ".join(topper_notes_parts)

    # Sticker controls also live OUTSIDE the form so the visible page order is guaranteed:
    # Topper Requirements -> Sticker Requirements -> Order Type.
    # Keeping both requirement sections in the same Streamlit container prevents the
    # browser from visually placing the form's Sticker section after Order Type.
    sticker_required, sticker_count = "No", 0
    sticker_1_notes = sticker_2_notes = sticker_notes = ""
    if product_type == "Cake":
        st.markdown("### Sticker Requirements")
        sticker_required = st.selectbox(
            "Does the cake need sticker(s)?", ["No", "Yes"], key="nc_sticker_required"
        )
        if sticker_required == "Yes":
            sticker_count = st.number_input(
                "How many stickers does this cake need?",
                min_value=1, max_value=20, value=1, step=1, key="nc_sticker_count",
                help="Enter the actual number needed. Stickers are not limited to two or three."
            )
            sticker_notes = st.text_area(
                "Sticker Design / Wording Notes", key="nc_sticker_notes",
                placeholder="Example: 3 logo stickers + 2 Happy Birthday stickers"
            )
    else:
        topper_required, topper_count = "No", 0
        topper_wording = topper_notes = topper_1_wording = topper_1_notes = topper_2_wording = topper_2_notes = topper_3_wording = topper_3_notes = ""

    order_form_gen = st.session_state.get("nc_order_form_gen", 0)
    with st.form(f"new_order_form_{order_form_gen}", clear_on_submit=False):
        st.markdown("### Order Type")
        a,b = st.columns(2)
        order_type = a.selectbox("Order Type", ["Normal Order", "Urgent / Abrupt Order"])
        inventory_check = b.selectbox("Need baked cake inventory check?", ["No", "Yes"],
                                      index=1 if order_type == "Urgent / Abrupt Order" else 0)

        design = st.text_area("Description of Design *" if product_type not in SHORT_PIPELINE_PRODUCTS else "Order Notes *")
        imgs = st.file_uploader("Customer Reference Image(s)", type=["jpg","jpeg","png","heic","heif"], accept_multiple_files=True,
                                 help="iPhone photos are accepted too (HEIC/HEIF). The app compresses large phone photos after upload so orders stay fast.")
        # Do not use Streamlit's extension filter here. Safari sometimes supplies iPhone
        # videos with a generic/odd filename even though the MIME type is valid, which can
        # make the red upload warning appear before Python ever receives the file.
        vids = st.file_uploader(
            "Customer Reference Video(s)",
            accept_multiple_files=True,
            key=f"nc_reference_videos_{order_form_gen}",
            help="iPhone and Android videos are accepted. Choose the video directly from Photos/Files. MOV, MP4, M4V, WebM and 3GP are supported; maximum 150MB per video.",
        )
        if vids:
            st.caption(f"✅ {len(vids)} video(s) selected. They will be attached when you create the order.")

        st.markdown("### Delivery Window")
        st.caption("The time range the customer expects delivery within — used by Dispatch/Driver.")
        a,b = st.columns(2)
        delivery_window_start = a.time_input("Delivery Window Start", value=dtime(9,0))
        delivery_window_end = b.time_input("Delivery Window End", value=dtime(17,0))

        st.markdown("### Confirm Price" + (" & Layers" if product_type == "Cake" else ""))
        st.caption(f"Total: UGX {total_price:,.0f}")
        if product_type == "Cake":
            final_layers = st.number_input("Final Approved Layers", min_value=1, step=1, value=int(suggested))
        else:
            final_layers = 0

        flavour_preference_note = st.text_area(
            "Flavour Preference Note (optional)",
            placeholder="e.g. Client wants more fruit than blueberry",
            help="Shown to the Piler and on the order card throughout production — use this for any flavour-balance or layering preference that isn't captured by the flavour list alone.")

        payment_arrangement = st.selectbox(
            "Payment Arrangement",
            ["Deposit", "Full Payment", "No Deposit / Pay on Delivery"]
        )
        a,b = st.columns(2)
        if payment_arrangement == "Deposit":
            amount_paid = a.number_input("Deposit Paid (UGX)", min_value=0, step=5000)
        elif payment_arrangement == "Full Payment":
            amount_paid = a.number_input("Amount Paid (UGX)", min_value=0, step=5000, value=int(total_price))
        else:
            amount_paid = 0
            a.info("No deposit. Full amount will remain due for delivery.")
        payment_method = b.selectbox("Expected / Used Payment Method", ["Mobile Money", "Cash", "Bank Transfer", "Other"])

        st.markdown("### Delivery and Order Source")
        location = st.text_input("Delivery / Pickup Location")
        a,b,c = st.columns(3)
        order_channel = a.selectbox("Order Channel", ["Loyal Client", "Referral", "New Client", "Gift", "Cake Album", "Other"])
        priority_default = 2 if order_type == "Urgent / Abrupt Order" else 0
        priority = b.selectbox("Priority", ["Normal", "High", "Critical", "Low"], index=priority_default)
        created_by = c.text_input("Order Entered By *")

        st.markdown("### Production Assignment")
        is_short_pipeline = product_type in SHORT_PIPELINE_PRODUCTS
        if is_short_pipeline:
            st.caption(f"{product_type} goes straight from Baking to Packaging — no piler, coverer, or decorator needed for this product.")
            needs_baking = st.selectbox("Does this need baking, or is it already in inventory?",
                                         ["Needs baking", "Already in inventory — skip baking"], key="nc_needs_baking_short")
        else:
            needs_baking = st.selectbox("Does this need baking, or is it already-baked inventory being used?",
                                         ["Needs baking", "Already in inventory — skip baking"], key="nc_needs_baking_full")

        bakers_nc, pilers_nc, coverers_nc, decorators_nc, _ = staff_lists_fast()
        # The decorating team rotates through Piling and Covering week by week.
        # Do not depend on each account's old department flags: every active decorator
        # must always be available to Customer Care for all three production roles.
        rotating_decorators = decorators_nc
        pilers_nc = list(dict.fromkeys(list(pilers_nc) + list(rotating_decorators)))
        coverers_nc = list(dict.fromkeys(list(coverers_nc) + list(rotating_decorators)))
        decorators_nc = list(dict.fromkeys(list(decorators_nc) + list(rotating_decorators)))
        baker_counts = staff_workload_counts_from_df(df, "baker_assigned")
        mixer_counts = staff_workload_counts_from_df(df, "mixer_assigned")
        oven_counts = staff_workload_counts_from_df(df, "oven_person_assigned")
        piler_counts = staff_workload_counts_from_df(df, "piler_assigned")
        coverer_counts = staff_workload_counts_from_df(df, "coverer_assigned")
        decorator_counts = staff_workload_counts_from_df(df, "decorator_assigned")

        # Customer Care now plans the baking crew at order entry. The old routine
        # Customer Care -> Finance -> Production Planning -> Baking hop made every order
        # wait for another person to touch it. We keep Finance as the payment gate, but
        # once Finance approves, the already-assigned crew receives the cake immediately.
        baker_nc, mixer_nc, oven_nc = "", [], []
        if needs_baking == "Needs baking":
            st.markdown("#### 🔥 Baking Team — assign now")
            st.caption("Assign the people who will bake this order now. Finance only confirms payment; it will then go straight to Baking without waiting for Production Planning.")
            baker_nc = st.selectbox("Baker (In Charge) *", [""] + bakers_nc,
                                    format_func=lambda n: "Select baker" if not n else format_name_with_workload(n, baker_counts),
                                    key="nc_baker")
            mixer_nc = st.multiselect("Mixer(s) *", bakers_nc,
                                      format_func=lambda n: format_name_with_workload(n, mixer_counts), key="nc_mixers")
            oven_nc = st.multiselect("Oven In Charge *", bakers_nc,
                                     format_func=lambda n: format_name_with_workload(n, oven_counts), key="nc_oven_people")

        piler_nc, coverer_nc, decorator_nc = [], [], []
        topper_owner_nc, sticker_owner_nc = "Keith", "Doreen"
        if not is_short_pipeline:
            st.caption("Pick who's doing the piling, covering, and decoration for this cake now, so the whole production chain is already lined up at Customer Care. Workload shown next to each name is their current active job count.")
            piler_nc = st.multiselect("Piler(s)", pilers_nc, format_func=lambda n: format_name_with_workload(n, piler_counts), key="nc_piler_multi")
            coverer_nc = st.multiselect("Coverer(s)", coverers_nc, format_func=lambda n: format_name_with_workload(n, coverer_counts), key="nc_coverer_multi")
            decorator_nc = st.multiselect("Decorator(s)", decorators_nc, format_func=lambda n: format_name_with_workload(n, decorator_counts), key="nc_decorator_multi")
            if topper_required == "Yes":
                topper_owner_nc = st.text_input("Topper assigned to", value="Keith", key="nc_topper_owner")
            if sticker_required == "Yes":
                sticker_owner_nc = st.text_input("Sticker assigned to", value="Doreen", key="nc_sticker_owner")

        st.markdown(
            """<style>
            button[kind="primaryFormSubmit"] p,
            button[kind="primaryFormSubmit"] span {
                color: white !important;
                font-weight: 800 !important;
            }
            button[kind="primaryFormSubmit"] {
                color: white !important;
                font-weight: 800 !important;
            }
            </style>""",
            unsafe_allow_html=True,
        )
        submitted = st.form_submit_button("CREATE NEW ORDER", type="primary", width='stretch')

    if submitted:
        missing = [name for name,val in [("Customer Name",customer_name),("Customer Phone",customer_number),
                   ("Flavours",flavours),("Design",design),("Entered By",created_by)] if not str(val).strip()]
        if price <= 0: missing.append("Price")
        if needs_baking == "Needs baking":
            if not str(baker_nc).strip(): missing.append("Baker (In Charge)")
            if not mixer_nc: missing.append("Mixer")
            if not oven_nc: missing.append("Oven In Charge")
        if is_multi_tier == "Yes":
            incomplete_tiers = [str(t.get("tier")) for t in tier_details if len(t.get("flavours", [])) < 2]
            if incomplete_tiers:
                st.error("Each full tier must have at least two flavours. Check tier(s): " + ", ".join(incomplete_tiers)); return
            incomplete_sides = [str(t.get("side_cake")) for t in side_cake_details if len(t.get("flavours", [])) < 1]
            if incomplete_sides:
                st.error("Each side cake must have at least one flavour. Check side cake(s): " + ", ".join(incomplete_sides)); return
        if amount_paid > total_price: st.error("Amount paid cannot be greater than total price."); return
        if delivery_window_end <= delivery_window_start: st.error("Delivery window end must be after the start time."); return
        if missing:
            st.error("⚠️ Missing information: " + ", ".join(missing))
            st.info("Nothing you entered has been cleared. Add the missing information above, then press **Create New Order** again.")
            return
        invalid_videos = []
        for v in (vids or []):
            v_name = str(getattr(v, "name", "") or "video")
            v_suffix = Path(v_name).suffix.lower()
            v_type = str(getattr(v, "type", "") or "").lower()
            allowed_video_suffixes = {".mp4", ".mov", ".m4v", ".webm", ".3gp", ".hevc", ".h265"}
            if v_suffix not in allowed_video_suffixes and not v_type.startswith("video/"):
                invalid_videos.append(v_name)
        if invalid_videos:
            st.error("These selected files do not look like videos: " + ", ".join(invalid_videos) + ". Please choose the clip again from Photos or Files.")
            return

        oversized = [str(getattr(v, "name", "video")) for v in (vids or []) if len(v.getbuffer()) > MAX_VIDEO_SIZE_BYTES]
        if oversized:
            st.error(f"These video(s) are over the {MAX_VIDEO_SIZE_BYTES // (1024*1024)}MB limit and can't be uploaded: "
                     f"{', '.join(oversized)}. Please trim or compress the clip and try again.")
            return

        order_id = generate_order_id()
        image_path = ""
        image_base64 = ""
        all_images_b64 = []
        for idx, one_img in enumerate(imgs or []):
            img_bytes, suffix, mime = _prepare_uploaded_reference_image(one_img)
            target = REFERENCE_IMAGE_DIR / f"{order_id}_{idx}{suffix}"
            target.write_bytes(img_bytes)
            data_uri = f"data:{mime};base64,{base64.b64encode(img_bytes).decode()}"
            all_images_b64.append(data_uri)
            if idx == 0:
                image_path = str(target)
                image_base64 = data_uri
        images_json = json.dumps(all_images_b64) if all_images_b64 else ""

        video_records = []
        for idx, one_vid in enumerate(vids or []):
            try:
                v_path, v_mime, v_size = _prepare_uploaded_reference_video(one_vid, order_id, idx)
                video_records.append({
                    "filename": one_vid.name, "mime_type": v_mime, "file_path": v_path,
                    # Keep NOT NULL compatibility with old databases without storing the actual video in SQLite.
                    "data_base64": "", "file_size_bytes": v_size,
                })
            except Exception as exc:
                st.error(f"Could not save video {one_vid.name}: {exc}")
                return

        balance = max(total_price - amount_paid, 0)
        if payment_arrangement == "No Deposit / Pay on Delivery":
            workflow_status, owner, next_action = "Payment Approval Required", "Finance", "Approve no-deposit order"
        else:
            workflow_status, owner, next_action = "Awaiting Payment Confirmation", "Finance", f"Confirm {payment_arrangement.lower()}"

        insert_order({
            "order_id": order_id, "customer_name": customer_name.strip(), "customer_number": customer_number.strip(),
            "product_type": product_type, "size_category": size_category, "dozens_quantity": dozens_qty,
            "sold_from_inventory": sold_from_inventory, "inventory_batch_id": inventory_batch_id,
            "flavours": flavours.strip(), "design_description": design.strip(), "due_date": str(due_date), "delivery_date": str(delivery_date),
            "expected_time": str(expected_time), "price_ugx": total_price, "unit_price_ugx": price,
            "order_quantity": int(order_quantity), "is_bulk_order": is_bulk_order,
            "deposit": amount_paid, "balance": balance,
            "payment_method": payment_method, "payment_arrangement": payment_arrangement, "payment_status": "Pending",
            "location": location.strip(), "order_channel": order_channel, "workflow_status": workflow_status,
            "current_owner": owner, "next_action": next_action, "priority": priority,
            "urgency_level": "Urgent" if order_type == "Urgent / Abrupt Order" else "Normal",
            "balance_to_collect": balance, "balance_collection_status": "Pending" if balance > 0 else "Not Required",
            "finance_confirmation_status": "Pending" if balance > 0 else "Not Required",
            "delivery_status": "Not Started", "follow_up_status": "Pending", "issue_flag": "No",
            "cake_size_value": size_value, "cake_size_unit": "Inches", "cake_shape": shape, "cake_height_inches": cake_height,
            "cake_format": cake_format, "icing_type": icing_type,
            "number_of_layers": final_layers, "system_suggested_layers": suggested,
            "final_approved_layers": final_layers, "flavour_preference_note": flavour_preference_note.strip(), "reference_image_path": image_path, "reference_image_base64": image_base64,
            "reference_images_json": images_json, "cake_category": cake_category,
            "is_multi_tier": is_multi_tier, "tier_count": int(tier_count), "tier_details_json": json.dumps(tier_details) if tier_details else "",
            "side_cake_count": int(side_cake_count), "side_cake_details_json": json.dumps(side_cake_details) if side_cake_details else "",
            "order_type": order_type, "inventory_check_required": inventory_check,
            "delivery_window_start": str(delivery_window_start), "delivery_window_end": str(delivery_window_end),
            "topper_required": topper_required, "topper_count": int(topper_count or 0),
            "topper_wording": topper_wording.strip() if topper_required=="Yes" else "",
            "topper_notes": topper_notes.strip() if topper_required=="Yes" else "",
            "topper_1_wording": topper_1_wording.strip() if topper_required=="Yes" else "",
            "topper_1_notes": topper_1_notes.strip() if topper_required=="Yes" else "",
            "topper_2_wording": topper_2_wording.strip() if (topper_required=="Yes" and int(topper_count or 0) >= 2) else "",
            "topper_2_notes": topper_2_notes.strip() if (topper_required=="Yes" and int(topper_count or 0) >= 2) else "",
            "topper_3_wording": topper_3_wording.strip() if (topper_required=="Yes" and int(topper_count or 0) >= 3) else "",
            "topper_3_notes": topper_3_notes.strip() if (topper_required=="Yes" and int(topper_count or 0) >= 3) else "",
            "topper_status": ("Assigned" if (topper_required=="Yes" and not is_short_pipeline) else
                               ("Pending Assignment" if topper_required=="Yes" else "Not Required")),
            "topper_assigned_to": topper_owner_nc if (topper_required=="Yes" and not is_short_pipeline) else "",
            "sticker_required": sticker_required, "sticker_count": int(sticker_count or 0),
            "sticker_notes": sticker_notes.strip() if sticker_required=="Yes" else "",
            "sticker_1_notes": "", "sticker_2_notes": "",
            "sticker_status": ("Assigned" if (sticker_required=="Yes" and not is_short_pipeline) else
                                ("Pending Assignment" if sticker_required=="Yes" else "Not Required")),
            "sticker_assigned_to": sticker_owner_nc if (sticker_required=="Yes" and not is_short_pipeline) else "",
            "baker_assigned": baker_nc if needs_baking == "Needs baking" else "",
            "mixer_assigned": ", ".join(mixer_nc) if (needs_baking == "Needs baking" and mixer_nc) else "",
            "oven_person_assigned": ", ".join(oven_nc) if (needs_baking == "Needs baking" and oven_nc) else "",
            "piler_assigned": ", ".join(piler_nc) if piler_nc else "",
            "coverer_assigned": ", ".join(coverer_nc) if coverer_nc else "",
            "decorator_assigned": ", ".join(decorator_nc) if decorator_nc else "",
            "skip_baking": "Yes" if needs_baking == "Already in inventory — skip baking" else "No",
            "order_created_at": now_iso(), "last_updated_at": now_iso(), "last_updated_by": created_by.strip(),
        })
        if video_records:
            with connect() as conn:
                for vr in video_records:
                    cur = conn.execute("""INSERT INTO order_videos(order_id, filename, mime_type, data_base64, file_size_bytes, uploaded_at, file_path)
                                    VALUES(?,?,?,?,?,?,?)""",
                                 (order_id, vr["filename"], vr["mime_type"], vr["data_base64"], vr["file_size_bytes"], now_iso(), vr.get("file_path", "")))
                    vr["_video_id"] = cur.lastrowid
                conn.commit()
        # Convert iPhone QuickTime clips only after the order/video rows are committed.
        # This runs in the background, so Customer Care never waits for video transcoding.
        for vr in video_records:
            if vr.get("_video_id") and vr.get("file_path"):
                threading.Thread(
                    target=_optimize_order_video_async,
                    args=(vr["_video_id"], vr["file_path"], vr.get("mime_type", "")),
                    daemon=True, name=f"video-opt-{vr['_video_id']}"
                ).start()
        if sold_from_inventory == "Yes" and inventory_batch_id is not None:
            with connect() as conn:
                conn.execute("""UPDATE baked_cookie_inventory SET quantity_available = MAX(quantity_available - ?, 0),
                                reserved_order_id=? WHERE id=?""", (int(order_quantity), order_id, int(inventory_batch_id)))
                conn.execute("UPDATE baked_cookie_inventory SET inventory_status='Reserved' WHERE id=? AND quantity_available<=0", (int(inventory_batch_id),))
                conn.commit()
        create_notification(order_id, "Finance", None,
                             f"💰 New order {order_id} ({customer_name.strip()}) needs {next_action.lower()}.")
        try:
            fresh_df = load_orders()
            new_row = fresh_df[fresh_df["order_id"] == order_id]
            if not new_row.empty:
                # Google Sheets is a remote service; never make Customer Care wait for it.
                row_copy = new_row.iloc[0].copy()
                threading.Thread(target=sync_order_to_sheet, args=(row_copy,), daemon=True,
                                 name=f"sheet-sync-{order_id[-8:]}").start()
        except Exception:
            pass  # sheet sync issues should never block order creation
        st.success(f"Order {order_id} created ({order_quantity} unit(s), total UGX {total_price:,.0f}) and sent to Finance."
                   + (" Fulfilled from cookie inventory." if sold_from_inventory == "Yes" else ""))
        # Only reset the order form after a successful database insert. Failed validation
        # leaves the same generation active, so Customer Care never loses what they typed.
        st.session_state["nc_order_form_gen"] = st.session_state.get("nc_order_form_gen", 0) + 1
        clear_draft_field("nc_customer_name")
        clear_draft_field("nc_customer_phone")
        st.session_state["nc_customer_field_gen"] = st.session_state.get("nc_customer_field_gen", 0) + 1
        for k in ("nc_is_bulk", "nc_qty", "nc_price"):
            if k in st.session_state:
                del st.session_state[k]
        st.rerun()

    render_customer_care_inventory_view()

    st.markdown("### Recent Orders")
    recent_orders = load_orders().tail(25).iloc[::-1].copy()
    if not recent_orders.empty and "flavours" in recent_orders.columns:
        recent_orders["flavour_combination"] = recent_orders["flavours"].apply(
            lambda f: f"({f})" if f and str(f).strip() and str(f) != "nan" else "—")
    table(recent_orders, ["order_id","product_type","customer_name","order_type","urgency_level","flavour_combination","order_quantity","is_bulk_order",
          "payment_arrangement","due_date","expected_time","delivery_window_start","delivery_window_end",
          "cake_size_value","cake_shape","cake_format","system_suggested_layers","final_approved_layers","balance","workflow_status","current_owner"])

    st.markdown("### Daily Orders")
    st.caption("Pick a day to see the orders entered on that date (up to 20).")
    pick_date = st.date_input("Date", value=date.today(), key="daily_orders_date")
    df_all = load_orders()
    if not df_all.empty and "order_created_at" in df_all.columns:
        created_dates = pd.to_datetime(df_all["order_created_at"], errors="coerce").dt.date
        day_orders = df_all[created_dates == pick_date].sort_values("order_created_at", ascending=False).head(20)
    else:
        day_orders = df_all.iloc[0:0]
    st.caption(f"{len(day_orders)} order(s) entered on {pick_date.strftime('%d %b %Y')}" + (" (showing first 20)" if len(day_orders) == 20 else ""))
    table(day_orders, ["order_id","product_type","cake_category","customer_name","order_type","urgency_level","order_quantity","is_bulk_order",
          "price_ugx","balance","payment_arrangement","workflow_status","order_created_at"])

def notify_topper_sticker_if_approved(row, by):
    """Fires right when Finance moves an order forward (confirmed payment or approved for
    pay-on-delivery, whichever path). Topper/sticker assignment now happens upfront at
    Customer Care, but Design & Innovation shouldn't see or hear about it until the order
    is actually real - i.e. Finance has approved it. This is that one moment things become
    visible to them, matching the same payment-gate everything else in the pipeline uses."""
    if str(row.get("topper_required")) == "Yes":
        owner = disp(row.get("topper_assigned_to"))
        if owner != "—":
            create_notification(row.order_id, "Design & Innovation", owner,
                                 f"🎨 {row.order_id} ({disp(row.get('customer_name'))}) is confirmed — topper needed. "
                                 f"Words: {disp(row.get('topper_wording'))}.")
    if str(row.get("sticker_required")) == "Yes":
        owner = disp(row.get("sticker_assigned_to"))
        if owner != "—":
            create_notification(row.order_id, "Design & Innovation", owner,
                                 f"🏷️ {row.order_id} ({disp(row.get('customer_name'))}) is confirmed — sticker work needed. "
                                 f"Notes: {disp(row.get('sticker_notes'))}.")


def render_finance():
    page_header("💰 Finance", "Confirm deposits/full payments, approve no-deposit orders, confirm delivery money, track drivers, and reconcile daily.")
    df = load_orders()
    t1,t2,t3,t4,t5,t6,t7,t8 = st.tabs([
        "Confirm Order Payments", "Approve Pay on Delivery", "Confirm Delivery Money",
        "Drivers Out & Trip Tracking", "Cash Clearance (Driver Returns)", "Daily 5PM Reconciliation",
        "Staff Accountability", "🖼️ All Orders",
    ])

    with t1:
        row = select_order(filter_orders(df,["Awaiting Payment Confirmation","Awaiting Deposit"]), "finance_order_payment")
        if row is not None:
            order_card(row, [("Arrangement", row.get("payment_arrangement")), ("Paid", fmt_ugx(row.get("deposit"))), ("Balance", fmt_ugx(row.get("balance")))])
            by = st.text_input("Confirmed by", value="Teddy / Finance", key="fin_by1")
            if st.button("✅ Confirm Payment", width='stretch'):
                if row.get("sold_from_inventory") == "Yes":
                    update_order(row.order_id, {
                        "workflow_status":"Ready for Packaging", "current_owner":"Packaging",
                        "next_action":"Pack and print delivery note (from cookie inventory)", "payment_status":"Confirmed",
                        "finance_confirmation_status":"Confirmed" if float(row.get("balance") or 0) == 0 else "Pending"
                    }, by, "Order Payment Confirmed — Fulfilled From Inventory", "Finance")
                    create_notification(row.order_id, "Packaging", None,
                                         f"💰 {row.order_id} ({disp(row.get('customer_name'))}) — payment confirmed, ready to package from inventory.")
                    notify_topper_sticker_if_approved(row, by)
                    st.success("Payment confirmed — already baked, sent straight to Packaging.")
                elif row.get("skip_baking") == "Yes":
                    update_order(row.order_id, {
                        "workflow_status":"Piling Incoming", "current_owner":"Filling / Piling",
                        "next_action":"Piler to accept — abrupt order, baking skipped, from inventory flavours",
                        "payment_status":"Confirmed",
                        "finance_confirmation_status":"Confirmed" if float(row.get("balance") or 0) == 0 else "Pending"
                    }, by, "Order Payment Confirmed — Baking Skipped, Straight to Piling", "Finance")
                    create_notification(row.order_id, "Filling / Piling", row.get("piler_assigned"),
                                         f"💰 {row.order_id} ({disp(row.get('customer_name'))}) — payment confirmed. Abrupt order, baking skipped, ready to pile now.")
                    notify_topper_sticker_if_approved(row, by)
                    st.success("Payment confirmed — abrupt order from inventory, baking skipped, sent straight to Piling.")
                else:
                    update_order(row.order_id, {
                        "workflow_status":"Production Planned", "current_owner":"Baking",
                        "next_action":"Baking team to start", "payment_status":"Confirmed",
                        "production_planned_at":now_iso(), "baking_status":"Not Started",
                        "finance_confirmation_status":"Confirmed" if float(row.get("balance") or 0) == 0 else "Pending"
                    }, by, "Order Payment Confirmed — Released Directly to Baking", "Finance")
                    baker = row.get("baker_assigned")
                    mixers = row.get("mixer_assigned")
                    oven_people = row.get("oven_person_assigned")
                    if disp(baker) != "—":
                        create_notification(row.order_id, "Baking", baker,
                                             f"🔥 {row.order_id} ({disp(row.get('customer_name'))}) is payment-confirmed and assigned to you as Baker in Charge. Start baking.")
                    for person in _split_people(mixers):
                        create_notification(row.order_id, "Baking", person,
                                             f"🥣 {row.order_id} ({disp(row.get('customer_name'))}) is payment-confirmed — you are assigned as Mixer.")
                    for person in _split_people(oven_people):
                        create_notification(row.order_id, "Baking", person,
                                             f"🔥 {row.order_id} ({disp(row.get('customer_name'))}) is payment-confirmed — you are Oven In Charge.")
                    notify_topper_sticker_if_approved(row, by)
                    st.success("Payment confirmed — sent straight to the assigned Baking team.")
                st.rerun()

    with t2:
        row = select_order(filter_orders(df,["Payment Approval Required"]), "finance_no_deposit")
        if row is not None:
            order_card(row, [("Arrangement", row.get("payment_arrangement")), ("Amount due at delivery", fmt_ugx(row.get("balance")))])
            by = st.text_input("Approved by", value="Teddy / Finance", key="fin_by_no_dep")
            a,b = st.columns(2)
            if a.button("✅ Approve Pay on Delivery", width='stretch'):
                if row.get("sold_from_inventory") == "Yes":
                    update_order(row.order_id, {
                        "workflow_status":"Ready for Packaging", "current_owner":"Packaging",
                        "next_action":"Pack and print delivery note (from cookie inventory)", "payment_status":"Approved for Pay on Delivery"
                    }, by, "No Deposit Order Approved — Fulfilled From Inventory", "Finance")
                    create_notification(row.order_id, "Packaging", None,
                                         f"💰 {row.order_id} ({disp(row.get('customer_name'))}) — approved for pay-on-delivery, ready to package from inventory.")
                    notify_topper_sticker_if_approved(row, by)
                    st.success("Approved — already baked, sent straight to Packaging.")
                elif row.get("skip_baking") == "Yes":
                    update_order(row.order_id, {
                        "workflow_status":"Piling Incoming", "current_owner":"Filling / Piling",
                        "next_action":"Piler to accept — abrupt order, baking skipped, from inventory flavours",
                        "payment_status":"Approved for Pay on Delivery"
                    }, by, "No Deposit Order Approved — Baking Skipped, Straight to Piling", "Finance")
                    create_notification(row.order_id, "Filling / Piling", row.get("piler_assigned"),
                                         f"💰 {row.order_id} ({disp(row.get('customer_name'))}) — approved for pay-on-delivery. Abrupt order, baking skipped, ready to pile now.")
                    notify_topper_sticker_if_approved(row, by)
                    st.success("Approved — abrupt order from inventory, baking skipped, sent straight to Piling.")
                else:
                    update_order(row.order_id, {
                        "workflow_status":"Production Planned", "current_owner":"Baking",
                        "next_action":"Baking team to start", "payment_status":"Approved for Pay on Delivery",
                        "production_planned_at":now_iso(), "baking_status":"Not Started"
                    }, by, "No Deposit Order Approved — Released Directly to Baking", "Finance")
                    baker = row.get("baker_assigned")
                    mixers = row.get("mixer_assigned")
                    oven_people = row.get("oven_person_assigned")
                    if disp(baker) != "—":
                        create_notification(row.order_id, "Baking", baker,
                                             f"🔥 {row.order_id} ({disp(row.get('customer_name'))}) is approved for pay-on-delivery and assigned to you as Baker in Charge. Start baking.")
                    for person in _split_people(mixers):
                        create_notification(row.order_id, "Baking", person,
                                             f"🥣 {row.order_id} ({disp(row.get('customer_name'))}) is approved — you are assigned as Mixer.")
                    for person in _split_people(oven_people):
                        create_notification(row.order_id, "Baking", person,
                                             f"🔥 {row.order_id} ({disp(row.get('customer_name'))}) is approved — you are Oven In Charge.")
                    notify_topper_sticker_if_approved(row, by)
                    st.success("Approved — sent straight to the assigned Baking team.")
                st.rerun()
            if b.button("❌ Hold Order", width='stretch'):
                update_order(row.order_id, {"workflow_status":"Payment Hold","current_owner":"Customer Care","next_action":"Review payment arrangement"},
                             by, "No Deposit Order Held", "Finance")
                create_notification(row.order_id, "Customer Care", None,
                                     f"⚠️ {row.order_id} ({disp(row.get('customer_name'))}) — payment on hold, needs review.")
                st.warning("Order held for Customer Care review."); st.rerun()

    with t3:
        row = select_order(filter_orders(df,["Finance Payment Confirmation Pending"]), "finance_bal")
        if row is not None:
            order_card(row, [("Balance", fmt_ugx(row.get("balance_to_collect"))), ("Method", row.get("payment_method")), ("Driver", row.get("driver_assigned"))])
            by = st.text_input("Confirmed by", value="Teddy / Finance", key="fin_by2")
            if st.button("✅ Confirm Money Received", width='stretch'):
                is_cash = str(row.get("payment_method")) == "Cash"
                update_order(row.order_id, {
                    "finance_confirmation_status":"Confirmed", "payment_confirmed_at":now_iso(),
                    "balance_to_collect":0, "workflow_status":"Payment Confirmed",
                    "current_owner":"Driver", "next_action":"Complete delivery handover", "payment_status":"Paid",
                    "cash_cleared_status": "Pending Physical Handover" if is_cash else "Not Applicable",
                }, by, "Delivery Money Confirmed", "Finance")
                with connect() as conn:
                    conn.execute("UPDATE delivery_run_orders SET delivery_status='Payment Confirmed' WHERE order_id=? AND delivery_status='Finance Pending'", (row.order_id,))
                    conn.commit()
                st.success("Money confirmed." + (" This was Cash — it will show under Cash Clearance once the driver returns it physically." if is_cash else "")); st.rerun()

    with t4:
        st.markdown("### Drivers Currently Out")
        st.caption("Every delivery run in progress, who's driving it, when it started, and how far along it is — so you know who to expect and when.")
        if st.button("🔄 Refresh Now — Check for Latest Driver Updates", key="refresh_drivers_out", width='stretch'):
            st.rerun()
        runs = load_table("delivery_runs")
        active_runs = runs[runs["run_status"] == "In Progress"] if not runs.empty else runs
        if active_runs.empty:
            st.info("No drivers currently out on a run.")
        else:
            dro = load_table("delivery_run_orders")
            for _, run in active_runs.iterrows():
                started = run.get("run_started_at")
                elapsed_min = minutes_elapsed_since(started)
                elapsed_txt = f"{elapsed_min/60:.1f} hrs out" if elapsed_min is not None else "start time unknown"
                stops = dro[dro["run_id"] == run["run_id"]] if not dro.empty else dro
                total_stops = len(stops)
                done_stops = len(stops[stops["delivery_status"] == "Delivered"]) if not stops.empty else 0
                st.markdown(
                    f"<div class='ca-card'><b>🚗 {disp(run.get('driver_name'))}</b> — Run {disp(run.get('run_id'))}<br>"
                    f"<b>Started:</b> {disp(started)} ({elapsed_txt})<br>"
                    f"<b>Progress:</b> {done_stops}/{total_stops} stops delivered</div>",
                    unsafe_allow_html=True)
                if not stops.empty:
                    merged = stops.merge(load_orders(), on="order_id", how="left", suffixes=("_run","_order"))
                    status_col = "delivery_status_run" if "delivery_status_run" in merged.columns else "delivery_status"
                    cols = ["stop_sequence","order_id","customer_name","location",status_col,"balance_to_collect"]
                    cols = [c for c in cols if c in merged.columns]
                    table(merged.sort_values("stop_sequence"), cols)
            st.caption("👆 This page does not update itself automatically — press the Refresh button above any time a driver may have made progress.")

    with t5:
        st.markdown("### Cash Clearance — Driver Returns to Bakery")
        st.caption("Once Finance has already confirmed a cash payment remotely (previous tab), that cash still needs to be physically counted when the driver brings it back. Use this to do that final check.")
        if st.button("🔄 Refresh Now — Check for Newly Confirmed Cash", key="refresh_cash_clearance", width='stretch'):
            st.rerun()
        cash_orders = df[(df.get("cash_cleared_status") == "Pending Physical Handover")] if "cash_cleared_status" in df.columns else df.iloc[0:0]
        if cash_orders.empty:
            st.info("No cash awaiting physical handover right now.")
        else:
            drivers_pending = sorted(cash_orders["driver_assigned"].dropna().unique().tolist()) if "driver_assigned" in cash_orders.columns else []
            driver_pick = st.selectbox("Driver returning", drivers_pending if drivers_pending else ["Unassigned"])
            driver_orders = cash_orders[cash_orders.get("driver_assigned") == driver_pick] if drivers_pending else cash_orders
            expected_total = float(pd.to_numeric(driver_orders.get("balance"), errors="coerce").fillna(0).sum()) if not driver_orders.empty else 0.0
            table(driver_orders, ["order_id","customer_name","location","balance","delivery_status"])
            st.info(f"**Expected cash from these orders:** {fmt_ugx(expected_total)}")
            actual_cash = st.number_input("Actual cash counted (UGX)", min_value=0.0, step=1000.0, key="cash_actual")
            cleared_by = st.text_input("Cleared by", value="Teddy / Finance", key="cash_cleared_by")
            notes = st.text_input("Notes (optional — e.g. shortfall reason)", key="cash_notes")
            if st.button("✅ Confirm Cash Cleared", width='stretch'):
                variance = actual_cash - expected_total
                order_ids = ",".join(driver_orders["order_id"].tolist())
                run_id = ""
                dro = load_table("delivery_run_orders")
                if not dro.empty and not driver_orders.empty:
                    matches = dro[dro["order_id"].isin(driver_orders["order_id"])]
                    if not matches.empty:
                        run_id = matches.iloc[0]["run_id"]
                with connect() as conn:
                    conn.execute("""INSERT INTO cash_clearances(run_id,driver_name,order_ids,expected_cash,actual_cash,variance,cleared_by,cleared_at,notes)
                                    VALUES(?,?,?,?,?,?,?,?,?)""",
                                 (run_id, driver_pick, order_ids, expected_total, actual_cash, variance, cleared_by, now_iso(), notes))
                    for oid in driver_orders["order_id"].tolist():
                        conn.execute("UPDATE orders SET cash_cleared_status='Cleared', cash_cleared_at=?, cash_cleared_by=? WHERE order_id=?",
                                     (now_iso(), cleared_by, oid))
                    conn.commit()
                if abs(variance) > 0.5:
                    st.warning(f"Cash cleared with a variance of {fmt_ugx(variance)} ({'short' if variance<0 else 'over'}). Logged for follow-up.")
                else:
                    st.success("Cash cleared — matches expected amount exactly.")
                st.rerun()
        st.divider()
        st.markdown("#### Past Clearances")
        table(load_table("cash_clearances").sort_values("cleared_at", ascending=False).head(30) if not load_table("cash_clearances").empty else load_table("cash_clearances"),
              ["run_id","driver_name","expected_cash","actual_cash","variance","cleared_by","cleared_at","notes"])

    with t6:
        st.markdown("### Daily 5:00 PM Reconciliation")
        st.caption("Run this at day's close — compares orders dispatched today against money received digitally (Mobile Money/Card) vs cash collected in the field.")
        pick_date = st.date_input("Reconciliation date", value=date.today(), key="recon_date")
        day_str = pick_date.strftime("%Y-%m-%d")
        if not df.empty and "order_created_at" in df.columns:
            dispatched_dates = pd.to_datetime(df.get("packaging_completed_at"), errors="coerce").dt.date
            day_df = df[dispatched_dates == pick_date]
        else:
            day_df = df.iloc[0:0]
        c1,c2,c3,c4 = st.columns(4)
        kpi("Orders Dispatched", len(day_df))
        digital_methods = ["Mobile Money", "Card", "Visa", "Bank Transfer"]
        digital_total = float(pd.to_numeric(day_df[day_df.get("payment_method").isin(digital_methods)]["price_ugx"], errors="coerce").sum()) if not day_df.empty and "payment_method" in day_df.columns else 0.0
        cash_confirmed_total = float(pd.to_numeric(day_df[day_df.get("payment_method")=="Cash"]["price_ugx"], errors="coerce").sum()) if not day_df.empty and "payment_method" in day_df.columns else 0.0
        clearances_today = load_table("cash_clearances")
        clearances_today = clearances_today[pd.to_datetime(clearances_today["cleared_at"], errors="coerce").dt.date == pick_date] if not clearances_today.empty else clearances_today
        cash_actually_cleared = float(pd.to_numeric(clearances_today["actual_cash"], errors="coerce").sum()) if not clearances_today.empty else 0.0
        with c2: kpi("Digital (Mobile Money/Card)", fmt_ugx(digital_total))
        with c3: kpi("Cash Orders (expected)", fmt_ugx(cash_confirmed_total))
        with c4: kpi("Cash Physically Cleared", fmt_ugx(cash_actually_cleared))
        variance_today = cash_actually_cleared - cash_confirmed_total
        if not clearances_today.empty:
            if abs(variance_today) > 0.5:
                st.warning(f"⚠️ Cash variance today: {fmt_ugx(variance_today)} — check Cash Clearance notes for the reason.")
            else:
                st.success("✅ Cash cleared today matches expected cash orders exactly.")
        st.markdown(f"#### Orders Dispatched — {day_str}")
        table(day_df, ["order_id","customer_name","payment_method","price_ugx","balance","workflow_status","driver_assigned"])
        st.markdown("#### Cash Clearances Logged Today")
        table(clearances_today, ["run_id","driver_name","expected_cash","actual_cash","variance","cleared_by","cleared_at"])

    with t7:
        st.markdown("### Staff Accountability")
        st.caption("Every complaint that names a responsible department/person, with the estimated loss value they need to account for.")
        comp = load_table("complaints")
        accountable = comp[comp.get("loss_value_ugx", 0) > 0] if not comp.empty else comp
        if accountable.empty:
            st.info("No complaints with a loss value on file yet.")
        else:
            total_outstanding = float(pd.to_numeric(
                accountable[accountable.get("repayment_status") != "Repaid"]["loss_value_ugx"], errors="coerce"
            ).sum()) if not accountable.empty else 0.0
            c1, c2, c3 = st.columns(3)
            with c1: kpi("Cases With Loss Value", len(accountable))
            with c2: kpi("Total Outstanding", fmt_ugx(total_outstanding))
            with c3: kpi("Repaid", int((accountable.get("repayment_status") == "Repaid").sum()))
            table(accountable.sort_values("opened_at", ascending=False),
                  ["complaint_id", "order_id", "responsible_department", "responsible_person",
                   "complaint_category", "severity", "loss_value_ugx", "repayment_status",
                   "repayment_notes", "opened_at"])

            st.markdown("#### Update Repayment Status")
            cid = st.selectbox("Complaint", accountable["complaint_id"].tolist(), key="acct_cid")
            new_status = st.selectbox("Repayment status", ["Pending Review", "Deducted From Pay", "Repaid", "Waived"], key="acct_status")
            notes = st.text_area("Notes (e.g. how much was deducted, when, or why waived)", key="acct_notes")
            by = st.text_input("Recorded by", value="Teddy / Finance", key="acct_by")
            if st.button("✅ Update Accountability Record", width='stretch'):
                with connect() as conn:
                    conn.execute("""UPDATE complaints SET repayment_status=?, repayment_notes=?,
                                    repayment_recorded_by=?, repayment_recorded_at=? WHERE complaint_id=?""",
                                 (new_status, notes, by, now_iso(), cid))
                    conn.commit()
                st.success(f"Accountability record for {cid} updated to '{new_status}'."); st.rerun()

    with t8:
        render_order_gallery(df, "🖼️ All Orders — Images & Copyable Details")

def render_production_planning():
    page_header("🏭 Production Planning", "Assign production teams, plan extra baking buffers, and reserve inventory for urgent orders.")
    df = load_orders()
    render_due_alert_board(df)

    pending_reassign = load_table("reassignment_requests")
    pending_reassign = pending_reassign[pending_reassign["status"] == "Pending"] if not pending_reassign.empty else pending_reassign
    if not pending_reassign.empty:
        st.markdown(f"## ✋ HOD Reassignment Requests Awaiting Your Confirmation — {len(pending_reassign)}")
        st.caption("An HOD wants to hand off work they're personally assigned to. Confirm or decline before it takes effect.")
        table(pending_reassign, ["id", "order_id", "role_label", "current_value", "proposed_value", "requested_by", "reason", "requested_at"])
        req_id = st.selectbox("Select a request to decide", pending_reassign["id"].tolist(), key="pp_reassign_pick")
        req_row = pending_reassign[pending_reassign["id"] == req_id].iloc[0]
        st.write(f"**{req_row['requested_by']}** wants to change **{req_row['role_label']}** on order **{req_row['order_id']}** "
                 f"from *{req_row['current_value']}* to *{req_row['proposed_value']}*.")
        decision_by = st.text_input("Decided by", value=st.session_state.get("staff_name", "Production Manager"), key="pp_reassign_by")
        notes = st.text_input("Notes (optional)", key="pp_reassign_notes")
        a, b = st.columns(2)
        if a.button("✅ Confirm Handoff", width='stretch', key="pp_reassign_approve"):
            update_order(req_row["order_id"], {req_row["staff_column"]: req_row["proposed_value"]}, decision_by,
                         f"Reassignment Confirmed: {req_row['role_label']}", "Production Planning")
            with connect() as conn:
                conn.execute("UPDATE reassignment_requests SET status='Approved', decided_by=?, decided_at=?, decision_notes=? WHERE id=?",
                             (decision_by, now_iso(), notes, int(req_id)))
                conn.commit()
            create_notification(req_row["order_id"], "Decoration", req_row["requested_by"],
                                 f"Your handoff request for {req_row['order_id']} was confirmed by Production Planning.")
            for new_person in [p.strip() for p in str(req_row["proposed_value"]).split(",") if p.strip()]:
                create_notification(req_row["order_id"], "Decoration", new_person,
                                     f"You've been assigned to {req_row['order_id']} following a confirmed handoff.")
            st.success("Handoff confirmed and applied."); st.rerun()
        if b.button("❌ Decline", width='stretch', key="pp_reassign_decline"):
            with connect() as conn:
                conn.execute("UPDATE reassignment_requests SET status='Declined', decided_by=?, decided_at=?, decision_notes=? WHERE id=?",
                             (decision_by, now_iso(), notes, int(req_id)))
                conn.commit()
            create_notification(req_row["order_id"], "Decoration", req_row["requested_by"],
                                 f"Your handoff request for {req_row['order_id']} was declined by Production Planning."
                                 + (f" Reason: {notes}" if notes else ""))
            st.warning("Request declined."); st.rerun()
        st.divider()

    if st.session_state.get("is_hod"):
        st.markdown("### 👑 Head of Department — Full Production Pipeline")
        pipeline_statuses = ["Production Planned","Baking","Baking Correction Required","Piling Incoming","Piling","Piling Correction Required",
              "Covering Incoming","Covering","Covering Correction Required","Decorating Incoming","Decorating","Decoration Correction Required","Studio Check"]
        table(df[df["workflow_status"].isin(pipeline_statuses)] if "workflow_status" in df.columns else df.iloc[0:0],
              ["order_id","customer_name","urgency_level","baker_assigned","piler_assigned","coverer_assigned","decorator_assigned","workflow_status","next_action"])

    st.markdown("## 🧮 Batch Baking Plan")
    st.caption("Bakers asked to receive grouped totals per flavour and size (e.g. 'bake 12 layers of Vanilla, 8-inch') for a given day, rather than individual customer orders one at a time. "
               "Pick the day you're planning to bake for below — every pending order due that day gets broken down by flavour and totalled up. "
               "All the math happens here; bakers never see customer names or images, just the totals to bake.")
    planning_date = st.date_input("Planning for orders due on", value=date.today() + timedelta(days=1), key="pp_planning_date")
    batchable = filter_orders(df, ["Deposit Confirmed"])
    batchable = batchable[batchable["order_type"] != "Urgent / Abrupt Order"] if not batchable.empty and "order_type" in batchable.columns else batchable
    batchable = batchable[batchable["due_date"].astype(str) == str(planning_date)] if not batchable.empty and "due_date" in batchable.columns else batchable
    if batchable.empty:
        st.caption(f"No orders due on {planning_date.strftime('%d %b %Y')} are waiting to be grouped yet.")
    else:
        # Decompose each order's flavour list (an order can have more than one flavour) so a
        # multi-flavour cake contributes its layers to EACH flavour it actually contains, not
        # just to whatever exact flavour combination happens to match another order.
        exploded_rows = []
        for _, orow in batchable.iterrows():
            layers_total = pd.to_numeric(orow.get("final_approved_layers"), errors="coerce")
            if pd.isna(layers_total):
                layers_total = pd.to_numeric(orow.get("number_of_layers"), errors="coerce")
            layers_total = float(layers_total) if pd.notna(layers_total) else 1.0
            flavour_list = [f.strip() for f in str(orow.get("flavours") or "").split(",") if f.strip()]
            if not flavour_list:
                flavour_list = ["Unspecified"]
            share = layers_total / len(flavour_list)
            for fl in flavour_list:
                exploded_rows.append({
                    "order_id": orow["order_id"], "product_type": orow.get("product_type") or "Cake", "flavour": fl,
                    "cake_size_value": orow.get("cake_size_value"), "cake_shape": orow.get("cake_shape"),
                    "layers_for_this_flavour": share,
                })
        exploded = pd.DataFrame(exploded_rows)
        st.caption("Cakes with more than one flavour split their layers evenly across each flavour they contain, so the totals below reflect what's actually being baked. "
                   "Cookies, cupcakes, loaves, and layers are grouped the same way — by product type, flavour, and size — so they never mix in with cake batches.")
        summary = exploded.groupby(["product_type", "flavour", "cake_size_value", "cake_shape"]).agg(
            total_layers=("layers_for_this_flavour", "sum"), order_count=("order_id", "nunique"),
            order_ids=("order_id", lambda x: ", ".join(sorted(set(x))))).reset_index()
        summary["total_layers"] = summary["total_layers"].round(1)
        st.markdown(f"#### Ready to Group Into Batches — {planning_date.strftime('%d %b %Y')}")
        table(summary, ["product_type", "flavour", "cake_size_value", "cake_shape", "total_layers", "order_count", "order_ids"])
        summary["group_label"] = summary.apply(lambda r: f"{r['product_type']} — {r['flavour']} — {r['cake_size_value']:g}\" {r['cake_shape']} ({r['total_layers']:g} layers, {int(r['order_count'])} order(s))", axis=1)
        pick_label = st.selectbox("Select a group to turn into a batch", summary["group_label"].tolist(), key="pp_batch_pick")
        pick_row = summary[summary["group_label"] == pick_label].iloc[0]
        _bakers_b, _, _, _, _ = staff_lists()
        a, b, c = st.columns(3)
        batch_baker = a.selectbox("Assigned Baker", _bakers_b, format_func=first_name, key="pp_batch_baker")
        batch_mixer = b.multiselect("Mixer(s)", _bakers_b, format_func=first_name, key="pp_batch_mixer")
        batch_oven = c.multiselect("Oven Person(s)", _bakers_b, format_func=first_name, key="pp_batch_oven")
        batch_by = st.text_input("Planned by", value=st.session_state.get("staff_name", "Production Manager"), key="pp_batch_by")
        if st.button("🧮 Create Batch From This Group", width='stretch'):
            matching_rows = exploded[
                (exploded["product_type"] == pick_row["product_type"]) &
                (exploded["flavour"] == pick_row["flavour"]) &
                (exploded["cake_size_value"] == pick_row["cake_size_value"]) &
                (exploded["cake_shape"] == pick_row["cake_shape"])
            ]
            batch_date_str = str(planning_date)
            with connect() as conn:
                seq = conn.execute("SELECT COUNT(*) FROM baking_batches WHERE batch_date=?", (batch_date_str,)).fetchone()[0] + 1
                type_slug = "".join(ch for ch in str(pick_row["product_type"])[:6] if ch.isalnum()) or "Item"
                flavour_slug = "".join(ch for ch in str(pick_row["flavour"])[:10] if ch.isalnum()) or "Batch"
                batch_number = f"B-{batch_date_str}-{type_slug}-{flavour_slug}-{seq:02d}"
                cur = conn.execute("""INSERT INTO baking_batches(batch_number, batch_date, product_type, flavour, cake_size_value, cake_shape,
                                    total_layers_requested, status, assigned_baker, mixer_assigned, oven_person_assigned, created_by, created_at)
                                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                   (batch_number, batch_date_str, pick_row["product_type"], pick_row["flavour"], pick_row["cake_size_value"], pick_row["cake_shape"],
                                    round(pick_row["total_layers"]), "Pending", batch_baker, ", ".join(batch_mixer), ", ".join(batch_oven),
                                    batch_by, now_iso()))
                batch_id = cur.lastrowid
                for _, mrow in matching_rows.iterrows():
                    conn.execute("INSERT INTO baking_batch_orders(batch_id, order_id, layers_needed) VALUES(?,?,?)",
                                 (batch_id, mrow["order_id"], round(mrow["layers_for_this_flavour"])))
                    # An order can span more than one flavour batch (multi-flavour cake) — append
                    # this batch to its list rather than overwrite, so we can tell later whether
                    # ALL of an order's flavour batches are done before moving it to Piling.
                    existing_batches = conn.execute("SELECT baking_batch_number FROM orders WHERE order_id=?", (mrow["order_id"],)).fetchone()
                    existing_list = [b.strip() for b in str(existing_batches[0] or "").split(",") if b.strip()] if existing_batches else []
                    if batch_number not in existing_list:
                        existing_list.append(batch_number)
                    conn.execute("""UPDATE orders SET workflow_status='Production Planned', current_owner='Baking',
                                    baking_batch_number=?, next_action=?, baker_assigned=?, mixer_assigned=?, oven_person_assigned=?
                                    WHERE order_id=?""",
                                 (", ".join(existing_list), f"Bake as part of batch {batch_number}", batch_baker, ", ".join(batch_mixer), ", ".join(batch_oven), mrow["order_id"]))
                conn.commit()
            audit_log(None, "Baking Batch Created", "Production Planning",
                      f"Batch {batch_number}: {pick_row['flavour']} {pick_row['cake_size_value']:g}\" {pick_row['cake_shape']} — "
                      f"{pick_row['total_layers']:g} layers across {int(pick_row['order_count'])} order(s)", batch_by)
            crew = [batch_baker] + list(batch_mixer) + list(batch_oven)
            seen_crew = []
            for member in crew:
                if member and member not in seen_crew:
                    seen_crew.append(member)
            batch_msg = (f"🧮 New batch {batch_number}: {pick_row['total_layers']:g} layers of {pick_row['flavour']} "
                         f"({pick_row['cake_size_value']:g}\" {pick_row['cake_shape']}) across "
                         f"{int(pick_row['order_count'])} cake(s) — open the Batch Board in Baking to start.")
            for member in seen_crew:
                create_notification(None, "Baking", member, batch_msg)
            send_push_notification("Baking", f"New batch {batch_number}", batch_msg)
            st.success(f"Batch {batch_number} created — {pick_row['total_layers']:g} layers of {pick_row['flavour']} assigned to {first_name(batch_baker)}.")
            st.rerun()

    active_batches = load_table("baking_batches")
    active_batches = active_batches[active_batches["status"] != "Complete"] if not active_batches.empty else active_batches
    if not active_batches.empty:
        st.markdown("#### Active Batches — Grouped by Day")
        st.caption("What bakers will see: date, batch number, and the exact flavour/size/layer breakdown — no customer names.")
        table(active_batches.sort_values(["batch_date", "flavour", "cake_size_value"]),
              ["batch_date", "batch_number", "product_type", "flavour", "cake_size_value", "cake_shape", "total_layers_requested",
               "assigned_baker", "status"])
    st.divider()

    st.markdown("## 🚨 Urgent Orders (Skip Batch, Handle Individually)")
    st.caption("Every regular order — cakes, cookies, cupcakes, loaves, and layers alike — now goes through the batch "
               "grouping above. Only genuinely urgent or abrupt orders land here, since they can't wait for the next "
               "scheduled batch and often need to be filled straight from existing inventory instead.")
    pp_queue = filter_orders(df,["Deposit Confirmed"])
    pp_queue = pp_queue[(pp_queue["order_type"] == "Urgent / Abrupt Order") | (pp_queue["inventory_check_required"] == "Yes")] if not pp_queue.empty else pp_queue
    render_queue_table(pp_queue, "Urgent Orders Awaiting Production Assignment")
    row = select_order(pp_queue, "pp_order")
    if row is not None:
        order_card(row)
        is_urgent = row.get("order_type") == "Urgent / Abrupt Order" or row.get("inventory_check_required") == "Yes"
        if is_urgent:
            st.error("🚨 URGENT ORDER — CHECK BAKED CAKE INVENTORY")
            available = available_inventory_view()
            table(available, ["id","date_baked","flavour","cake_size_value","cake_shape","layers_available","quantity_available","baker","storage_location","inventory_status"])
            if not available.empty:
                inv_id = st.selectbox("Reserve baked inventory item", available["id"].tolist())
                st.caption("Same as a fresh order — assign the piler, coverer, and decorator who'll take this cake forward now, so it doesn't sit unassigned once it reaches Piling.")
                _,_pilers_inv,_coverers_inv,_decorators_inv,_ = staff_lists()
                a,b,c = st.columns(3)
                inv_piler = a.multiselect("Piler(s)", _pilers_inv, format_func=first_name, key="pp_inv_piler")
                inv_coverer = b.multiselect("Coverer(s)", _coverers_inv, format_func=first_name, key="pp_inv_coverer")
                inv_decorator = c.multiselect("Decorator(s)", _decorators_inv, format_func=first_name, key="pp_inv_decorator")
                by_inv = st.text_input("Reserved by", value="Production Manager", key="reserve_by")
                if st.button("⚡ Reserve Inventory Cake and Skip Baking", width='stretch'):
                    piler_val = ", ".join(inv_piler)
                    coverer_val = ", ".join(inv_coverer)
                    decorator_val = ", ".join(inv_decorator)
                    with connect() as conn:
                        # Reserve without reducing layers yet. The Piler confirms actual layers used.
                        conn.execute("""UPDATE baked_cake_inventory SET inventory_status='Reserved',
                                      reserved_order_id=?, reserved_at=? WHERE id=?""",
                                     (row.order_id, now_iso(), int(inv_id)))
                        conn.commit()
                    update_order(row.order_id, {
                        "inventory_reservation_id":int(inv_id), "workflow_status":"Piling Incoming",
                        "current_owner":"Filling / Piling", "next_action":"Piler to accept reserved baked cake",
                        "piler_assigned": piler_val, "coverer_assigned": coverer_val, "decorator_assigned": decorator_val,
                    }, by_inv, "Baked Inventory Reserved — Baking Skipped, Team Pre-Assigned", "Production Planning")
                    create_notification(row.order_id, "Filling / Piling", piler_val,
                                         f"🚨 {row.order_id} ({disp(row.get('customer_name'))}) — urgent, baking skipped, ready to pile now.")
                    # This urgent path was missing the topper/sticker handoff that the normal
                    # assignment flow already does - meaning any topper or sticker instructions
                    # for an urgent order silently never reached Design & Innovation as an
                    # actionable, notified assignment. Fixed to match the normal flow exactly.
                    if str(row.get("topper_required")) == "Yes":
                        target = topper_target_datetime(row)
                        update_order(row.order_id, {
                            "topper_assigned_to": "Keith", "topper_status": "Assigned",
                            "topper_target_at": target.isoformat() if target is not None else None,
                            "topper_pickup_note": "Topper assigned to Keith"
                        }, by_inv, "Topper Assigned", "Production Planning")
                        create_notification(row.order_id, "Design & Innovation", "Keith",
                                             f"🚨 Urgent order — new topper assignment. Words: {disp(row.get('topper_wording'))}. Decorator: {decorator_val}.")
                    if str(row.get("sticker_required")) == "Yes":
                        update_order(row.order_id, {
                            "sticker_assigned_to": "Doreen", "sticker_status": "Assigned",
                            "sticker_pickup_note": "Sticker assigned to Doreen"
                        }, by_inv, "Sticker Assigned", "Production Planning")
                        create_notification(row.order_id, "Design & Innovation", "Doreen",
                                             f"🚨 Urgent order — new sticker assignment for {row.order_id}. Notes: {disp(row.get('sticker_notes'))}. Decorator: {decorator_val}.")
                    st.success(f"Inventory reserved and team pre-assigned "
                               f"(Piler: {piler_val or 'none yet'}, Coverer: {coverer_val or 'none yet'}, Decorator: {decorator_val or 'none yet'}). "
                               f"Cake sent to Piling.")
                    st.rerun()

        bakers,pilers,coverers,decorators,_ = staff_lists()
        ptype = row.get("product_type") or "Cake"
        is_short_pipeline = ptype in SHORT_PIPELINE_PRODUCTS
        st.markdown("### Baker Assignment")
        st.caption("Piler, coverer, decorator, topper, and sticker are already assigned from Customer Care when the order "
                   "was created — this step is only for picking who bakes it.")
        existing_piler = disp(row.get("piler_assigned"))
        existing_coverer = disp(row.get("coverer_assigned"))
        existing_decorator = disp(row.get("decorator_assigned"))
        if not is_short_pipeline:
            st.info(f"Already assigned — Piler: {existing_piler} · Coverer: {existing_coverer} · Decorator: {existing_decorator}")
        a, b = st.columns(2)
        baker = a.selectbox("Baker (In Charge)", bakers, format_func=first_name)
        by = b.text_input("Updated by", value="Production Manager")
        mixer = st.multiselect("Mixer(s)", bakers, format_func=first_name, key="pp_mixer_multi")
        oven_person = st.multiselect("Oven Person(s)", bakers, format_func=first_name, key="pp_oven_multi")
        if is_short_pipeline:
            st.info(f"{PRODUCT_BADGE.get(ptype, ('',''))[0]} order — goes straight from Baking to Packaging, so only baking roles are needed here.")
        centerpiece_team, side_cake_team = [], []
        if str(row.get("is_multi_tier")) == "Yes":
            st.markdown("### 💍 Wedding Cake Team Split")
            st.caption("Assign a separate team to the centerpiece versus the side cakes — 2 or 3 people on each is fine.")
            centerpiece_team = st.multiselect("Centerpiece Team (2-3 people)", decorators, format_func=first_name, key="pp_centerpiece_team")
            side_cake_team = st.multiselect("Side Cake Team (2-3 people)", decorators, format_func=first_name, key="pp_side_cake_team")
        if st.button("Assign Baker", width='stretch'):
            mixer_val = ", ".join(mixer) if isinstance(mixer, list) else mixer
            oven_val = ", ".join(oven_person) if isinstance(oven_person, list) else oven_person
            centerpiece_val = ", ".join(centerpiece_team) if isinstance(centerpiece_team, list) else centerpiece_team
            side_cake_val = ", ".join(side_cake_team) if isinstance(side_cake_team, list) else side_cake_team
            update_order(row.order_id, {
                "baker_assigned":baker, "mixer_assigned":mixer_val, "oven_person_assigned":oven_val,
                "centerpiece_team_assigned":centerpiece_val, "side_cake_team_assigned":side_cake_val,
                "workflow_status":"Production Planned", "current_owner":"Baking", "next_action":"Start baking",
                "production_planned_at":now_iso(), "baking_status":"Not Started", "decoration_status":"Not Started",
            }, by, "Baker Assigned", "Production Planning")
            create_notification(row.order_id, "Baking", baker,
                                 f"🎂 {row.order_id} ({disp(row.get('customer_name'))}) has been assigned to you for baking.")
            due_str = disp(row.get("due_date"))
            if existing_piler != "—":
                create_notification(row.order_id, "Filling / Piling", row.get("piler_assigned"),
                                     f"📅 Heads up — {row.order_id} ({disp(row.get('customer_name'))}) is coming your way for piling. "
                                     f"Currently at Baking, due {due_str}. Check '📅 Incoming Workload' for the full picture.")
            if existing_coverer != "—":
                create_notification(row.order_id, "Coating / Covering", row.get("coverer_assigned"),
                                     f"📅 Heads up — {row.order_id} ({disp(row.get('customer_name'))}) is coming your way for covering. "
                                     f"Currently at Baking, due {due_str}. Check '📅 Incoming Workload' for the full picture.")
            if existing_decorator != "—":
                create_notification(row.order_id, "Decoration", row.get("decorator_assigned"),
                                     f"📅 Heads up — {row.order_id} ({disp(row.get('customer_name'))}) is coming your way for decoration. "
                                     f"Currently at Baking, due {due_str}. Check '📅 Incoming Workload' for the full picture.")
            st.success("Baker assigned."); st.rerun()

    st.divider()
    st.markdown("## 🧁 Extra Cake Layers for the Day (Abrupt/Buffer Stock)")
    st.caption("Bake ahead for abrupt/urgent clients — one flavour at a time. Add as many as you need, one after another; each becomes its own row below.")
    a,b,c,d = st.columns(4)
    plan_date = a.date_input("Plan Date", value=date.today(), key="pp_extra_date")
    extra_flavour_choice = b.selectbox("Flavour", STANDARD_FLAVOURS, key="pp_extra_flavour_choice")
    extra_flavour = st.text_input("Specify flavour", key="pp_extra_flavour_other") if extra_flavour_choice == "Other" else extra_flavour_choice
    extra_size_choice = c.selectbox("Cake Size (inches)", STANDARD_CAKE_SIZES, key="pp_extra_size_choice")
    extra_size = st.number_input("Specify size (inches)", min_value=0.0, step=0.5, value=8.0, key="pp_extra_size_other") if extra_size_choice == "Custom" else float(extra_size_choice)
    extra_shape = d.selectbox("Shape", ["Round","Rectangle","Square","Heart","Custom"], key="pp_extra_shape")
    a,b,c = st.columns(3)
    total_layers = a.number_input("Number of Layers to Bake (this flavour)", min_value=1, step=1, value=3, key="pp_extra_total_layers")
    _bakers_dd,_,_,_,_ = staff_lists()
    assigned_baker = b.selectbox("Assigned Baker", _bakers_dd, format_func=first_name, key="pp_extra_baker")
    reason = c.selectbox("Reason", ["Abrupt client buffer","Expected demand","Corporate orders","Wedding support","Other"], key="pp_extra_reason")
    created_by = st.text_input("Planned by", value=st.session_state.get("staff_name", "Production Manager"), key="pp_extra_by")
    if st.button("Assign Extra Baking to Baker", width='stretch', key="pp_extra_btn"):
        if not str(extra_flavour).strip():
            st.error("Enter the flavour.")
        else:
            with connect() as conn:
                conn.execute("""INSERT INTO extra_baking_assignments(plan_date,flavour,cake_size_value,cake_shape,layers_per_cake,
                                cake_units,total_layers,assigned_baker,reason,assignment_status,created_by,created_at)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                             (str(plan_date), str(extra_flavour).strip(), extra_size, extra_shape, int(total_layers),
                              1, int(total_layers), assigned_baker, reason, "Assigned", created_by, now_iso()))
                conn.commit()
            audit_log(None, "Extra Baking Assigned", "Production Planning",
                      f"{extra_size}\" {extra_shape} {extra_flavour}: {total_layers} layers",
                      created_by)
            st.success("Extra baking assignment sent to Baking. Add another below if needed — this form stays ready.")
            st.rerun()

    assignments = load_table("extra_baking_assignments")
    table(assignments.sort_values("created_at", ascending=False).head(25) if not assignments.empty else assignments,
          ["id","plan_date","flavour","cake_size_value","cake_shape","total_layers","assigned_baker","reason","assignment_status"])

    st.markdown("### Production Pipeline")
    table(filter_orders(df,["Production Planned","Baking","Baking Correction Required","Piling Incoming","Piling","Piling Correction Required",
          "Covering Incoming","Covering","Covering Correction Required","Decorating Incoming","Decorating","Decoration Correction Required","Studio Check"]),
          ["order_id","customer_name","order_type","due_date","expected_time","baker_assigned","piler_assigned","coverer_assigned","decorator_assigned","workflow_status","next_action"])

def issue_form(prefix, from_stage, to_stage, sender_status, sender_owner, row, default_person):
    with st.expander("❌ Reject / Return to Sender"):
        cat = st.selectbox("Issue category", ["Quality Issue", "Wrong Colour", "Wrong Size", "Wrong Flavour", "Height Issue", "Leaning", "Cracked", "Underbaked", "Overbaked", "Spelling/Design Issue", "Other"], key=f"{prefix}_cat")
        desc = st.text_area("Issue description", key=f"{prefix}_desc")
        resp_dept = st.text_input("Responsible department", value=from_stage, key=f"{prefix}_dept")
        resp_person = st.text_input("Responsible person", value=default_person or "", key=f"{prefix}_person")
        by = st.text_input("Rejected by", value=to_stage, key=f"{prefix}_by")
        if st.button("Log Issue and Return", key=f"{prefix}_reject", width='stretch'):
            insert_stage_check(row.order_id, from_stage, to_stage, by, "Rejected", cat, desc, resp_dept, resp_person)
            update_order(row.order_id, {"workflow_status":sender_status, "current_owner":sender_owner, "next_action":"Correct issue and resubmit", "issue_flag":"Yes", "issue_notes":desc}, by, "Stage Rejected", to_stage)
            escalate_urgency_on_rejection(row.order_id, by)
            st.warning("Issue logged, returned to sender, and order priority raised to Urgent."); st.rerun()


MINUTES_FULL_CAKE_BAKING = 90    # 1.5 hours, per Baking HOD — was 180, corrected to avoid over-baking
MINUTES_CUPCAKE_BAKING = 35      # 35–40 minutes, per Baking HOD — using the lower end as the minimum
MINUTES_COOKIE_BAKING = 40       # 40 minutes, per Baking HOD — must also be checked every 10 minutes while baking
COOKIE_CHECK_INTERVAL_MINUTES = 10
MINUTES_LOAF_LAYERS_BAKING = 90  # same as full cake by default (same batter, just undecorated) — adjust if loaves/layers actually differ


def baking_minimum_minutes(row):
    ptype = row.get("product_type")
    if ptype == "Cookies":
        return MINUTES_COOKIE_BAKING
    if ptype == "Cupcakes":
        return MINUTES_CUPCAKE_BAKING
    if ptype in ("Cake Loaves", "Cake Layers"):
        return MINUTES_LOAF_LAYERS_BAKING
    return MINUTES_FULL_CAKE_BAKING


def minutes_elapsed_since(iso_ts):
    if not iso_ts or str(iso_ts) in ("nan", "None", ""):
        return None
    try:
        started = datetime.fromisoformat(str(iso_ts))
        return (datetime.now() - started).total_seconds() / 60
    except Exception:
        return None


def batch_progress_map():
    """batch_id -> (cakes baked, cakes flagged, cakes total) so the board can show progress at a glance."""
    links = load_table("baking_batch_orders")
    progress = {}
    if links.empty:
        return progress
    for bid, grp in links.groupby("batch_id"):
        statuses = grp["baked_status"].fillna("Pending").tolist()
        progress[int(bid)] = (sum(1 for s in statuses if s == "Baked"),
                              sum(1 for s in statuses if s == "Issue"),
                              len(statuses))
    return progress


def batch_cake_spec(orders_df, order_id):
    """Flavour / size / shape only — bakers never need the customer name or the design."""
    match = orders_df[orders_df["order_id"] == order_id] if not orders_df.empty else orders_df
    if match.empty:
        return "—", None
    r = match.iloc[0]
    return f"{disp(r.get('flavours'))} · {disp(r.get('cake_size_value'))}\" {disp(r.get('cake_shape'))}", r


def start_baking_batch(brow, by, start_temp):
    """Start a batch: the batch AND every cake inside it move into 'Baking' together,
    so dashboards, Piling's incoming forecast and the oven log all stay in step."""
    started = now_iso()
    with connect() as conn:
        conn.execute("""UPDATE baking_batches SET status='Baking', baking_started_at=?, oven_start_temp_c=?, oven_started_by=?
                        WHERE id=?""", (started, int(start_temp), by, int(brow["id"])))
        member_rows = conn.execute("SELECT order_id FROM baking_batch_orders WHERE batch_id=?", (int(brow["id"]),)).fetchall()
        conn.execute("""INSERT INTO oven_logs(order_id, flavour, product_type, start_temp_c, oven_start_at, recorded_by_start)
                        VALUES(?,?,?,?,?,?)""",
                     (brow["batch_number"], brow.get("flavour"), "Batch", int(start_temp), started, by))
        conn.commit()
    for (oid,) in member_rows:
        update_order(oid, {"workflow_status": "Baking", "current_owner": "Baking",
                           "baking_started_at": started, "baking_status": "In Progress",
                           "next_action": f"Baking in batch {brow['batch_number']} — tick off when this cake is out"},
                     by, "Baking Started (Batch)", "Baking")
    audit_log(None, "Batch Baking Started", "Baking",
              f"Batch {brow['batch_number']} started at {int(start_temp)}°C with {len(member_rows)} cake(s).", by)
    return len(member_rows)


def complete_batch_cake(brow, lrow, by, actual_layers, stop_temp=None):
    """Tick one cake out of a batch. That single cake moves on by itself; the rest of the
    batch keeps baking. The batch only closes once no cake is still pending."""
    order_id = lrow["order_id"]
    completed = now_iso()
    with connect() as conn:
        conn.execute("""UPDATE baking_batch_orders SET baked_status='Baked', actual_layers_baked=?, baked_at=?, baked_by=?
                        WHERE id=?""", (int(actual_layers), completed, by, int(lrow["id"])))
        conn.commit()
        orow = conn.execute("""SELECT baking_batch_number, customer_name, product_type, piler_assigned
                               FROM orders WHERE order_id=?""", (order_id,)).fetchone()
        batch_numbers = [bn.strip() for bn in str((orow[0] if orow else "") or "").split(",") if bn.strip()]
        waiting_on_other_flavour = False
        if batch_numbers:
            ph = ",".join("?" * len(batch_numbers))
            rows = conn.execute(f"""SELECT bbo.baked_status FROM baking_batch_orders bbo
                                    JOIN baking_batches bb ON bbo.batch_id = bb.id
                                    WHERE bb.batch_number IN ({ph}) AND bbo.order_id=?""",
                                batch_numbers + [order_id]).fetchall()
            waiting_on_other_flavour = any((s[0] or "Pending") != "Baked" for s in rows)
        still_pending = conn.execute("""SELECT COUNT(*) FROM baking_batch_orders
                                        WHERE batch_id=? AND IFNULL(baked_status,'Pending')='Pending'""",
                                     (int(brow["id"]),)).fetchone()[0]
        batch_done = still_pending == 0
        total_actual = 0
        if batch_done:
            total_actual = conn.execute("SELECT SUM(actual_layers_baked) FROM baking_batch_orders WHERE batch_id=?",
                                        (int(brow["id"]),)).fetchone()[0] or 0
            conn.execute("""UPDATE baking_batches SET status='Complete', actual_layers_baked=?, completed_at=?, oven_stop_temp_c=?
                            WHERE id=?""", (int(total_actual), completed, int(stop_temp) if stop_temp else None, int(brow["id"])))
            open_log = conn.execute("""SELECT id FROM oven_logs WHERE order_id=? AND stop_temp_c IS NULL
                                       ORDER BY id DESC LIMIT 1""", (brow["batch_number"],)).fetchone()
            if open_log and stop_temp:
                conn.execute("UPDATE oven_logs SET stop_temp_c=?, oven_stop_at=?, recorded_by_stop=? WHERE id=?",
                             (int(stop_temp), completed, by, open_log[0]))
            conn.commit()

    ptype = (orow[2] if orow else None) or "Cake"
    customer = disp(orow[1]) if orow else ""
    cake_label = f"{disp(brow.get('flavour'))} · {brow.get('cake_size_value'):g}\"" if brow.get("cake_size_value") else disp(brow.get("flavour"))
    if waiting_on_other_flavour:
        audit_log(order_id, "Batch Portion Baked", "Baking",
                  f"{order_id} baked in batch {brow['batch_number']} — still waiting on another flavour batch.", by)
        message = f"{order_id} baked in this batch — still waiting on its other flavour batch before it can move on."
        notify_cake_finished(order_id,
                             f"✅ {order_id} ({cake_label}) is out of the oven in batch {brow['batch_number']} — "
                             f"still waiting on its other flavour batch before it moves on.",
                             mixers=brow.get("mixer_assigned"), oven_crew=brow.get("oven_person_assigned"))
    elif ptype in SHORT_PIPELINE_PRODUCTS:
        insert_stage_check(order_id, "Baking", "Packaging", by, "Passed")
        update_order(order_id, {"workflow_status": "Ready for Packaging", "current_owner": "Packaging",
                                "next_action": "Pack and print delivery note",
                                "baking_completed_at": completed, "baking_status": "Complete"},
                     by, f"Baking Passed (Batch {brow['batch_number']}) — {ptype}", "Baking")
        create_notification(order_id, "Packaging", None,
                            f"{order_id} ({customer}) has finished baking in batch {brow['batch_number']} and is ready to package.")
        message = f"{order_id} baked and sent straight to Packaging."
        notify_cake_finished(order_id,
                             f"✅ {order_id} ({cake_label}) is out of the oven in batch {brow['batch_number']} "
                             f"and has gone straight to Packaging.",
                             mixers=brow.get("mixer_assigned"), oven_crew=brow.get("oven_person_assigned"))
    else:
        insert_stage_check(order_id, "Baking", "Piling", by, "Passed")
        update_order(order_id, {"workflow_status": "Piling Incoming", "current_owner": "Filling / Piling",
                                "next_action": f"Piler to accept cake from batch {brow['batch_number']}",
                                "baking_completed_at": completed, "baking_status": "Complete"},
                     by, f"Baking Passed (Batch {brow['batch_number']})", "Baking")
        create_notification(order_id, "Filling / Piling", orow[3] if orow else None,
                            f"🎂 {order_id} ({customer}) baked in batch {brow['batch_number']} and is ready to pile.")
        message = f"{order_id} baked and handed to Piling."
        notify_cake_finished(order_id,
                             f"🎂 {order_id} ({cake_label}) is out of the oven in batch {brow['batch_number']} "
                             f"and is ready to pile / assemble.",
                             mixers=brow.get("mixer_assigned"), oven_crew=brow.get("oven_person_assigned"),
                             assemblers=(orow[3] if orow else None))
    return {"message": message, "batch_done": batch_done, "total_actual": int(total_actual),
            "waiting": waiting_on_other_flavour}


def flag_batch_cake_issue(brow, lrow, by, notes):
    """Pull one problem cake out of the batch without holding up the rest of it."""
    with connect() as conn:
        conn.execute("UPDATE baking_batch_orders SET baked_status='Issue', baked_at=?, baked_by=? WHERE id=?",
                     (now_iso(), by, int(lrow["id"])))
        conn.commit()
    update_order(lrow["order_id"], {"workflow_status": "Baking Correction Required", "current_owner": "Baking",
                                    "next_action": f"Re-bake — issue raised in batch {brow['batch_number']}",
                                    "issue_notes": notes, "baking_status": "Issue"},
                 by, f"Baking Issue Raised (Batch {brow['batch_number']})", "Baking")
    create_notification(None, "Baking", brow.get("assigned_baker"),
                        f"⚠️ {lrow['order_id']} was flagged in batch {brow['batch_number']}: {notes}")


def reopen_batch_cake(order_id, by):
    """A corrected cake goes back onto its batch board as pending again."""
    with connect() as conn:
        conn.execute("""UPDATE baking_batch_orders SET baked_status='Pending', actual_layers_baked=NULL, baked_at=NULL, baked_by=NULL
                        WHERE order_id=? AND IFNULL(baked_status,'Pending')='Issue'""", (order_id,))
        conn.execute("""UPDATE baking_batches SET status='Baking', completed_at=NULL
                        WHERE id IN (SELECT batch_id FROM baking_batch_orders WHERE order_id=?) AND status='Complete'""",
                     (order_id,))
        conn.commit()



def render_baking_simple_view():
    """Phone-first live queue for the Baking department.

    Customer Care has already chosen the Baker in Charge, Mixer(s), and Oven In Charge.
    Anyone on that crew can see the cake. Only one selected cake is rendered at a time, and
    the queue is urgency-first to keep low-cost phones responsive.
    """
    my_name = st.session_state.get("staff_name", "").strip()
    is_hod = st.session_state.get("is_hod")
    df = load_orders()
    q = df[df["workflow_status"].isin(["Production Planned", "Baking", "Baking Correction Required"])].copy() if not df.empty else df.iloc[0:0]
    if not q.empty and "baking_batch_number" in q.columns:
        # Batch cakes have their own batch board in Full View; don't duplicate them in the phone queue.
        batched = q["baking_batch_number"].fillna("").astype(str).str.strip() != ""
        q = q[~batched]

    if not is_hod and not q.empty and my_name:
        crew_blob = (
            q.get("baker_assigned", pd.Series("", index=q.index)).fillna("").astype(str) + " | " +
            q.get("mixer_assigned", pd.Series("", index=q.index)).fillna("").astype(str) + " | " +
            q.get("oven_person_assigned", pd.Series("", index=q.index)).fillna("").astype(str)
        )
        q = q[crew_blob.str.contains(my_name, case=False, regex=False, na=False)]

    if q.empty:
        st.success("🎉 No baking jobs waiting for you right now.")
        return

    def _sort_tuple(r):
        urgent = 0 if str(r.get("urgency_level") or "").strip().lower() == "urgent" else 1
        due = pd.to_datetime(r.get("due_date"), errors="coerce")
        due = due if not pd.isna(due) else pd.Timestamp.max
        try:
            tm = datetime.strptime(str(r.get("expected_time") or "23:59")[:5], "%H:%M").time()
        except Exception:
            tm = dtime(23, 59)
        status_rank = {"Baking Correction Required": 0, "Baking": 1, "Production Planned": 2}.get(str(r.get("workflow_status")), 3)
        return (urgent, due, tm, status_rank)

    q["_sort"] = q.apply(_sort_tuple, axis=1)
    q = q.sort_values("_sort", kind="stable").reset_index(drop=True)
    ids = q["order_id"].astype(str).tolist()
    rows = {str(r.get("order_id")): r for _, r in q.iterrows()}
    key = "baking_mobile_pick"
    if st.session_state.get(key) not in ids:
        st.session_state[key] = ids[0]

    st.markdown("""
    <style>
    div[data-testid="stRadio"] div[role="radiogroup"]{display:flex!important;flex-wrap:nowrap!important;overflow-x:auto!important;gap:.45rem!important;padding:.25rem .05rem .65rem!important;-webkit-overflow-scrolling:touch}
    div[data-testid="stRadio"] div[role="radiogroup"]>label{flex:0 0 auto!important;min-height:46px;padding:.45rem .65rem!important;border:1px solid #D7C6DF;border-radius:10px;background:#F7F1FA;white-space:nowrap}
    div[data-testid="stButton"]>button{min-height:56px!important;font-size:1.03rem!important;font-weight:700!important;border-radius:10px!important}
    @media(max-width:640px){.block-container{padding-left:.65rem!important;padding-right:.65rem!important;padding-top:.7rem!important}div[data-testid="stImage"] img{width:100%!important;height:auto!important;border-radius:12px!important}}
    </style>
    """, unsafe_allow_html=True)

    def _label(oid):
        r = rows[str(oid)]
        urgent = "🚨 " if str(r.get("urgency_level") or "").lower() == "urgent" else ""
        status = "🔁 " if r.get("workflow_status") == "Baking Correction Required" else ("🔥 " if r.get("workflow_status") == "Baking" else "")
        due = disp(r.get("due_date")); tm = disp(r.get("expected_time"))
        short = due[5:] if len(due) >= 10 and due[4:5] == "-" else due
        return f"{urgent}{status}{oid} • {short}" + (f" {tm}" if tm != "—" else "")

    st.markdown(f"**🔥 BAKING QUEUE — {len(q)} job(s), most urgent first. Swipe left/right.**")
    oid = st.radio("Baking queue", ids, key=key, horizontal=True, label_visibility="collapsed", format_func=_label)
    row = rows[str(oid)]

    render_reference_images(row)
    urgent = "🚨 URGENT — " if str(row.get("urgency_level") or "").lower() == "urgent" else ""
    st.markdown(f"### {urgent}{row.get('order_id')} — {disp(row.get('customer_name'))}")
    st.markdown(f"**Due:** {disp(row.get('due_date'))} at {disp(row.get('expected_time'))}")
    st.markdown(f"**Flavours:** {disp(row.get('flavours'))}")
    st.markdown(f"**Size:** {disp(row.get('cake_size_value'))}\" {disp(row.get('cake_shape'))} · **Layers:** {disp(row.get('final_approved_layers'))}")
    st.markdown(f"**Baker in Charge:** {disp(row.get('baker_assigned'))}")
    st.markdown(f"**Mixer:** {disp(row.get('mixer_assigned'))}")
    st.markdown(f"**Oven In Charge:** {disp(row.get('oven_person_assigned'))}")

    # Tell the logged-in worker why this card is on their phone.
    my_roles = []
    if my_name and my_name.lower() in str(row.get("baker_assigned") or "").lower(): my_roles.append("Baker in Charge")
    if my_name and my_name.lower() in str(row.get("mixer_assigned") or "").lower(): my_roles.append("Mixer")
    if my_name and my_name.lower() in str(row.get("oven_person_assigned") or "").lower(): my_roles.append("Oven In Charge")
    if my_roles:
        st.info("Your role on this cake: **" + " + ".join(my_roles) + "**")

    notes = disp(row.get("design_description"))
    if notes != "—":
        st.markdown("### **INSTRUCTIONS**")
        pieces = [x.strip(" -•\t") for x in re.split(r"[\n*]+", str(notes)) if x.strip(" -•\t")]
        for i, piece in enumerate(pieces or [notes], 1):
            piece = re.sub(r"^\d+[.):]\s*", "", str(piece)).strip()
            st.markdown(f"**{i}. {piece}**")

    status = row.get("workflow_status")
    if status == "Baking Correction Required":
        st.error(f"Correction required: {disp(row.get('issue_notes'))}")
        by = st.text_input("Corrected by", value=my_name or disp(row.get("baker_assigned")), key=f"mob_bake_corr_by_{oid}")
        if st.button("🔁 Correction complete — return to baking", key=f"mob_bake_corr_{oid}", width='stretch'):
            update_order(row.order_id, {"workflow_status":"Baking", "current_owner":"Baking", "next_action":"Complete corrected bake", "baking_status":"In Progress"}, by, "Baking Correction Completed", "Baking")
            st.rerun()
        return

    has_materials = render_stage_material_planning("Baking", row, row.get("baker_assigned"), key_prefix=f"mobile_bake_{oid}")
    by = st.text_input("Working on this cake", value=my_name or disp(row.get("baker_assigned")), key=f"mob_bake_by_{oid}")

    if status == "Production Planned":
        start_temp = st.number_input("Oven start temperature (°C)", min_value=0, max_value=300, value=180, step=5, key=f"mob_bake_start_temp_{oid}")
        if not has_materials:
            st.caption("Log at least one baking material above before starting.")
        if st.button("▶️ START BAKING", key=f"mob_bake_start_{oid}", width='stretch', disabled=not has_materials):
            started = now_iso()
            update_order(row.order_id, {"workflow_status":"Baking", "current_owner":"Baking", "next_action":"Bake and complete oven check", "baking_started_at":started, "baking_status":"In Progress"}, by, "Baking Started", "Baking")
            with connect() as conn:
                conn.execute("""INSERT INTO oven_logs(order_id, flavour, product_type, start_temp_c, oven_start_at, recorded_by_start)
                                VALUES(?,?,?,?,?,?)""", (row.order_id, row.get("flavours"), row.get("product_type"), start_temp, started, by))
                conn.commit()
            st.rerun()
        return

    required = baking_minimum_minutes(row)
    elapsed = minutes_elapsed_since(row.get("baking_started_at"))
    if elapsed is not None and elapsed < required:
        st.warning(f"⏳ Baking for {elapsed:.0f} of minimum {required} minutes — about {max(required-elapsed,0):.0f} minute(s) remaining.")
    elif elapsed is not None:
        st.success(f"✅ Minimum baking time of {required} minutes met.")
    stop_temp = st.number_input("Final oven temperature (°C)", min_value=0, max_value=300, value=180, step=5, key=f"mob_bake_stop_temp_{oid}")
    can_finish = has_materials and (elapsed is None or elapsed >= required)
    ptype = row.get("product_type") or "Cake"
    short = ptype in SHORT_PIPELINE_PRODUCTS
    label = "✅ BAKED → SEND TO PACKAGING" if short else "✅ BAKED → SEND TO PILER"
    if st.button(label, key=f"mob_bake_done_{oid}", width='stretch', disabled=not can_finish):
        stopped = now_iso()
        with connect() as conn:
            open_id = conn.execute("SELECT id FROM oven_logs WHERE order_id=? AND stop_temp_c IS NULL ORDER BY id DESC LIMIT 1", (row.order_id,)).fetchone()
            if open_id:
                conn.execute("UPDATE oven_logs SET stop_temp_c=?, oven_stop_at=?, recorded_by_stop=? WHERE id=?", (stop_temp, stopped, by, open_id[0]))
                conn.commit()
        if short:
            insert_stage_check(row.order_id, "Baking", "Packaging", by, "Passed")
            update_order(row.order_id, {"workflow_status":"Ready for Packaging", "current_owner":"Packaging", "next_action":"Pack and print delivery note", "baking_completed_at":stopped, "baking_status":"Complete"}, by, f"Baking Passed — {ptype}", "Baking")
            create_notification(row.order_id, "Packaging", None, f"✅ {row.order_id} ({disp(row.get('customer_name'))}) has finished baking and is ready to package.")
            notify_cake_finished(row.order_id, f"✅ {row.order_id} ({disp(row.get('flavours'))}) is out of the oven and ready for Packaging.", mixers=row.get("mixer_assigned"), oven_crew=row.get("oven_person_assigned"))
        else:
            insert_stage_check(row.order_id, "Baking", "Piling", by, "Passed")
            update_order(row.order_id, {"workflow_status":"Piling Incoming", "current_owner":"Filling / Piling", "next_action":"Piler to accept cake", "baking_completed_at":stopped, "baking_status":"Complete"}, by, "Baking Passed", "Baking")
            create_notification(row.order_id, "Filling / Piling", row.get("piler_assigned"), f"🎂 {row.order_id} ({disp(row.get('customer_name'))}) has finished baking and is ready to pile.")
            notify_cake_finished(row.order_id, f"🎂 {row.order_id} ({disp(row.get('flavours'))}) is out of the oven and ready to pile / assemble.", mixers=row.get("mixer_assigned"), oven_crew=row.get("oven_person_assigned"), assemblers=row.get("piler_assigned"))
        st.rerun()


if hasattr(st, "fragment"):
    render_baking_simple_view_live = st.fragment(run_every="10s")(render_baking_simple_view)
else:
    render_baking_simple_view_live = render_baking_simple_view

def render_baking():
    page_header("🔥 Baking", "Customer Care assigns Baker, Mixer and Oven In Charge at order entry. After Finance approves, the cake appears here immediately.")
    view_mode = st.radio("View", ["📷 Simple View", "📋 Full View"], key="baking_view_mode", horizontal=True,
                         index=(0 if st.session_state.get("baking_view_mode", "📷 Simple View") == "📷 Simple View" else 1))
    if view_mode == "📷 Simple View":
        render_baking_simple_view_live()
        return
    df = load_orders()
    render_hod_overview("Baking", df)
    tab_batch, tab_assigned, tab_progress, tab_correction, tab_cakeinv, tab_cookieinv, tab_oven, tab_finished, tab_gallery = st.tabs(
        ["🧮 Batch Board", "Assigned", "In Progress", "Correction Required", "Baked Cake Inventory", "🍪 Baked Cookie Inventory", "🌡️ Oven Log", "✅ Finished Work", "🖼️ All Orders"])
    with tab_assigned:
        assigned_q = filter_orders(df,["Production Planned"])
        assigned_q = assigned_q[assigned_q["baking_batch_number"].isna() | (assigned_q["baking_batch_number"] == "")] if not assigned_q.empty and "baking_batch_number" in assigned_q.columns else assigned_q
        render_queue_table(assigned_q, "Cakes Assigned To Baking",
                            extra_columns=["baker_assigned", "mixer_assigned", "oven_person_assigned"],
                            base_cols_override=["#", "🚨", "order_id", "flavour_combination", "product_type", "order_type",
                                                 "urgency_level", "due_date", "expected_time", "workflow_status"])
        row = select_order(assigned_q, "bake_assigned")
        if row is not None:
            order_card(row, [("Baker (In Charge)", row.get("baker_assigned")), ("Mixer", row.get("mixer_assigned")), ("Oven", row.get("oven_person_assigned")), ("Format", row.get("cake_format"))], show_image=False)
            st.markdown("### Materials Needed for This Bake")
            st.markdown(
                "<div style='background:#FBEAEA;border:1px solid #E8B4B4;border-radius:8px;padding:10px 14px;"
                "color:#8C1D1D;font-weight:700;'>⚠️ Kindly enter the materials you're going to use before you start to bake.</div>",
                unsafe_allow_html=True)
            render_stage_material_planning("Baking", row, row.get("baker_assigned"))
            logged_materials = load_table("stage_material_usage")
            has_materials = not logged_materials.empty and not logged_materials[
                (logged_materials["order_id"] == row.order_id) & (logged_materials["stage"] == "Baking")].empty
            by = st.text_input("Updated by", value=disp(row.get("baker_assigned")), key="bake_by1")
            start_temp = st.number_input("Oven Start Temperature (°C)", min_value=0, max_value=300, value=180, step=5, key="bake_start_temp")
            if not has_materials:
                st.caption("The Start Baking button unlocks once at least one material has been logged above.")
            if st.button("▶️ Start Baking", width='stretch', disabled=not has_materials):
                started_at = now_iso()
                update_order(row.order_id, {"workflow_status":"Baking", "current_owner":"Baking", "next_action":"Submit for baking check", "baking_started_at":started_at, "baking_status":"In Progress"}, by, "Baking Started", "Baking")
                with connect() as conn:
                    conn.execute("""INSERT INTO oven_logs(order_id, flavour, product_type, start_temp_c, oven_start_at, recorded_by_start)
                                    VALUES(?,?,?,?,?,?)""",
                                 (row.order_id, row.get("flavours"), row.get("product_type"), start_temp, started_at, by))
                    conn.commit()
                st.rerun()

        st.divider()
        st.markdown("### 🧁 Extra Cake Layers for the Day — Assigned to You")
        st.caption("Pre-baked layers for abrupt/urgent clients, assigned by Production Planning — separate from customer orders above.")
        extra_here = load_table("extra_baking_assignments")
        pending_extra_here = extra_here[extra_here["assignment_status"].isin(["Assigned","In Progress"])] if not extra_here.empty else extra_here
        if pending_extra_here.empty:
            st.caption("No extra cake layers assigned for today.")
        else:
            table(pending_extra_here, ["id","plan_date","flavour","cake_size_value","cake_shape","layers_per_cake","cake_units","total_layers","assigned_baker","reason","assignment_status"])
            st.caption("Go to the 'Baked Cake Inventory' tab to mark one of these complete once baked.")
    with tab_progress:
        prog_q = filter_orders(df,["Baking"])
        if not prog_q.empty and "baking_batch_number" in prog_q.columns:
            batched_mask = prog_q["baking_batch_number"].notna() & (prog_q["baking_batch_number"].astype(str).str.strip() != "")
            if batched_mask.any():
                st.caption(f"🧮 {int(batched_mask.sum())} cake(s) currently baking are part of a batch — tick those off in the "
                           "**Batch Board** tab instead, so the batch totals stay correct.")
            prog_q = prog_q[~batched_mask]
        render_queue_table(prog_q, "Cakes Currently Baking (Individual Bakes)", ["baker_assigned"])
        row = select_order(prog_q, "bake_prog")
        if row is not None:
            order_card(row, show_image=False)
            required_mins = baking_minimum_minutes(row)
            elapsed = minutes_elapsed_since(row.get("baking_started_at"))
            oven_logs = load_table("oven_logs")
            open_log = oven_logs[(oven_logs["order_id"] == row.order_id) & (oven_logs["stop_temp_c"].isna())] if not oven_logs.empty else oven_logs
            if not open_log.empty:
                st.caption(f"🌡️ Oven started at {open_log.iloc[-1]['start_temp_c']:.0f}°C")
            has_baking_materials = render_stage_material_planning("Baking", row, row.get("baker_assigned"))
            by = st.text_input("Checked/Submitted by", value=disp(row.get("baker_assigned")), key="bake_by2")
            stop_temp = st.number_input("Oven Stop / Final Temperature (°C)", min_value=0, max_value=300, value=180, step=5, key="bake_stop_temp")
            can_pass = (elapsed is None or elapsed >= required_mins) and has_baking_materials
            if row.get("product_type") == "Cookies" and elapsed is not None:
                checkpoints = list(range(COOKIE_CHECK_INTERVAL_MINUTES, MINUTES_COOKIE_BAKING + 1, COOKIE_CHECK_INTERVAL_MINUTES))
                last_checkpoint = max([c for c in checkpoints if c <= elapsed], default=0)
                next_checkpoint = next((c for c in checkpoints if c > elapsed), None)
                if next_checkpoint is not None:
                    mins_to_next = next_checkpoint - elapsed
                    st.info(f"🔔 Cookies must be checked every {COOKIE_CHECK_INTERVAL_MINUTES} minutes. "
                            f"Last checkpoint: {last_checkpoint} min." +
                            (f" Next check in about {mins_to_next:.0f} minute(s) (at the {next_checkpoint}-minute mark)." if mins_to_next > 0 else " Check now."))
                else:
                    st.info(f"🔔 All {COOKIE_CHECK_INTERVAL_MINUTES}-minute checkpoints passed ({last_checkpoint} min). Do a final check before marking baked.")
            if elapsed is not None:
                remaining = max(required_mins - elapsed, 0)
                threshold_label = ("cookies" if required_mins == MINUTES_COOKIE_BAKING
                                    else "cupcakes" if required_mins == MINUTES_CUPCAKE_BAKING
                                    else "loaves/layers" if required_mins == MINUTES_LOAF_LAYERS_BAKING and row.get("product_type") in ("Cake Loaves","Cake Layers")
                                    else "full cake")
                if remaining > 0:
                    st.warning(f"⏳ This needs at least {required_mins} minutes of baking time "
                               f"({threshold_label} threshold). "
                               f"About {remaining:.0f} more minute(s) before it can be marked baked.")
                else:
                    st.success(f"✅ Minimum baking time of {required_mins} minutes met.")
            ptype = row.get("product_type") or "Cake"
            is_short_pipeline = ptype in SHORT_PIPELINE_PRODUCTS
            btn_label = "✅ Baking Check Passed → Send to Packaging" if is_short_pipeline else "✅ Baking Check Passed → Send to Piling"
            a,b = st.columns(2)
            if a.button(btn_label, width='stretch', disabled=not can_pass):
                stopped_at = now_iso()
                with connect() as conn:
                    open_id = conn.execute(
                        "SELECT id FROM oven_logs WHERE order_id=? AND stop_temp_c IS NULL ORDER BY id DESC LIMIT 1",
                        (row.order_id,)).fetchone()
                    if open_id:
                        conn.execute("UPDATE oven_logs SET stop_temp_c=?, oven_stop_at=?, recorded_by_stop=? WHERE id=?",
                                     (stop_temp, stopped_at, by, open_id[0]))
                        conn.commit()
                if is_short_pipeline:
                    insert_stage_check(row.order_id,"Baking","Packaging",by,"Passed")
                    update_order(row.order_id, {"workflow_status":"Ready for Packaging", "current_owner":"Packaging", "next_action":"Pack and print delivery note", "baking_completed_at":stopped_at, "baking_status":"Complete"}, by, f"Baking Passed — {ptype} (Piling/Covering/Decoration/QC skipped)", "Baking")
                    create_notification(row.order_id, "Packaging", None,
                                         f"{PRODUCT_BADGE.get(ptype, ('',''))[0]} order {row.order_id} ({disp(row.get('customer_name'))}) has finished baking and is ready to package.")
                    notify_cake_finished(row.order_id,
                                         f"✅ {row.order_id} ({disp(row.get('flavours'))}) is out of the oven and has gone straight to Packaging.",
                                         mixers=row.get("mixer_assigned"), oven_crew=row.get("oven_person_assigned"))
                else:
                    insert_stage_check(row.order_id,"Baking","Piling",by,"Passed")
                    update_order(row.order_id, {"workflow_status":"Piling Incoming", "current_owner":"Filling / Piling", "next_action":"Piler to accept cake", "baking_completed_at":stopped_at, "baking_status":"Complete"}, by, "Baking Passed", "Baking")
                    create_notification(row.order_id, "Filling / Piling", row.get("piler_assigned"),
                                         f"🎂 {row.order_id} ({disp(row.get('customer_name'))}) has finished baking and is ready to pile.")
                    notify_cake_finished(row.order_id,
                                         f"🎂 {row.order_id} ({disp(row.get('flavours'))}) is out of the oven and is ready to pile / assemble.",
                                         mixers=row.get("mixer_assigned"), oven_crew=row.get("oven_person_assigned"),
                                         assemblers=row.get("piler_assigned"))
                st.rerun()
            with b:
                issue_form("bake", "Baking", "Baking Check", "Baking Correction Required", "Baking", row, row.get("baker_assigned"))
    with tab_correction:
        row = select_order(filter_orders(df,["Baking Correction Required"]), "bake_corr")
        if row is not None:
            order_card(row, [("Issue", row.get("issue_notes"))], show_image=False)
            by = st.text_input("Corrected by", value=disp(row.get("baker_assigned")), key="bake_by3")
            in_batch = disp(row.get("baking_batch_number")) not in ("—", "", "nan", "None")
            if in_batch:
                st.caption(f"This cake belongs to batch {disp(row.get('baking_batch_number'))} — once corrected it goes back "
                           "onto the Batch Board to be ticked off with the rest.")
            if st.button("🔁 Correction Complete — Resubmit Baking Check", width='stretch'):
                update_order(row.order_id, {"workflow_status":"Baking", "current_owner":"Baking", "next_action":"Resubmitted for baking check"}, by, "Baking Correction Complete", "Baking")
                if in_batch:
                    reopen_batch_cake(row.order_id, by)
                st.rerun()

    with tab_cakeinv:
        st.markdown("### Extra Cake Layers for the Day — Assigned to You")
        extra = load_table("extra_baking_assignments")
        pending_extra = extra[extra["assignment_status"].isin(["Assigned","In Progress"])] if not extra.empty else extra
        table(pending_extra, ["id","plan_date","flavour","cake_size_value","cake_shape","layers_per_cake","cake_units","total_layers","assigned_baker","reason","assignment_status"])
        if not pending_extra.empty:
            assignment_id = st.selectbox("Select assignment to complete", pending_extra["id"].tolist(), key="extra_complete_id")
            selected_extra = pending_extra[pending_extra["id"] == assignment_id].iloc[0]
            by_extra = st.text_input("Completed by", value=selected_extra.assigned_baker, key="extra_complete_by")
            storage_extra = st.text_input("Storage location", value="Bakery", key="extra_storage")
            if st.button("✅ Complete Extra Baking → Add to Inventory", width='stretch'):
                with connect() as conn:
                    cur = conn.execute("""INSERT INTO baked_cake_inventory(date_baked,flavour,cake_size_value,cake_size_unit,cake_shape,
                                        number_of_layers,quantity_available,layers_available,baker,storage_location,inventory_status,created_at)
                                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                                       (str(date.today()), selected_extra.flavour, selected_extra.cake_size_value, "Inches",
                                        selected_extra.cake_shape, int(selected_extra.layers_per_cake), int(selected_extra.cake_units),
                                        int(selected_extra.total_layers), by_extra, storage_extra, "Available", now_iso()))
                    inv_id = cur.lastrowid
                    conn.execute("UPDATE extra_baking_assignments SET assignment_status='Completed', completed_at=?, inventory_record_id=? WHERE id=?",
                                 (now_iso(), inv_id, int(assignment_id)))
                    conn.commit()
                st.success("Extra baking completed and added to dated baked cake inventory.")
                st.rerun()

        st.markdown("### Manual Inventory Entry")
        a,b,c,d = st.columns(4)
        baked_date = a.date_input("Date baked", value=date.today(), key="inv_date")
        inv_flavour = b.text_input("Flavour", key="inv_flavour")
        inv_size = c.number_input("Size (inches)", min_value=0.0, step=0.5, value=8.0, key="inv_size")
        inv_shape = d.selectbox("Shape", ["Round","Rectangle","Square","Heart","Custom"], key="inv_shape")
        a,b,c,d = st.columns(4)
        inv_layers = a.number_input("Layers", min_value=1, step=1, value=3, key="inv_layers")
        inv_qty = b.number_input("Quantity available", min_value=1, step=1, value=1, key="inv_qty")
        inv_baker = c.selectbox("Baker", FALLBACK_BAKERS, key="inv_baker")
        storage = d.text_input("Storage location", value="Bakery", key="inv_storage")
        if st.button("Add to Baked Cake Inventory", width='stretch'):
            if not inv_flavour.strip():
                st.error("Enter the flavour.")
            else:
                with connect() as conn:
                    conn.execute("""INSERT INTO baked_cake_inventory(date_baked,flavour,cake_size_value,cake_size_unit,cake_shape,
                                    number_of_layers,quantity_available,layers_available,baker,storage_location,inventory_status,created_at)
                                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                                 (str(baked_date),inv_flavour.strip(),inv_size,"Inches",inv_shape,inv_layers,inv_qty,
                                  int(inv_layers)*int(inv_qty),inv_baker,storage,"Available",now_iso()))
                    conn.commit()
                st.success("Extra baked cake inventory recorded."); st.rerun()
        inv = load_table("baked_cake_inventory")
        table(inv.sort_values("date_baked", ascending=False) if not inv.empty else inv,
              ["id","date_baked","flavour","cake_size_value","cake_shape","number_of_layers","quantity_available","baker","storage_location","inventory_status","reserved_order_id"])

    with tab_cookieinv:
        st.markdown("### 🍪 Baked Cookie Inventory")
        st.caption("Cookies baked ahead of time and kept on the shelf. Customer Care sells straight from this when fulfilling a cookie order.")
        a,b,c,d = st.columns(4)
        cookie_date = a.date_input("Date baked", value=date.today(), key="cookie_inv_date")
        cookie_flavour = b.text_input("Flavour", key="cookie_inv_flavour")
        cookie_size = c.selectbox("Size", ["Small", "Medium", "Big"], key="cookie_inv_size")
        cookie_qty = d.number_input("Quantity baked", min_value=1, step=1, value=20, key="cookie_inv_qty")
        e,f = st.columns(2)
        cookie_baker = e.selectbox("Baker", FALLBACK_BAKERS, key="cookie_inv_baker")
        cookie_storage = f.text_input("Storage location", value="Bakery", key="cookie_inv_storage")
        if st.button("Add to Baked Cookie Inventory", width='stretch', key="cookie_inv_add"):
            if not cookie_flavour.strip():
                st.error("Enter the flavour.")
            else:
                with connect() as conn:
                    conn.execute("""INSERT INTO baked_cookie_inventory(date_baked,flavour,size_category,quantity_available,baker,storage_location,inventory_status,created_at)
                                    VALUES(?,?,?,?,?,?,?,?)""",
                                 (str(cookie_date), cookie_flavour.strip(), cookie_size, int(cookie_qty), cookie_baker, cookie_storage, "Available", now_iso()))
                    conn.commit()
                st.success("Cookie batch added to inventory."); st.rerun()
        cookie_inv_all = load_table("baked_cookie_inventory")
        table(cookie_inv_all.sort_values("date_baked", ascending=False) if not cookie_inv_all.empty else cookie_inv_all,
              ["id","date_baked","flavour","size_category","quantity_available","baker","storage_location","inventory_status","reserved_order_id"])

    with tab_oven:
        st.markdown("### 🌡️ Oven Temperature & Timing Log")
        st.caption("Start temperature is recorded when baking begins; final temperature when the baking check passes — so we can spot patterns that affect taste and texture.")
        logs = load_table("oven_logs")
        if logs.empty:
            st.info("No oven log entries yet.")
        else:
            display_logs = logs.copy()
            start_dt = pd.to_datetime(display_logs["oven_start_at"], errors="coerce")
            stop_dt = pd.to_datetime(display_logs["oven_stop_at"], errors="coerce")
            display_logs["duration_minutes"] = ((stop_dt - start_dt).dt.total_seconds() / 60).round(0)
            display_logs["temp_change_c"] = pd.to_numeric(display_logs["stop_temp_c"], errors="coerce") - pd.to_numeric(display_logs["start_temp_c"], errors="coerce")
            filter_date = st.date_input("Filter by date", value=date.today(), key="oven_log_date")
            day_logs = display_logs[start_dt.dt.date == filter_date] if not display_logs.empty else display_logs
            st.caption(f"{len(day_logs)} oven session(s) on {filter_date.strftime('%d %b %Y')}")
            table(day_logs.sort_values("oven_start_at", ascending=False),
                  ["order_id", "flavour", "product_type", "start_temp_c", "stop_temp_c", "temp_change_c",
                   "oven_start_at", "oven_stop_at", "duration_minutes", "recorded_by_start", "recorded_by_stop"])
            st.markdown("#### All-Time Log")
            table(display_logs.sort_values("oven_start_at", ascending=False).head(100),
                  ["order_id", "flavour", "product_type", "start_temp_c", "stop_temp_c", "temp_change_c",
                   "oven_start_at", "oven_stop_at", "duration_minutes", "recorded_by_start", "recorded_by_stop"])

    with tab_batch:
        st.markdown("### 🧮 Batch Board")
        st.caption("Everything Production Planning has grouped for you — flavour, size and layer totals only, no customer names. "
                   "Start the batch once, then tick each cake off as it comes out of the oven. Every ticked cake moves to the "
                   "next stage on its own; the rest of the batch keeps going.")

        batches = load_table("baking_batches")
        active_board = batches[batches["status"].isin(["Pending", "Baking"])].copy() if not batches.empty else batches
        me = st.session_state.get("staff_name", "").strip()
        show_all = st.checkbox("Show batches assigned to everyone", value=bool(st.session_state.get("is_hod")),
                               key="batch_show_all",
                               help="Off by default so you only see the batches you were given.")
        if not active_board.empty and me and not show_all:
            crew_blob = (active_board["assigned_baker"].fillna("") + " | " +
                         active_board["mixer_assigned"].fillna("") + " | " +
                         active_board["oven_person_assigned"].fillna("")).str.lower()
            mine_board = active_board[crew_blob.str.contains(me.lower(), regex=False)]
            if mine_board.empty and not active_board.empty:
                st.info("No batches assigned to you right now — tick 'Show batches assigned to everyone' to see the rest.")
            active_board = mine_board

        if active_board.empty:
            st.info("No batches on the board.")
        else:
            progress = batch_progress_map()
            board_view = active_board.copy()
            board_view["cakes_baked"] = board_view["id"].apply(
                lambda i: f"{progress.get(int(i), (0, 0, 0))[0]} / {progress.get(int(i), (0, 0, 0))[2]}")
            board_view["flagged"] = board_view["id"].apply(lambda i: progress.get(int(i), (0, 0, 0))[1])
            table(board_view.sort_values(["batch_date", "flavour", "cake_size_value"]),
                  ["batch_date", "batch_number", "product_type", "flavour", "cake_size_value", "cake_shape",
                   "total_layers_requested", "cakes_baked", "flagged", "assigned_baker",
                   "mixer_assigned", "oven_person_assigned", "status"])

            pick_batch = st.selectbox("Select a batch to work on", active_board["batch_number"].tolist(), key="batch_board_pick")
            brow = active_board[active_board["batch_number"] == pick_batch].iloc[0]
            st.markdown(f"#### Batch {brow['batch_number']} — {brow['flavour']}, {brow['cake_size_value']:g}\" {brow['cake_shape']}")
            st.info(f"**Total layers to bake: {int(brow['total_layers_requested'])}** · Baker: {first_name(brow.get('assigned_baker'))} · "
                    f"Mixer: {first_name(brow.get('mixer_assigned')) or '—'} · Oven: {first_name(brow.get('oven_person_assigned')) or '—'}")

            linked = load_table("baking_batch_orders")
            linked = linked[linked["batch_id"] == brow["id"]].copy() if not linked.empty else linked
            if not linked.empty:
                linked["baked_status"] = linked["baked_status"].fillna("Pending")
            pending_in_batch = linked[linked["baked_status"] == "Pending"] if not linked.empty else linked
            done_in_batch = linked[linked["baked_status"] == "Baked"] if not linked.empty else linked
            issue_in_batch = linked[linked["baked_status"] == "Issue"] if not linked.empty else linked
            st.caption(f"This batch covers {len(linked)} cake(s) — "
                       f"{int(linked['layers_needed'].sum()) if not linked.empty else 0} layers combined.")
            if len(linked):
                st.progress(len(done_in_batch) / len(linked),
                            text=f"{len(done_in_batch)} of {len(linked)} cakes baked"
                                 + (f" · {len(issue_in_batch)} flagged for re-bake" if len(issue_in_batch) else ""))

            class _BatchRow:
                order_id = brow["batch_number"]
                def get(self, k, default=None):
                    return default

            st.markdown("##### Materials for the Whole Batch")
            st.caption("Log the mix once for the batch — not per cake.")
            has_batch_materials = render_stage_material_planning("Baking", _BatchRow(), brow.get("assigned_baker"))
            logged_batch = load_table("stage_material_usage")
            has_batch_materials = bool(has_batch_materials) or (
                not logged_batch.empty and not logged_batch[(logged_batch["order_id"] == brow["batch_number"]) &
                                                            (logged_batch["stage"] == "Baking")].empty)

            by = st.text_input("Updated by", value=first_name(brow.get("assigned_baker")) or me, key="batch_by")

            if brow["status"] == "Pending":
                if not has_batch_materials:
                    st.caption("The Start Baking button unlocks once at least one material has been logged above.")
                start_temp_batch = st.number_input("Oven Start Temperature (°C)", min_value=0, max_value=300,
                                                    value=180, step=5, key="batch_start_temp")
                if st.button("▶️ Start Baking This Batch", width='stretch', key="batch_start_btn",
                             disabled=not has_batch_materials):
                    moved = start_baking_batch(brow, by, start_temp_batch)
                    st.success(f"Batch started — {moved} cake(s) are now marked as baking and visible to Piling's incoming board.")
                    st.rerun()
            else:
                elapsed_batch = minutes_elapsed_since(brow.get("baking_started_at"))
                required_batch = MINUTES_FULL_CAKE_BAKING
                time_ok = elapsed_batch is None or elapsed_batch >= required_batch
                if elapsed_batch is not None:
                    if time_ok:
                        st.success(f"✅ Minimum baking time of {required_batch} minutes met ({elapsed_batch:.0f} min in the oven).")
                    else:
                        st.warning(f"⏳ In the oven {elapsed_batch:.0f} of the {required_batch} minutes needed — about "
                                   f"{required_batch - elapsed_batch:.0f} more minute(s) before cakes can be ticked off.")
                override = False
                if not time_ok:
                    override = st.checkbox("Override the minimum baking time (a cake genuinely came out early)",
                                            key="batch_time_override")
                    if override:
                        st.caption("This override is recorded in the audit trail against your name.")
                can_tick = time_ok or override

                stop_temp_batch = st.number_input("Oven Stop / Final Temperature (°C)", min_value=0, max_value=300,
                                                   value=180, step=5, key="batch_stop_temp",
                                                   help="Recorded on the oven log when the last cake in the batch is ticked off.")

                st.markdown("##### ✅ Tick Off Each Cake As It Comes Out")
                st.caption("Each cake moves forward on its own the moment it's ticked — it doesn't wait for the rest of the batch. "
                           "Baking only needs flavour, size and layers, so this works whether or not the design is finalised yet.")

                orders_df = load_orders()
                if pending_in_batch.empty:
                    st.success("Every cake in this batch has been ticked off.")
                else:
                    if len(pending_in_batch) > 1:
                        if st.button(f"✅ All {len(pending_in_batch)} remaining cakes came out — tick them all",
                                     key="batch_tick_all", width='stretch', disabled=not can_tick):
                            msgs = []
                            for _, lrow in pending_in_batch.iterrows():
                                res = complete_batch_cake(brow, lrow, by, int(lrow["layers_needed"]), stop_temp_batch)
                                msgs.append(res["message"])
                            if override:
                                audit_log(None, "Baking Time Override", "Baking",
                                          f"Batch {brow['batch_number']} ticked off before the {required_batch}-minute minimum.", by)
                            st.success(" · ".join(msgs[-3:]))
                            st.rerun()
                        st.divider()

                    for _, lrow in pending_in_batch.iterrows():
                        spec, _order_row = batch_cake_spec(orders_df, lrow["order_id"])
                        with st.container(border=True):
                            st.markdown(f"**{lrow['order_id']}** — {spec} — needs **{int(lrow['layers_needed'])} layers**")
                            a, b = st.columns([2, 1])
                            actual_this = a.number_input("Layers actually baked for this cake", min_value=0, step=1,
                                                          value=int(lrow["layers_needed"]),
                                                          key=f"batch_tick_qty_{lrow['id']}")
                            if b.button("✅ Mark This Cake Baked", key=f"batch_tick_btn_{lrow['id']}",
                                        width='stretch', disabled=not can_tick):
                                res = complete_batch_cake(brow, lrow, by, actual_this, stop_temp_batch)
                                if override:
                                    audit_log(lrow["order_id"], "Baking Time Override", "Baking",
                                              f"Ticked off before the {required_batch}-minute minimum in batch {brow['batch_number']}.", by)
                                if res["waiting"]:
                                    st.info(res["message"])
                                else:
                                    st.success(res["message"])
                                if res["batch_done"]:
                                    if res["total_actual"] < int(brow["total_layers_requested"]):
                                        st.warning(f"⚠️ Batch finished with {res['total_actual']} of "
                                                   f"{int(brow['total_layers_requested'])} requested layers — "
                                                   "flag this shortfall to Production Planning.")
                                    else:
                                        st.success(f"Batch {brow['batch_number']} fully complete — all cakes baked and moved on.")
                                st.rerun()
                            with st.expander("Something went wrong with this one"):
                                issue_notes = st.text_area("What happened?", key=f"batch_issue_notes_{lrow['id']}",
                                                            placeholder="e.g. sunk in the middle, needs a re-bake")
                                if st.button("⚠️ Flag This Cake for Re-bake", key=f"batch_issue_btn_{lrow['id']}",
                                             width='stretch'):
                                    if not issue_notes.strip():
                                        st.error("Please say what went wrong.")
                                    else:
                                        flag_batch_cake_issue(brow, lrow, by, issue_notes.strip())
                                        st.warning(f"{lrow['order_id']} moved to Correction Required — the rest of the batch is unaffected.")
                                        st.rerun()

                if not done_in_batch.empty:
                    with st.expander(f"Already baked in this batch ({len(done_in_batch)})"):
                        table(done_in_batch, ["order_id", "layers_needed", "actual_layers_baked", "baked_at", "baked_by"])
                if not issue_in_batch.empty:
                    with st.expander(f"Flagged for re-bake ({len(issue_in_batch)})"):
                        st.caption("These sit in the 'Correction Required' tab. Once corrected they come back onto this board.")
                        table(issue_in_batch, ["order_id", "layers_needed", "baked_at", "baked_by"])

    with tab_finished:
        render_finished_work_tab("Baking")

    with tab_gallery:
        render_order_gallery(df, "🖼️ All Orders — Images & Copyable Details")


def render_piling():
    page_header("🎂 Filling / Piling", "Accept baked cakes, pile to correct height, and send to Covering.")
    view_mode = st.radio("View", ["📷 Simple View", "📋 Full View"], key="pile_view_mode", horizontal=True,
                         index=(0 if st.session_state.get("pile_view_mode", "📷 Simple View") == "📷 Simple View" else 1))
    if view_mode == "📷 Simple View":
        render_simple_view_live("Filling / Piling", "piler_assigned", "Piler", "Piling Incoming", "Piling",
                            "Covering Incoming", "Coating / Covering", "coverer_assigned", "Coverer to check piling and accept",
                            "Piling Complete → Send to Covering", "piling_started_at", "piling_completed_at",
                            "Filling / Piling", materials_required=False)
        return
    df = load_orders()
    render_hod_overview("Filling / Piling", df)
    t0,t1,t2,t3,t4,t5,t6,t7 = st.tabs(["📅 Incoming Workload", "Incoming from Baking", "In Progress", "Correction Required",
                                  "End-of-Day Layer Reconciliation", "📋 End-of-Day Accountability", "✅ Finished Work", "🖼️ All Orders"])
    with t0:
        pre_piling_statuses = ["Production Planned", "Baking", "Baking Correction Required"]
        render_incoming_workload_forecast(df, "piler_assigned", "Piler", pre_piling_statuses, "Incoming from Baking")
    with t1:
        incoming_q = filter_orders(df,["Piling Incoming"])
        render_queue_table(incoming_q, "Cakes Incoming From Baking", ["piler_assigned", "baking_batch_number", "decorator_assigned", "icing_type"])
        row = select_order(incoming_q, "pile_in")
        if row is not None:
            order_card(row, [("Baker", row.get("baker_assigned")), ("Piler", row.get("piler_assigned")), ("🧮 Batch Number", row.get("baking_batch_number"))])
            may_act = can_act_on(row, "piler_assigned")
            if not may_act:
                st.info(f"👀 Viewing only — this job is assigned to **{first_name(row.get('piler_assigned'))}**.")
            piling_materials_ready = render_stage_material_planning("Filling / Piling", row, row.get("piler_assigned"))
            st.caption("Logging materials here is optional per cake — you can also account for everything used today in one go at the end of your shift, in the \"📋 End-of-Day Accountability\" tab.")
            by = st.text_input("Checked by", value=disp(row.get("piler_assigned")) if disp(row.get("piler_assigned")) != "—" else st.session_state.get("staff_name",""), key="pile_by1")
            a,b = st.columns(2)
            if disp(row.get("inventory_reservation_id")) != "—":
                st.markdown("### Inventory Layer Usage")
                st.caption("For reserved baked inventory, Piler confirms layers used. This reduces dated layer inventory.")
                layers_used = st.number_input("Layers used for this cake", min_value=1, step=1, value=int(row.get("final_approved_layers") or row.get("number_of_layers") or 1), key="pile_layers_used")
                layer_notes = st.text_input("Layer usage notes", key="pile_layer_notes")
            else:
                layers_used = None
                layer_notes = ""
            if a.button("✅ Accept for Piling", width='stretch', disabled=not may_act):
                if disp(row.get("inventory_reservation_id")) != "—":
                    record_layer_usage(int(row.get("inventory_reservation_id")), row.order_id, "Filling / Piling", int(layers_used), by, layer_notes)
                insert_stage_check(row.order_id,"Baking","Piling",by,"Passed")
                update_order(row.order_id, {"workflow_status":"Piling", "current_owner":"Filling / Piling", "next_action":"Pile and submit to Covering", "piler_assigned": by}, by, "Piling Accepted", "Piling")
                st.rerun()
            with b:
                if may_act:
                    issue_form("pile_in", "Baking", "Piling", "Baking Correction Required", "Baking", row, row.get("baker_assigned"))
    with t2:
        prog_q = filter_orders(df,["Piling"])
        render_queue_table(prog_q, "Cakes Currently Being Piled", ["piler_assigned", "decorator_assigned", "icing_type"])
        row = select_order(prog_q, "pile_prog")
        if row is not None:
            order_card(row)
            may_act = can_act_on(row, "piler_assigned")
            if not may_act:
                st.info(f"👀 Viewing only — this job is assigned to **{first_name(row.get('piler_assigned'))}**.")
            _,_pilers_ma,_,_,_ = staff_lists()
            render_multi_assign(row, "piler_assigned", "Piler", _pilers_ma, f"pile_{row.order_id}")
            piling_materials_ready = render_stage_material_planning("Filling / Piling", row, row.get("piler_assigned"))
            st.caption("Optional here too — account for the day's materials all at once in \"📋 End-of-Day Accountability\" if that's easier.")
            by = st.text_input("Updated by", value=disp(row.get("piler_assigned")), key="pile_by2")
            if st.button("✅ Piling Complete → Send to Covering Check", width='stretch', disabled=not may_act):
                update_order(row.order_id, {"workflow_status":"Covering Incoming", "current_owner":"Coating / Covering", "next_action":"Coverer to check piling and accept"}, by, "Piling Submitted", "Piling")
                create_notification(row.order_id, "Coating / Covering", row.get("coverer_assigned"),
                                     f"🎂 {row.order_id} ({disp(row.get('customer_name'))}) has finished piling and is ready to cover.")
                st.rerun()
    with t3:
        row = select_order(filter_orders(df,["Piling Correction Required"]), "pile_corr")
        if row is not None:
            order_card(row, [("Issue", row.get("issue_notes"))])
            may_act = can_act_on(row, "piler_assigned")
            if not may_act:
                st.info(f"👀 Viewing only — this job is assigned to **{first_name(row.get('piler_assigned'))}**.")
            by = st.text_input("Corrected by", value=disp(row.get("piler_assigned")), key="pile_by3")
            if st.button("🔁 Correction Complete — Resubmit to Covering", width='stretch', disabled=not may_act):
                update_order(row.order_id, {"workflow_status":"Covering Incoming", "current_owner":"Coating / Covering", "next_action":"Resubmitted for covering acceptance"}, by, "Piling Correction Complete", "Piling")
                st.rerun()

    with t4:
        st.markdown("### End-of-Day Layer Reconciliation")
        inv = load_table("baked_cake_inventory")
        usage = load_table("layer_inventory_usage")
        total_layers = int(inv["layers_available"].fillna(0).sum()) if not inv.empty and "layers_available" in inv.columns else 0
        today_str = str(date.today())
        used_today = int(usage[usage["used_at"].astype(str).str.startswith(today_str)]["layers_used"].sum()) if not usage.empty else 0
        st.metric("System Closing Layers Available", total_layers)
        st.metric("Layers Used Today", used_today)
        a,b,c = st.columns(3)
        opening = a.number_input("Opening layers", min_value=0, step=1, value=total_layers + used_today)
        used = b.number_input("Layers used", min_value=0, step=1, value=used_today)
        closing = c.number_input("Closing layers", min_value=0, step=1, value=total_layers)
        a,b = st.columns(2)
        procurement_balance = a.number_input("Procurement balance", min_value=0, step=1, value=closing)
        confirmed_by = b.text_input("Confirmed by", value="Piler")
        comments = st.text_area("Comments")
        if st.button("Confirm End-of-Day Layer Balance", width='stretch'):
            with connect() as conn:
                conn.execute("""INSERT INTO layer_inventory_reconciliation(reconciliation_date,confirmed_by,opening_layers,layers_used,
                                closing_layers,procurement_balance,comments,confirmed_at)
                                VALUES(?,?,?,?,?,?,?,?)""",
                             (today_str, confirmed_by, int(opening), int(used), int(closing), int(procurement_balance), comments, now_iso()))
                conn.commit()
            st.success("Layer inventory reconciliation saved.")
            st.rerun()
        rec = load_table("layer_inventory_reconciliation")
        table(rec.sort_values("confirmed_at", ascending=False).head(20) if not rec.empty else rec,
              ["reconciliation_date","confirmed_by","opening_layers","layers_used","closing_layers","procurement_balance","comments","confirmed_at"])

    with t5:
        st.markdown("### 📋 End-of-Day Accountability")
        st.caption("Since measuring isn't required per cake anymore, use this at the end of your shift to account for everything "
                   "used across all the cakes you piled today — cake boards, buttercream, or anything else drawn from stores.")
        acc_by = st.text_input("Piler name", value=st.session_state.get("staff_name", ""), key="pile_acc_by")
        acc_date = st.date_input("Date", value=date.today(), key="pile_acc_date")
        st.markdown("#### Add an item")
        a, b, c = st.columns(3)
        acc_item = a.selectbox("Item", STAGE_MATERIALS.get("Filling / Piling", ["Other"]), key="pile_acc_item")
        if acc_item == "Other":
            acc_item = a.text_input("Specify item", key="pile_acc_item_other")
        acc_qty = b.number_input("Quantity used today", min_value=0.0, step=1.0, key="pile_acc_qty")
        acc_unit = c.selectbox("Unit", ["pieces", "kg", "grams", "litres", "ml", "trays", "boxes"], key="pile_acc_unit")
        if st.button("➕ Add to Today's Accountability", key="pile_acc_add", width='stretch'):
            if acc_item and acc_qty > 0:
                with connect() as conn:
                    conn.execute("""INSERT INTO piler_daily_accountability(piler_name, accountability_date, item_name,
                                    quantity_used, unit, recorded_at) VALUES(?,?,?,?,?,?)""",
                                 (acc_by, str(acc_date), acc_item, acc_qty, acc_unit, now_iso()))
                    conn.commit()
                st.success(f"Added {acc_qty:g} {acc_unit} of {acc_item} for {acc_date}.")
                st.rerun()
            else:
                st.error("Pick an item and enter a quantity greater than zero.")
        st.markdown(f"#### {acc_by or 'Your'} accountability for {acc_date.strftime('%d %b %Y')}")
        acc_records = load_table("piler_daily_accountability")
        if not acc_records.empty:
            today_mine = acc_records[(acc_records["piler_name"] == acc_by) & (acc_records["accountability_date"] == str(acc_date))]
        else:
            today_mine = acc_records
        table(today_mine.sort_values("recorded_at", ascending=False) if not today_mine.empty else today_mine,
              ["item_name", "quantity_used", "unit", "recorded_at"])
        st.markdown("#### 👑 HOD view — everyone's accountability for a chosen day")
        if st.session_state.get("is_hod"):
            hod_acc_date = st.date_input("Day to review", value=date.today(), key="pile_acc_hod_date")
            all_for_day = acc_records[acc_records["accountability_date"] == str(hod_acc_date)] if not acc_records.empty else acc_records
            table(all_for_day.sort_values(["piler_name", "recorded_at"]) if not all_for_day.empty else all_for_day,
                  ["piler_name", "item_name", "quantity_used", "unit", "recorded_at"])
        else:
            st.caption("Visible to your Head of Department.")

    with t6:
        render_finished_work_tab("Filling / Piling")

    with t7:
        render_order_gallery(df, "🖼️ All Orders — Images & Copyable Details")


def render_covering():
    page_header("🧁 Coating / Covering", "Check piling/height, cover cake, then send to Decoration.")
    view_mode = st.radio("View", ["📷 Simple View", "📋 Full View"], key="cov_view_mode", horizontal=True,
                         index=(0 if st.session_state.get("cov_view_mode", "📷 Simple View") == "📷 Simple View" else 1))
    if view_mode == "📷 Simple View":
        render_simple_view_live("Coating / Covering", "coverer_assigned", "Coverer", "Covering Incoming", "Covering",
                            "Decorating Incoming", "Decoration", "decorator_assigned", "Decorator to check covering and accept",
                            "Covering Complete → Send to Decoration", "covering_started_at", "covering_completed_at",
                            "Coating / Covering", materials_required=True)
        return
    df = load_orders()
    render_hod_overview("Coating / Covering", df)
    t0,t1,t2,t3,t4,t5 = st.tabs(["📅 Incoming Workload", "Incoming from Piling", "In Progress", "Correction Required", "✅ Finished Work", "🖼️ All Orders"])
    with t0:
        pre_covering_statuses = ["Production Planned", "Baking", "Baking Correction Required",
                                  "Piling Incoming", "Piling", "Piling Correction Required"]
        render_incoming_workload_forecast(df, "coverer_assigned", "Coverer", pre_covering_statuses, "Incoming from Piling")
    with t1:
        incoming_q = filter_orders(df,["Covering Incoming"])
        render_queue_table(incoming_q, "Cakes Incoming From Piling", ["coverer_assigned", "decorator_assigned", "icing_type"])
        row = select_order(incoming_q, "cov_in")
        if row is not None:
            order_card(row, [("Piler", row.get("piler_assigned")), ("Coverer", row.get("coverer_assigned"))])
            may_act = can_act_on(row, "coverer_assigned")
            if not may_act:
                st.info(f"👀 Viewing only — this job is assigned to **{first_name(row.get('coverer_assigned'))}**.")
            covering_materials_ready = render_stage_material_planning("Coating / Covering", row, row.get("coverer_assigned"))
            if not covering_materials_ready:
                st.warning("Enter covering materials before accepting and starting this job.")
            by = st.text_input("Checked by", value=disp(row.get("coverer_assigned")) if disp(row.get("coverer_assigned")) != "—" else st.session_state.get("staff_name",""), key="cov_by1")
            a,b = st.columns(2)
            if a.button("✅ Piling Accepted → Start Covering", width='stretch', disabled=(not may_act or not covering_materials_ready)):
                insert_stage_check(row.order_id,"Piling","Covering",by,"Passed")
                update_order(row.order_id, {"workflow_status":"Covering", "current_owner":"Coating / Covering", "next_action":"Cover and submit to Decoration", "coverer_assigned": by}, by, "Piling Accepted by Coverer", "Covering")
                st.rerun()
            with b:
                if may_act:
                    issue_form("cov_in", "Piling", "Covering", "Piling Correction Required", "Filling / Piling", row, row.get("piler_assigned"))
    with t2:
        prog_q = filter_orders(df,["Covering"])
        render_queue_table(prog_q, "Cakes Currently Being Covered", ["coverer_assigned", "decorator_assigned", "icing_type"])
        row = select_order(prog_q, "cov_prog")
        if row is not None:
            order_card(row)
            may_act = can_act_on(row, "coverer_assigned")
            if not may_act:
                st.info(f"👀 Viewing only — this job is assigned to **{first_name(row.get('coverer_assigned'))}**.")
            _,_,_coverers_ma,decorator_names,_ = staff_lists()
            render_multi_assign(row, "coverer_assigned", "Coverer", _coverers_ma, f"cov_{row.order_id}")
            covering_materials_ready = render_stage_material_planning("Coating / Covering", row, row.get("coverer_assigned"))
            if not covering_materials_ready:
                st.warning("Record covering materials before completing this job.")
            by = st.text_input("Updated by", value=disp(row.get("coverer_assigned")), key="cov_by2")
            assign_to_list = st.multiselect(
                "Assign to which decorator(s)?",
                decorator_names,
                key="cov_assign_decorator",
                help="Pick more than one for bulk orders that need several decorators. Everyone picked gets a direct alert — this is how a job stops sitting unclaimed in a shared queue.")
            if st.button("✅ Covering Complete → Send to Decorator Check", width='stretch', disabled=(not may_act or not covering_materials_ready)):
                assign_to = ", ".join(assign_to_list) if assign_to_list else ""
                update_order(row.order_id, {
                    "workflow_status": "Decorating Incoming", "current_owner": "Decoration",
                    "next_action": f"{assign_to} to check covering and accept", "decorator_assigned": assign_to,
                }, by, "Covering Submitted", "Covering")
                for dname in assign_to_list:
                    create_notification(row.order_id, "Decoration", dname,
                                         f"🧁 {row.order_id} ({disp(row.get('customer_name'))}) has finished Covering and is assigned to you — check it in 'Incoming from Covering'.")
                st.success(f"Sent to Decoration, assigned to {first_name(assign_to) if assign_to else 'nobody yet'}.")
                st.rerun()
    with t3:
        row = select_order(filter_orders(df,["Covering Correction Required"]), "cov_corr")
        if row is not None:
            order_card(row, [("Issue", row.get("issue_notes"))])
            may_act = can_act_on(row, "coverer_assigned")
            if not may_act:
                st.info(f"👀 Viewing only — this job is assigned to **{first_name(row.get('coverer_assigned'))}**.")
            by = st.text_input("Corrected by", value=disp(row.get("coverer_assigned")), key="cov_by3")
            if st.button("🔁 Correction Complete — Resubmit to Decoration", width='stretch', disabled=not may_act):
                update_order(row.order_id, {"workflow_status":"Decorating Incoming", "current_owner":"Decoration", "next_action":"Resubmitted for decorator acceptance"}, by, "Covering Correction Complete", "Covering")
                st.rerun()

    with t4:
        render_finished_work_tab("Coating / Covering")

    with t5:
        render_order_gallery(df, "🖼️ All Orders — Images & Copyable Details")


def render_design_innovation():
    page_header("🎨 Design & Innovation", "Keith's topper queue, automatically prioritized by topper target time.")
    st.markdown("## 💡 Creativity & Innovation Contributions")
    st.caption("Ideas submitted by anyone across the company — review, discuss, and mark progress here.")
    ideas = load_table("creativity_contributions")
    if ideas.empty:
        st.caption("No ideas submitted yet.")
    else:
        table(ideas.sort_values("submitted_at", ascending=False),
              ["id", "contributor_name", "department", "idea_title", "category", "status", "submitted_at"])
        pick_id = st.selectbox("Select an idea to review", ideas["id"].tolist(), key="idea_review_pick")
        idea_row = ideas[ideas["id"] == pick_id].iloc[0]
        st.markdown(f"**{idea_row['idea_title']}** — by {idea_row['contributor_name']} ({disp(idea_row.get('department'))})")
        st.write(idea_row["idea_description"])
        new_status = st.selectbox("Status", ["Submitted", "Under Review", "Approved", "Implemented", "Not Feasible"],
                                   index=["Submitted", "Under Review", "Approved", "Implemented", "Not Feasible"].index(idea_row["status"]) if idea_row["status"] in ["Submitted", "Under Review", "Approved", "Implemented", "Not Feasible"] else 0,
                                   key="idea_status_pick")
        review_notes = st.text_area("Review notes", value=disp(idea_row.get("review_notes")) if disp(idea_row.get("review_notes")) != "—" else "", key="idea_review_notes")
        reviewer = st.text_input("Reviewed by", value=st.session_state.get("staff_name", "Keith"), key="idea_reviewer")
        if st.button("Save Review", key="idea_save_review"):
            with connect() as conn:
                conn.execute("UPDATE creativity_contributions SET status=?, review_notes=?, reviewed_by=?, reviewed_at=? WHERE id=?",
                             (new_status, review_notes, reviewer, now_iso(), int(pick_id)))
                conn.commit()
            st.success("Review saved."); st.rerun()
    st.divider()

    df=load_orders()
    PRE_FINANCE_APPROVAL_STATUSES = ["Awaiting Payment Confirmation", "Awaiting Deposit", "Payment Approval Required", "Payment Hold"]
    q=df[(col(df,"topper_required")=="Yes") & (~df["workflow_status"].isin(PRE_FINANCE_APPROVAL_STATUSES))].copy()
    if q.empty:
        st.info("No topper tasks.")
    else:
        q["topper_urgency"]=q.apply(topper_urgency,axis=1)
        q["topper_target_display"]=q.apply(lambda r: topper_target_datetime(r).strftime("%Y-%m-%d %I:%M %p") if topper_target_datetime(r) is not None else "—",axis=1)
        rank={"⚠️ DELAYED / OVERDUE":0,"🚨 DUE NOW":1,"🟡 DUE SOON":2,"🟢 NORMAL TIME":3,"Completed":4}
        q["_rank"]=q["topper_urgency"].map(rank).fillna(9)
        q=q.sort_values(["_rank","due_date","expected_time"])
        a,b,c,d=st.columns(4)
        a.metric("Delayed",int((q["topper_urgency"]=="⚠️ DELAYED / OVERDUE").sum()))
        b.metric("Due Now",int((q["topper_urgency"]=="🚨 DUE NOW").sum()))
        c.metric("Due Soon",int((q["topper_urgency"]=="🟡 DUE SOON").sum()))
        d.metric("Normal Time",int((q["topper_urgency"]=="🟢 NORMAL TIME").sum()))
        st.markdown("### Topper Priority Queue")
        topper_cols = [c for c in ["topper_urgency","order_id","customer_name","topper_count","topper_1_wording","topper_2_wording","topper_3_wording","decorator_assigned","due_date","expected_time","topper_target_display","topper_status","topper_assigned_to"] if c in q.columns]
        st.dataframe(q[topper_cols],hide_index=True,width='stretch')
        active=q[~q["topper_status"].isin(["Ready","Received by Decorator"])]
        row=select_order(active,"topper_task") if not active.empty else None
        if row is None:
            st.success("All topper tasks are completed.")
        else:
            order_card(row,[("Number of Toppers",row.get("topper_count") or 1),
                            ("Topper 1 Words",row.get("topper_1_wording") or row.get("topper_wording")),
                            ("Topper 1 Notes",row.get("topper_1_notes") or row.get("topper_notes")),
                            ("Topper 2 Words",row.get("topper_2_wording")),
                            ("Topper 2 Notes",row.get("topper_2_notes")),
                            ("Topper 3 Words",row.get("topper_3_wording")),
                            ("Topper 3 Notes",row.get("topper_3_notes")),
                            ("Decorator",row.get("decorator_assigned")),("Urgency",topper_urgency(row)),("Status",row.get("topper_status"))])
            render_stage_material_planning("Design & Innovation", row, row.get("topper_assigned_to"), key_prefix="topper")
            by=st.text_input("Updated by",value=disp(row.get("topper_assigned_to")) if disp(row.get("topper_assigned_to"))!="—" else "Keith")
            a,b=st.columns(2)
            if a.button("🎨 Start Topper",width='stretch'):
                update_order(row.order_id,{"topper_status":"In Progress"},by,"Topper Started","Design & Innovation"); st.rerun()
            if b.button("✅ Topper Ready",width='stretch'):
                decorator=disp(row.get("decorator_assigned"))
                message=f"🎨 Your cake topper is ready for order {row.order_id} ({disp(row.get('customer_name'))}) — pick it up from {by}."
                update_order(row.order_id,{"topper_status":"Ready","topper_ready_at":now_iso(),"topper_pickup_note":message},by,"Topper Ready","Design & Innovation")
                create_notification(row.order_id,"Decoration",decorator,message)
                st.success(f"Topper ready. {decorator} notified."); st.rerun()

    st.divider()
    sq = df[(col(df,"sticker_required")=="Yes") & (~df["workflow_status"].isin(PRE_FINANCE_APPROVAL_STATUSES))].copy()
    if sq.empty:
        st.info("No sticker tasks.")
    else:
        st.markdown("### 🏷️ Sticker Priority Queue")
        sq_active = sq[~sq["sticker_status"].isin(["Ready", "Received by Decorator"])]
        sticker_cols = [c for c in ["order_id","customer_name","sticker_notes","decorator_assigned","due_date","expected_time","sticker_status","sticker_assigned_to"] if c in sq.columns]
        st.dataframe(sq[sticker_cols], hide_index=True, width='stretch')
        srow = select_order(sq_active, "sticker_task") if not sq_active.empty else None
        if srow is None:
            st.success("All sticker tasks are completed.")
        else:
            order_card(srow, [("Number of Stickers", srow.get("sticker_count") or 1),
                              ("Sticker Notes", srow.get("sticker_notes")),
                              ("Decorator", srow.get("decorator_assigned")), ("Status", srow.get("sticker_status"))])
            render_stage_material_planning("Design & Innovation", srow, srow.get("sticker_assigned_to"), key_prefix="sticker")
            sby = st.text_input("Updated by", value=disp(srow.get("sticker_assigned_to")) if disp(srow.get("sticker_assigned_to")) != "—" else "Doreen", key="sticker_updated_by")
            sa, sb = st.columns(2)
            if sa.button("🏷️ Start Sticker", width='stretch'):
                update_order(srow.order_id, {"sticker_status":"In Progress"}, sby, "Sticker Started", "Design & Innovation"); st.rerun()
            if sb.button("✅ Sticker Ready", width='stretch'):
                sdecorator = disp(srow.get("decorator_assigned"))
                smessage = f"🏷️ Sticker work is ready for order {srow.order_id} ({disp(srow.get('customer_name'))}) — pick up from {sby}."
                update_order(srow.order_id, {"sticker_status":"Ready", "sticker_ready_at":now_iso(), "sticker_pickup_note":smessage}, sby, "Sticker Ready", "Design & Innovation")
                create_notification(srow.order_id, "Decoration", sdecorator, smessage)
                st.success(f"Sticker ready. {sdecorator} notified."); st.rerun()

    st.divider()
    st.markdown("### 📋 Sent Work — Toppers")
    st.caption("Every topper sent out, when it was sent, who it went to, and whether they've acknowledged picking it up.")
    tsent = df[df["topper_status"].isin(["Ready", "Received by Decorator"])].copy() if not df.empty else df
    if tsent.empty:
        st.info("No toppers sent yet.")
    else:
        tsent["Acknowledged?"] = tsent["topper_status"].apply(lambda s: "✅ Yes" if s == "Received by Decorator" else "⏳ Not yet")
        tsent = tsent.rename(columns={"topper_ready_at": "Sent At", "decorator_assigned": "Sent To",
                                       "topper_received_by_decorator": "Acknowledged By", "topper_received_at": "Acknowledged At"})
        table(tsent.sort_values("Sent At", ascending=False),
              ["order_id", "customer_name", "Sent At", "Sent To", "Acknowledged?", "Acknowledged By", "Acknowledged At"])

    st.markdown("### 📋 Sent Work — Stickers")
    st.caption("Same idea for stickers — when each was sent, to whom, and whether it's been acknowledged.")
    ssent = df[df["sticker_status"].isin(["Ready", "Received by Decorator"])].copy() if not df.empty else df
    if ssent.empty:
        st.info("No stickers sent yet.")
    else:
        ssent["Acknowledged?"] = ssent["sticker_status"].apply(lambda s: "✅ Yes" if s == "Received by Decorator" else "⏳ Not yet")
        ssent = ssent.rename(columns={"sticker_ready_at": "Sent At", "decorator_assigned": "Sent To",
                                       "sticker_received_by_decorator": "Acknowledged By", "sticker_received_at": "Acknowledged At"})
        table(ssent.sort_values("Sent At", ascending=False),
              ["order_id", "customer_name", "Sent At", "Sent To", "Acknowledged?", "Acknowledged By", "Acknowledged At"])

    st.divider()
    render_order_gallery(df, "🖼️ All Orders — Images & Copyable Details")


MINUTES_DECORATION_DEFAULT = 30   # default minimum decoration time
MINUTES_DECORATION_FONDANT = 60   # fondant needs at least 1 hour
ICING_TYPES_NO_MINIMUM = {"Buttercream", "Whipped Cream"}


def decoration_minimum_minutes(row):
    icing = str(row.get("icing_type") or "")
    if icing in ICING_TYPES_NO_MINIMUM:
        return 0
    if icing == "Fondant":
        return MINUTES_DECORATION_FONDANT
    return MINUTES_DECORATION_DEFAULT


def render_finished_work_tab(department_label, staff_column=None):
    """Shows what's actually been finished — for the logged-in person by default, or the
    whole department for an HOD — searchable by day or by the current week. Built on the
    audit log that every update_order() call already writes to, so this needed no new
    tracking anywhere else. Once work moves off someone's active queue they lose sight of
    it entirely otherwise; this is what gives that visibility back."""
    st.markdown("### ✅ Finished Work")
    logs = load_table("audit_logs")
    orders = load_orders()
    if logs.empty:
        st.info("Nothing logged yet.")
        return
    logs = logs.copy()
    logs["performed_date"] = pd.to_datetime(logs["performed_at"], errors="coerce").dt.date
    is_hod = st.session_state.get("is_hod")
    my_name = st.session_state.get("staff_name", "").strip()
    view_mode = st.radio("Show", (["My work", "Whole department"] if is_hod else ["My work"]),
                          horizontal=True, key=f"finwork_view_{department_label}")
    if view_mode == "My work" and my_name:
        logs = logs[logs["performed_by"].astype(str).str.strip().str.lower() == my_name.lower()]
    range_mode = st.radio("Range", ["This week", "Pick a date"], horizontal=True, key=f"finwork_range_{department_label}")
    if range_mode == "This week":
        start_of_week = date.today() - timedelta(days=date.today().weekday())
        logs = logs[logs["performed_date"] >= start_of_week]
        st.caption(f"Showing {start_of_week.strftime('%A %d %b')} through today. Clears automatically once next week starts.")
    else:
        picked = st.date_input("Date", value=date.today(), key=f"finwork_date_{department_label}")
        logs = logs[logs["performed_date"] == picked]
    if logs.empty:
        st.info("No finished work in this range.")
        return
    merged = logs.merge(orders[["order_id", "customer_name", "product_type", "flavours"]] if not orders.empty else pd.DataFrame(columns=["order_id"]),
                         on="order_id", how="left")
    merged = merged.sort_values("performed_at", ascending=False)
    st.caption(f"{len(merged)} action(s) in this range.")
    table(merged, ["performed_at", "order_id", "customer_name", "product_type", "flavours", "action_type", "performed_by"])


def render_order_gallery(df, title="🖼️ All Active Orders"):
    """Mobile-safe order gallery.

    Older versions created an expander (and image widgets) for every active order.
    Streamlit executes the contents of collapsed expanders, so iPhones could receive
    a very large websocket message and Safari would sometimes report
    'Failed to process a WebSocket message / RangeError: index out of range'.

    Keep the full searchable list, but render media/details for ONE selected cake only.
    """
    st.markdown(f"### {title}")
    active = df[df["workflow_status"] != "Follow-up Done"] if not df.empty else df.iloc[0:0]
    if active.empty:
        st.info("No active orders right now.")
        return
    active = active.copy()
    active["_urgency_rank"] = (active["urgency_level"] == "Urgent").astype(int) if "urgency_level" in active.columns else 0
    active_sorted = active.sort_values(["_urgency_rank", "due_date", "expected_time"], ascending=[False, True, True])

    labels, by_label = [], {}
    for _, cake_row in active_sorted.iterrows():
        urgent_tag = "🚨 URGENT — " if str(cake_row.get("urgency_level")) == "Urgent" else ""
        decorator_raw = cake_row.get("decorator_assigned")
        decorator_people = [p.strip() for p in str(decorator_raw or "").replace(";", ",").split(",") if p.strip()]
        final_decorator = first_name(decorator_people[-1]) if decorator_people else "—"
        label = (f"{urgent_tag}{cake_row.get('order_id')} — {disp(cake_row.get('customer_name'))} — "
                 f"{disp(cake_row.get('flavours'))} — Due {disp(cake_row.get('due_date'))} {disp(cake_row.get('expected_time'))} — "
                 f"Decorator: {final_decorator} — {disp(cake_row.get('workflow_status'))}")
        labels.append(label)
        by_label[label] = cake_row

    st.caption(f"{len(labels)} active order(s). Search/select one cake below. Only that cake's image is loaded — safer and faster on iPhones.")
    selected_label = st.selectbox("Select an order to open", labels, key=f"gallery_pick_{re.sub(r'[^a-z0-9]+','_',title.lower())[:45]}")
    cake_row = by_label[selected_label]

    has_image = render_reference_images(cake_row)
    if not has_image:
        st.caption("No reference image was uploaded for this order.")
    can_see_price = st.session_state.get("department") in (
        "Customer Care", "Packaging", "Finance", "Procurement", "Owner / Admin"
    )
    bold_fields = [
        ("Order", cake_row.get('order_id')),
        ("Customer", f"{disp(cake_row.get('customer_name'))}  Phone: {disp(cake_row.get('customer_number'))}"),
        ("Product", disp(cake_row.get('product_type'))),
        ("Category", disp(cake_row.get('cake_category'))),
        ("Flavours", disp(cake_row.get('flavours'))),
        ("Size", f"{disp(cake_row.get('cake_size_value'))}\"  Shape: {disp(cake_row.get('cake_shape'))}"),
        ("Icing/Finish", disp(cake_row.get('icing_type'))),
        ("Design Notes", disp(cake_row.get('design_description'))),
    ]
    if can_see_price:
        bold_fields.append(("Price", f"{fmt_ugx(cake_row.get('price_ugx'))}  Balance: {fmt_ugx(cake_row.get('balance'))}"))
    bold_fields += [
        ("Due", f"{disp(cake_row.get('due_date'))} at {disp(cake_row.get('expected_time'))}"),
        ("Currently at", disp(cake_row.get('workflow_status'))),
        ("Urgency", disp(cake_row.get('urgency_level'))),
    ]
    for label_txt, value_txt in bold_fields:
        st.markdown(f"**{label_txt}:** {value_txt}")
    details_text = "\n".join(f"{label_txt}: {value_txt}" for label_txt, value_txt in bold_fields)
    st.caption("Tap the copy icon below to copy these details.")
    st.code(details_text, language=None)

def render_simple_view(department_label, staff_column, role_label, incoming_status, active_status,
                        next_status, next_owner, next_staff_column, next_action_text, completion_label,
                        started_field, completed_field, materials_stage, materials_required=True):
    """Fast, phone-first production queue.

    The queue is always sorted most urgent first and shown four cakes at a time, so
    Bakers/Pilers/Coverers/Decorators can move through work without hidden horizontal scrolling.
    Only the selected cake is rendered in full, which keeps cheap phones from loading every
    image and every order card at once.
    """
    my_name = st.session_state.get("staff_name", "").strip()
    is_hod = st.session_state.get("is_hod")

    # Phone-first CSS: large touch targets.  The rules are
    # intentionally scoped to this simple view so the admin/full dashboards stay unchanged.
    st.markdown("""
    <style>
    div[data-testid="stRadio"] div[role="radiogroup"] {
        display:flex !important;
        flex-wrap:nowrap !important;
        overflow-x:auto !important;
        overflow-y:hidden !important;
        gap:.45rem !important;
        padding:.25rem .05rem .65rem !important;
        scroll-snap-type:x proximity;
        -webkit-overflow-scrolling:touch;
    }
    div[data-testid="stRadio"] div[role="radiogroup"] > label {
        flex:0 0 auto !important;
        scroll-snap-align:start;
        min-height:46px;
        padding:.45rem .65rem !important;
        border:1px solid #D7C6DF;
        border-radius:10px;
        background:#F7F1FA;
        white-space:nowrap;
    }
    div[data-testid="stButton"] > button {
        min-height:54px !important;
        font-size:1.02rem !important;
        font-weight:700 !important;
        border-radius:10px !important;
    }
    @media (max-width: 640px) {
        .block-container {padding-left:.65rem !important; padding-right:.65rem !important; padding-top:.7rem !important;}
        div[data-testid="stImage"] img {width:100% !important; height:auto !important; border-radius:12px !important;}
        h3 {font-size:1.18rem !important;}
        p, li {font-size:1rem !important; line-height:1.45 !important;}
    }
    </style>
    """, unsafe_allow_html=True)

    df = load_orders()
    mine = df[df["workflow_status"].isin([incoming_status, active_status])] if not df.empty else df.iloc[0:0]
    if not is_hod and not mine.empty and staff_column in mine.columns:
        assigned = mine[staff_column].fillna("").astype(str)
        mine = mine[assigned.str.contains(my_name, case=False, na=False) | (assigned.str.strip() == "")]

    if mine.empty:
        st.success("🎉 Nothing waiting for you right now — you're all caught up!")
        return

    def _urgency_key(r):
        # Explicit urgent jobs lead the queue; within each group, earliest due date/time wins.
        urgent_rank = 0 if str(r.get("urgency_level") or "").strip().lower() == "urgent" else 1
        due = pd.to_datetime(r.get("due_date"), errors="coerce")
        due_rank = due if not pd.isna(due) else pd.Timestamp.max
        raw_time = str(r.get("expected_time") or "23:59").strip()
        try:
            time_rank = datetime.strptime(raw_time[:5], "%H:%M").time()
        except Exception:
            time_rank = dtime(23, 59)
        return (urgent_rank, due_rank, time_rank)

    mine = mine.copy()
    mine["_sort_key"] = mine.apply(_urgency_key, axis=1)
    mine = mine.sort_values("_sort_key", kind="stable").reset_index(drop=True)

    # A stable order-id selection is safer than an index: if another cake arrives while the
    # worker is viewing the page, their selected cake does not unexpectedly jump.
    queue_ids = mine["order_id"].astype(str).tolist()
    pick_key = f"simple_queue_pick_{department_label}"
    if st.session_state.get(pick_key) not in queue_ids:
        st.session_state[pick_key] = queue_ids[0]

    by_id = {str(r.get("order_id")): r for _, r in mine.iterrows()}

    def _queue_label(order_id):
        r = by_id[str(order_id)]
        urgent = "🚨 " if str(r.get("urgency_level") or "").strip().lower() == "urgent" else ""
        due = disp(r.get("due_date"))
        tm = disp(r.get("expected_time"))
        due_short = due[5:] if len(due) >= 10 and due[4:5] == "-" else due
        when = due_short + ((" " + tm) if tm != "—" else "")
        return f"{urgent}{order_id} • {when}"

    # Keep the production queue impossible to "lose" on small phones. Streamlit's horizontal
    # radio can preserve its scrollbar position after a rerun, which means the selected cake
    # may exist but be off-screen. Show four cakes at a time instead, with explicit page controls.
    # This is slightly less flashy than an endless swipe strip, but much safer for low-end phones.
    page_size = 4
    page_key = f"simple_queue_page_{department_label}"
    total_pages = max(1, (len(queue_ids) + page_size - 1) // page_size)

    # If the selected cake changed because of a notification / live refresh, move to the page
    # containing that cake so the worker always sees what the app says is selected.
    selected_now = str(st.session_state.get(pick_key) or queue_ids[0])
    if selected_now not in queue_ids:
        selected_now = queue_ids[0]
        st.session_state[pick_key] = selected_now
    selected_index = queue_ids.index(selected_now)
    wanted_page = selected_index // page_size

    current_page = int(st.session_state.get(page_key, wanted_page))
    if current_page < 0 or current_page >= total_pages:
        current_page = wanted_page
    # When selection has moved to another page externally, follow it automatically.
    if not (current_page * page_size <= selected_index < min((current_page + 1) * page_size, len(queue_ids))):
        current_page = wanted_page
    st.session_state[page_key] = current_page

    st.markdown(f"**🔥 WORK QUEUE — {len(mine)} cake(s), most urgent first.**")
    st.caption(f"Showing cakes {current_page * page_size + 1}–{min((current_page + 1) * page_size, len(queue_ids))} of {len(queue_ids)}")

    start = current_page * page_size
    visible_ids = queue_ids[start:start + page_size]

    for absolute_idx, order_id in enumerate(visible_ids, start=start + 1):
        label = _queue_label(order_id)
        selected_marker = "✅ " if str(order_id) == selected_now else ""
        if st.button(
            f"{selected_marker}{absolute_idx}. {label}",
            key=f"queue_pick_btn_{department_label}_{order_id}",
            use_container_width=True,
            type="primary" if str(order_id) == selected_now else "secondary",
        ):
            st.session_state[pick_key] = str(order_id)
            st.session_state[page_key] = (absolute_idx - 1) // page_size
            st.rerun()

    nav_left, nav_mid, nav_right = st.columns([1, 1.25, 1])
    with nav_left:
        if st.button("◀ Previous 4", key=f"queue_prev_{department_label}", use_container_width=True,
                     disabled=current_page <= 0):
            new_page = max(0, current_page - 1)
            st.session_state[page_key] = new_page
            st.session_state[pick_key] = queue_ids[new_page * page_size]
            st.rerun()
    with nav_mid:
        st.markdown(
            f"<div style='text-align:center;padding:.8rem .1rem;font-weight:700;'>Page {current_page + 1} of {total_pages}</div>",
            unsafe_allow_html=True,
        )
    with nav_right:
        if st.button("Next 4 ▶", key=f"queue_next_{department_label}", use_container_width=True,
                     disabled=current_page >= total_pages - 1):
            new_page = min(total_pages - 1, current_page + 1)
            st.session_state[page_key] = new_page
            st.session_state[pick_key] = queue_ids[new_page * page_size]
            st.rerun()

    selected_id = str(st.session_state.get(pick_key) or queue_ids[0])
    row = by_id[selected_id]
    position = queue_ids.index(selected_id) + 1
    st.caption(f"Selected: Cake {position} of {len(queue_ids)} • The first cake is always the most urgent.")

    # Match the team's familiar WhatsApp rhythm: photo first, then a short readable job sheet.
    has_image = render_reference_images(row)
    if not has_image:
        st.info("No photo was attached to this order.")

    urgent_tag = "🚨 URGENT — " if str(row.get("urgency_level") or "").strip().lower() == "urgent" else ""
    st.markdown(f"### {urgent_tag}Order: {row.get('order_id')}")
    due_display = disp(row.get("due_date"))
    time_display = disp(row.get("expected_time"))
    st.markdown(f"**Due:** {due_display}" + (f" at {time_display}" if time_display != "—" else ""))
    st.markdown(f"**{role_label}:** {disp(row.get(staff_column))}")
    st.markdown(f"**Flavours:** {disp(row.get('flavours'))}")
    size_bits = f"{disp(row.get('cake_size_value'))}\" {disp(row.get('cake_shape'))}".strip()
    st.markdown(f"**Size:** {size_bits}")
    st.markdown(f"**Icing:** {disp(row.get('icing_type'))}")

    design_notes = disp(row.get("design_description"))
    if design_notes != "—":
        st.markdown("### **INSTRUCTIONS**")
        # Each instruction is bold and occupies its own line. Split normal new lines, bullets,
        # or numbered text without turning a sentence like "Camp meeting 2026" into fragments.
        steps = [s.strip(" -•\t") for s in re.split(r"[\n*]+", str(design_notes)) if s.strip(" -•\t")]
        if not steps:
            steps = [str(design_notes).strip()]
        for step_no, step in enumerate(steps, 1):
            clean = re.sub(r"^\d+[.):]\s*", "", step).strip()
            st.markdown(f"**{step_no}. {clean}**")

    if str(row.get("topper_required")) == "Yes" and disp(row.get("topper_wording")) != "—":
        st.markdown(f"**WORDS ON CAKE: {disp(row.get('topper_wording'))}**")
    if str(row.get("sticker_required")) == "Yes" and disp(row.get("sticker_notes")) != "—":
        st.markdown(f"**STICKER: {disp(row.get('sticker_notes'))}**")
    st.markdown(f"**Client:** {disp(row.get('customer_name'))}")

    may_act = (is_hod or not my_name or
               my_name.lower() in str(row.get(staff_column) or "").lower() or
               str(row.get(staff_column) or "").strip() == "")
    is_incoming = row.get("workflow_status") == incoming_status

    with st.expander("📦 What did you use?"):
        options = STAGE_MATERIALS.get(materials_stage, ["Other"])
        used = st.multiselect("Tap everything you used on this cake", options, key=f"simple_materials_{row.order_id}")
        if st.button("Save materials", key=f"simple_materials_save_{row.order_id}", width='stretch'):
            if used:
                with connect() as conn:
                    for item in used:
                        conn.execute("""INSERT INTO stage_material_usage(order_id,stage,item_name,colour,size,quantity,unit,
                                        material_action,recorded_by,recorded_at,base_quantity,multiplier)
                                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                                     (row.order_id, materials_stage, item, "", "", 1.0, "used", "Used",
                                      my_name or role_label, now_iso(), 1.0, 1.0))
                    conn.commit()
                st.success("Saved.")
                st.rerun()
            else:
                st.error("Tap at least one item first.")

    usage = load_table("stage_material_usage")
    materials_logged = (not usage[(usage["order_id"] == row.order_id) &
                                  (usage["stage"] == materials_stage)].empty) if not usage.empty else False

    if is_incoming:
        if st.button(f"▶️ START THIS CAKE — {row.order_id}", width='stretch', disabled=not may_act,
                     key=f"simple_start_{row.order_id}"):
            update_order(row.order_id, {"workflow_status": active_status, "current_owner": department_label,
                                        started_field: now_iso()}, my_name or role_label,
                         f"{department_label} Started (Simple View)", department_label)
            st.rerun()
    else:
        can_finish = may_act and (materials_logged or not materials_required)
        if not materials_logged and materials_required:
            st.warning("Open ‘What did you use?’ and save materials before marking this cake done.")
        if st.button(f"✅ {completion_label}", width='stretch', disabled=not can_finish,
                     key=f"simple_done_{row.order_id}"):
            update_fields = {"workflow_status": next_status, "current_owner": next_owner,
                             "next_action": next_action_text, completed_field: now_iso()}
            update_order(row.order_id, update_fields, my_name or role_label,
                         f"{department_label} Complete (Simple View)", department_label)
            if next_staff_column:
                create_notification(row.order_id, next_owner, row.get(next_staff_column),
                                    f"🎂 {row.order_id} ({disp(row.get('customer_name'))}) is ready for you.")
            st.rerun()

    if not may_act:
        st.caption(f"👀 Viewing only — this is assigned to {disp(row.get(staff_column))}.")

    with st.expander("🔍 See full details"):
        order_card(row)

# Refresh only the phone queue, not the entire ERP page. A cake handed off by another
# department becomes visible here within 10 seconds even if the worker does not tap anything.
if hasattr(st, "fragment"):
    render_simple_view_live = st.fragment(run_every="10s")(render_simple_view)
else:
    render_simple_view_live = render_simple_view


def render_incoming_workload_forecast(df, staff_column, role_label, pre_stage_statuses, next_stage_label):
    """Shows the whole department everything still earlier in the pipeline for this role -
    whether or not a specific person has been named yet. The point is visibility as soon as
    a cake leaves Production Planning, so the department can prepare (pull items from
    Procurement, plan the day) well before it physically arrives. Pure view-only - no action
    buttons here."""
    st.markdown("### What's Coming Our Way")
    st.caption(f"Every cake already on its way to {role_label.lower()}s, wherever it currently sits earlier in the pipeline — "
               f"shown to the whole department as soon as it leaves Production Planning, whether or not a specific "
               f"{role_label.lower()} has been named yet. This is view-only — you can only work on a cake once it "
               f"actually reaches '{next_stage_label}'.")
    upcoming = df[df["workflow_status"].isin(pre_stage_statuses)] if not df.empty else df.iloc[0:0]
    if upcoming.empty:
        st.info("Nothing coming yet that's still earlier in the pipeline.")
    else:
        counts_by_day = upcoming.groupby("due_date").size().reset_index(name="count").sort_values("due_date")
        cols = st.columns(min(len(counts_by_day), 6) or 1)
        for i, (_, day_row) in enumerate(counts_by_day.head(6).iterrows()):
            with cols[i % len(cols)]:
                st.metric(day_row["due_date"], f"{day_row['count']} cake(s)")
        st.markdown("#### Full List — Click a Cake to See Its Image and Copy Its Details")
        upcoming = upcoming.copy()
        upcoming["_urgency_rank"] = (upcoming["urgency_level"] == "Urgent").astype(int) if "urgency_level" in upcoming.columns else 0
        upcoming_sorted = upcoming.sort_values(["_urgency_rank", "due_date", "expected_time"], ascending=[False, True, True])
        for _, cake_row in upcoming_sorted.iterrows():
            urgent_tag = "🚨 URGENT — " if str(cake_row.get("urgency_level")) == "Urgent" else ""
            label = (f"{urgent_tag}{cake_row.get('order_id')} — {disp(cake_row.get('customer_name'))} — "
                     f"{disp(cake_row.get('flavours'))} — Due {disp(cake_row.get('due_date'))} {disp(cake_row.get('expected_time'))}")
            with st.expander(label):
                has_image = render_reference_images(cake_row)
                if not has_image:
                    st.caption("No reference image was uploaded for this order.")
                assigned_person = disp(cake_row.get(staff_column))
                bold_fields = [
                    ("Order", cake_row.get('order_id')),
                    ("Customer", disp(cake_row.get('customer_name'))),
                    ("Product", disp(cake_row.get('product_type'))),
                    ("Category", disp(cake_row.get('cake_category'))),
                    ("Flavours", disp(cake_row.get('flavours'))),
                    ("Size", f"{disp(cake_row.get('cake_size_value'))}\""),
                    ("Shape", disp(cake_row.get('cake_shape'))),
                    ("Icing/Finish", disp(cake_row.get('icing_type'))),
                    ("Design Notes", disp(cake_row.get('design_description'))),
                    ("Due", f"{disp(cake_row.get('due_date'))} at {disp(cake_row.get('expected_time'))}"),
                    ("Currently at", disp(cake_row.get('workflow_status'))),
                    (role_label, assigned_person if assigned_person != '—' else 'Not yet named'),
                    ("Urgency", disp(cake_row.get('urgency_level'))),
                ]
                for label_txt, value_txt in bold_fields:
                    st.markdown(f"**{label_txt}:** {value_txt}")
                details_text = "\n".join(f"{label_txt}: {value_txt}" for label_txt, value_txt in bold_fields)
                st.caption("Tap the copy icon in the corner below to grab these details for your own notes.")
                st.code(details_text, language=None)


def render_decoration():
    page_header("🎨 Decoration", "Accept covering, receive topper handoffs, decorate, and send to Studio.")
    view_mode = st.radio("View", ["📷 Simple View", "📋 Full View"], key="deco_view_mode", horizontal=True,
                         index=(0 if st.session_state.get("deco_view_mode", "📷 Simple View") == "📷 Simple View" else 1))
    if view_mode == "📷 Simple View":
        render_simple_view_live("Decoration", "decorator_assigned", "Decorator", "Decorating Incoming", "Decorating",
                            "Studio Check", "Studio / Final QC", None, "Final quality check",
                            "Decoration Complete → Send to Studio", "decorating_started_at", "decorating_completed_at",
                            "Decoration", materials_required=True)
        return
    df = load_orders()
    render_hod_overview("Decoration", df)
    t0,t1,t3,t4,t5,t6 = st.tabs(["📅 Incoming Workload", "Incoming from Covering", "Decorating", "Correction Required", "✅ Finished Work", "🖼️ All Orders"])
    with t0:
        pre_decoration_statuses = ["Production Planned", "Baking", "Baking Correction Required",
                                    "Piling Incoming", "Piling", "Piling Correction Required",
                                    "Covering Incoming", "Covering", "Covering Correction Required"]
        render_incoming_workload_forecast(df, "decorator_assigned", "Decorator", pre_decoration_statuses, "Incoming from Covering")
    with t1:
        incoming_q = filter_orders(df,["Decorating Incoming"])
        render_queue_table(incoming_q, "Cakes Incoming From Covering", ["decorator_assigned", "icing_type"])
        row = select_order(incoming_q, "deco_in")
        if row is not None:
            order_card(row, [("Coverer", row.get("coverer_assigned")), ("Decorator", row.get("decorator_assigned"))])
            may_act = can_act_on(row, "decorator_assigned")
            if not may_act:
                st.info(f"👀 Viewing only — this job is assigned to **{first_name(row.get('decorator_assigned'))}**.")
            decoration_materials_ready = render_stage_material_planning("Decoration", row, row.get("decorator_assigned"))
            if not decoration_materials_ready:
                st.warning("Enter the materials for this cake before starting Decoration.")
            by = st.text_input("Checked by", value=disp(row.get("decorator_assigned")) if disp(row.get("decorator_assigned")) != "—" else st.session_state.get("staff_name",""), key="deco_by1")
            a,b = st.columns(2)
            if a.button("✅ Covering Accepted → Start Decorating", width='stretch', disabled=(not may_act or not decoration_materials_ready)):
                insert_stage_check(row.order_id,"Covering","Decoration",by,"Passed")
                update_order(row.order_id, {"workflow_status":"Decorating", "current_owner":"Decoration", "next_action":"Decorate and submit to Studio", "decorating_started_at":now_iso(), "decoration_status":"In Progress"}, by, "Covering Accepted by Decorator", "Decoration")
                st.rerun()
            with b:
                if may_act:
                    issue_form("deco_in", "Covering", "Decoration", "Covering Correction Required", "Coating / Covering", row, row.get("coverer_assigned"))
    with t3:
        prog_q = filter_orders(df,["Decorating"])
        render_queue_table(prog_q, "Cakes Currently Being Decorated", ["decorator_assigned", "icing_type"])
        row = select_order(prog_q, "deco_prog")
        if row is not None:
            order_card(row, [("Icing Type", row.get("icing_type"))])
            may_act = can_act_on(row, "decorator_assigned")
            if not may_act:
                st.info(f"👀 Viewing only — this job is assigned to **{first_name(row.get('decorator_assigned'))}**.")
            _,_,_,_decorators_ma,_ = staff_lists()
            render_multi_assign(row, "decorator_assigned", "Decorator", _decorators_ma, f"deco_{row.order_id}")
            can_finish = may_act
            if str(row.get("topper_required")) == "Yes":
                st.markdown("### 🎨 Topper Handoff")
                if str(row.get("topper_status")) == "Ready":
                    st.success(f"TOPPER READY — {disp(row.get('topper_pickup_note'))}")
                    st.write(f"**Topper wording:** {disp(row.get('topper_wording'))}")
                    receiver=st.text_input("Decorator receiving topper",value=disp(row.get("decorator_assigned")),key="topper_receiver")
                    if st.button("Confirm Topper Picked from Keith",width='stretch',key="topper_received_btn", disabled=not may_act):
                        update_order(row.order_id,{"topper_status":"Received by Decorator","topper_received_by_decorator":receiver,"topper_received_at":now_iso(),"topper_pickup_note":"Topper received by assigned decorator"},receiver,"Topper Received","Decoration")
                        st.rerun()
                elif str(row.get("topper_status")) == "Received by Decorator":
                    st.success(f"Topper received by {disp(row.get('topper_received_by_decorator'))}.")
                else:
                    st.warning(f"TOPPER STATUS: {disp(row.get('topper_status'))} — follow up with {disp(row.get('topper_assigned_to'))}.")
            if str(row.get("sticker_required")) == "Yes":
                st.markdown("### 🏷️ Sticker Handoff")
                if str(row.get("sticker_status")) == "Ready":
                    st.success(f"STICKER READY — {disp(row.get('sticker_pickup_note'))}")
                    st.write(f"**Sticker notes:** {disp(row.get('sticker_notes'))}")
                    sticker_receiver=st.text_input("Decorator receiving sticker",value=disp(row.get("decorator_assigned")),key="sticker_receiver")
                    if st.button("Confirm Sticker Picked",width='stretch',key="sticker_received_btn", disabled=not may_act):
                        update_order(row.order_id,{"sticker_status":"Received by Decorator","sticker_received_by_decorator":sticker_receiver,"sticker_received_at":now_iso(),"sticker_pickup_note":"Sticker received by assigned decorator"},sticker_receiver,"Sticker Received","Decoration")
                        st.rerun()
                elif str(row.get("sticker_status")) == "Received by Decorator":
                    st.success(f"Sticker received by {disp(row.get('sticker_received_by_decorator'))}.")
                else:
                    st.warning(f"STICKER STATUS: {disp(row.get('sticker_status'))} — follow up with {disp(row.get('sticker_assigned_to'))}.")
            decoration_materials_ready = render_stage_material_planning("Decoration", row, row.get("decorator_assigned"))
            if not decoration_materials_ready:
                st.warning("Record materials before completing Decoration.")
            can_finish = can_finish and decoration_materials_ready
            by = st.text_input("Updated by", value=disp(row.get("decorator_assigned")), key="deco_by3")
            if st.button("✅ Decoration Complete → Send to Studio / Final QC", width='stretch', disabled=not can_finish):
                update_order(row.order_id, {"workflow_status":"Studio Check", "current_owner":"Studio / Final QC", "next_action":"Final quality check", "decorating_completed_at":now_iso(), "decoration_status":"Complete"}, by, "Decoration Submitted", "Decoration")
                create_notification(row.order_id, "Studio / Final QC", None,
                                     f"🎂 {row.order_id} ({disp(row.get('customer_name'))}) has finished decoration and is ready for final QC.")
                st.rerun()
    with t4:
        row = select_order(filter_orders(df,["Decoration Correction Required"]), "deco_corr")
        if row is not None:
            order_card(row, [("Issue", row.get("issue_notes"))])
            may_act = can_act_on(row, "decorator_assigned")
            if not may_act:
                st.info(f"👀 Viewing only — this job is assigned to **{first_name(row.get('decorator_assigned'))}**.")
            by = st.text_input("Corrected by", value=disp(row.get("decorator_assigned")), key="deco_by4")
            if st.button("🔁 Correction Complete — Resubmit to Studio", width='stretch', disabled=not may_act):
                update_order(row.order_id, {"workflow_status":"Studio Check", "current_owner":"Studio / Final QC", "next_action":"Resubmitted for Studio check"}, by, "Decoration Correction Complete", "Decoration")
                st.rerun()

    with t5:
        render_finished_work_tab("Decoration")

    with t6:
        render_order_gallery(df, "🖼️ All Orders — Images & Copyable Details")


def render_studio_qc():
    page_header("🔍 Final QC & Packaging", "One continuous platform: final quality control, packaging, delivery notes, and dispatch planning.")
    df = load_orders()

    # Do not use Streamlit tabs here. Tab contents execute even when the user has not
    # opened the tab, which used to build QC + Packaging + Dispatch + gallery all at
    # once. That creates very large websocket messages on Safari/iPhone.
    section = st.selectbox(
        "What do you want to do?",
        ["Final QC", "Packaging & Delivery Notes", "Dispatch Planning", "✅ Finished Work", "🖼️ All Orders"],
        key="studio_mobile_section",
    )

    if section == "Final QC":
        render_hod_overview("Studio / Final QC", df)
        check_q = filter_orders(df,["Studio Check"])
        render_queue_table(check_q, "Cakes Awaiting Final QC", ["decorator_assigned"])
        row = select_order(check_q, "studio_check")
        if row is not None:
            order_card(row, [("Decorator", row.get("decorator_assigned"))])
            by = st.text_input("Checked by", value=first_name(st.session_state.get("staff_name", "Studio")), key="studio_checked_by")
            a,b = st.columns(2)
            if a.button("✅ Final QC Passed → Ready for Packaging", width='stretch'):
                insert_stage_check(row.order_id,"Decoration","Studio / Final QC",by,"Passed")
                update_order(row.order_id, {"workflow_status":"Ready for Packaging", "current_owner":"Studio / Final QC", "next_action":"Package and print delivery note", "qc_status":"Approved", "qc_completed_at":now_iso()}, by, "Final QC Passed", "Studio")
                create_notification(row.order_id, "Studio / Final QC", None,
                                     f"🎂 {row.order_id} ({disp(row.get('customer_name'))}) passed final QC and is ready to package.")
                st.rerun()
            with b:
                issue_form("studio", "Decoration", "Studio / Final QC", "Decoration Correction Required", "Decoration", row, row.get("decorator_assigned"))
    elif section == "Packaging & Delivery Notes":
        render_packaging(show_header=False)
    elif section == "Dispatch Planning":
        render_dispatch(show_header=False)
    elif section == "✅ Finished Work":
        render_finished_work_tab("Studio / Final QC")
    else:
        render_order_gallery(df, "🖼️ All Orders — Images & Copyable Details")

def render_procurement():
    page_header("🧾 Procurement", "Receive material requests from Baking, Piling, Covering, Decoration and Packaging.")
    st.markdown("### Material Usage Log — By Colour, Size, Quantity & Weight")
    st.caption("Everything departments have logged as Used / Needed / Requested, across every stage, so you can track real consumption.")
    usage = load_table("stage_material_usage")
    table(usage.sort_values("recorded_at", ascending=False).head(100) if not usage.empty else usage,
          ["order_id","stage","item_name","colour","size","quantity","unit","material_action","recorded_by","recorded_at"])

    st.divider()
    st.markdown("### Material Requirements Queue")
    req = load_table("order_material_requirements")
    if req.empty:
        st.info("No material requirements submitted yet.")
    else:
        table(req, ["id","order_id","item_name","quantity_required","unit","requirement_status","requested_by","requested_at"])
        req_options = [f"#{r.id} — {r.item_name} ({r.quantity_required:g} {r.unit}) for {r.order_id}" for r in req.itertuples()]
        req_pick = st.selectbox("Select a requirement (search by typing item name, order ID, or number)", req_options, key="proc_req_pick")
        rid = int(req_pick.split(" — ")[0].lstrip("#"))
        status = st.selectbox("Procurement action", ["Issued", "Partially Issued", "Out of Stock", "Requisition Required"])
        issued_qty = st.number_input("Quantity issued", min_value=0.0, step=1.0)
        by = st.text_input("Updated by", value="Procurement")
        notes = st.text_input("Notes")
        if st.button("Update Requirement", width='stretch'):
            matches = req[req["id"] == rid]
            if matches.empty:
                st.error("No requirement found with that ID.")
            else:
                row_req = matches.iloc[0]
                with connect() as conn:
                    conn.execute("UPDATE order_material_requirements SET requirement_status=? WHERE id=?", (status, rid))
                    conn.execute("INSERT INTO material_issues(requirement_id, quantity_issued, issued_by, issued_to, issue_status, issued_at, notes) VALUES(?,?,?,?,?,?,?)",
                                 (rid, issued_qty, by, row_req.requested_by, status, now_iso(), notes))
                    if status == "Requisition Required":
                        conn.execute("INSERT INTO procurement_requisitions(requirement_id, order_id, item_name, quantity_required, requisition_status, requested_at, updated_by) VALUES(?,?,?,?,?,?,?)",
                                     (rid, row_req.order_id, row_req.item_name, row_req.quantity_required, "Pending Procurement", now_iso(), by))
                    conn.commit()
                    requester_dept = None
                    if row_req.order_id:
                        owner_row = conn.execute("SELECT current_owner FROM orders WHERE order_id=?", (row_req.order_id,)).fetchone()
                        requester_dept = owner_row[0] if owner_row else None
                status_icon = {"Issued": "✅", "Partially Issued": "⚠️", "Out of Stock": "❌", "Requisition Required": "📋"}.get(status, "🧾")
                if requester_dept:
                    create_notification(row_req.order_id, requester_dept, row_req.requested_by,
                                         f"{status_icon} {row_req.item_name} ({row_req.quantity_required:g} {row_req.unit}) for {row_req.order_id} — {status.lower()} by Procurement.")
                st.success("Procurement updated."); st.rerun()

    st.divider()
    render_order_gallery(load_orders(), "🖼️ All Orders — Images & Copyable Details")



def delivery_note_html(row, fmt="full"):
    """fmt: 'full' = one-page A5-ish delivery note for the customer copy.
            'label' = small thermal-printer label (~76mm wide) to stick on the box,
                      with just the essentials plus our contact in case someone else picks it up."""
    if fmt == "label":
        return f"""
        <div class='print-note print-note--label'>
          <div style='text-align:center;'><img src='{LOGO_DATA_URI}' style='height:34px;width:auto;'></div>
          <div style='text-align:center;font-weight:900;font-size:13px;'>{BAKERY_NAME}</div>
          <div style='text-align:center;font-style:italic;font-size:9px;margin-bottom:4px;'>{APP_TAGLINE}</div>
          <hr style='border-top:1px dashed #111;margin:3px 0;'>
          <div><b>Order:</b> {disp(row.get('order_id'))}</div>
          <div><b>For:</b> {disp(row.get('customer_name'))}</div>
          <div><b>Tel:</b> {disp(row.get('customer_number'))}</div>
          <div><b>Deliver to:</b> {disp(row.get('location'))}</div>
          <div><b>Window:</b> {disp(row.get('delivery_window_start'))}–{disp(row.get('delivery_window_end'))}</div>
          <div><b>Qty:</b> {disp(row.get('order_quantity'))} cake(s){' BULK' if str(row.get('is_bulk_order'))=='Yes' else ''}</div>
          <div><b>Balance due:</b> {fmt_ugx(row.get('balance_to_collect') or row.get('balance'))}</div>
          <hr style='border-top:1px dashed #111;margin:3px 0;'>
          <div style='text-align:center;font-weight:900;'>If found, please call:</div>
          <div style='text-align:center;font-weight:900;'>{BAKERY_PHONE}</div>
        </div>
        """
    return f"""
    <div class='print-note print-note--full'>
      <div style='text-align:center;'><img src='{LOGO_DATA_URI}' style='height:70px;width:auto;'></div>
      <div style='text-align:center;font-size:22px;font-weight:900;'>{BAKERY_NAME}</div>
      <div style='text-align:center;font-style:italic;margin:2px 0 8px;'>{APP_TAGLINE}</div>
      <hr>
      <p><b>ORDER:</b> {disp(row.get('order_id'))}</p>
      <p><b>CUSTOMER:</b> {disp(row.get('customer_name'))}</p>
      <p><b>TEL:</b> {disp(row.get('customer_number'))}</p>
      <p><b>DELIVER TO:</b><br>{disp(row.get('location'))}</p>
      <p><b>DELIVERY TIME:</b> {disp(row.get('expected_time'))}</p>
      <p><b>DELIVERY WINDOW:</b> {disp(row.get('delivery_window_start'))} – {disp(row.get('delivery_window_end'))}</p>
      <p><b>QUANTITY:</b> {disp(row.get('order_quantity'))} cake(s){' (BULK ORDER)' if str(row.get('is_bulk_order'))=='Yes' else ''}</p>
      <p><b>BALANCE:</b> {fmt_ugx(row.get('balance_to_collect') or row.get('balance'))}</p>
      <p><b>PAYMENT:</b> {disp(row.get('payment_method'))}</p>
      <hr>
      <p style='text-align:center;'>Questions or issues with this delivery? Call us: <b>{BAKERY_PHONE}</b></p>
      <p style='text-align:center;font-weight:bold;'>BAKING YOUR IDEAS TO LIFE</p>
    </div>
    """


def render_packaging_finish_step(row, key_prefix):
    order_card(row)
    packaging_materials_ready = render_stage_material_planning("Packaging", row, st.session_state.get("staff_name", "Studio"), key_prefix=key_prefix)
    if not packaging_materials_ready:
        st.warning("Record packaging materials before completing this job.")
    st.markdown("### Print / Delivery Note")
    print_fmt = st.radio(
        "What are you printing?",
        ["Full Delivery Note (one page, for the customer)", "Small Box Label (thermal/receipt printer, sticks on the box)"],
        key=f"{key_prefix}_print_fmt")
    fmt = "label" if print_fmt.startswith("Small") else "full"
    if fmt == "label":
        st.caption("Designed for narrow thermal/receipt printers (~76mm roll width). If your printer uses a different width, adjust the printer's paper size setting before printing.")
    st.markdown(delivery_note_html(row, fmt), unsafe_allow_html=True)
    st.info("Press Ctrl + P (or your printer app's print option). It now prints on a single page/label only — the rest of the screen is hidden automatically.")
    by = st.text_input("Updated by", value="Packaging", key=f"{key_prefix}_by2")
    if st.button("✅ Packaging Complete → Ready for Dispatch", width='stretch', disabled=not packaging_materials_ready, key=f"{key_prefix}_complete_btn"):
        update_order(row.order_id, {"workflow_status":"Ready for Dispatch", "current_owner":"Dispatch", "next_action":"Assign to delivery run", "packaging_status":"Complete", "packaging_completed_at":now_iso()}, by, "Packaging Complete", "Packaging")
        create_notification(row.order_id, "Dispatch / Driver", None,
                             f"📦 {row.order_id} ({disp(row.get('customer_name'))}) is packaged and ready for a delivery run.")
        st.session_state.pop("_just_started_packaging", None)
        st.rerun()


def render_packaging(show_header=True):
    if show_header:
        page_header("📦 Packaging", "Pack cake, print simple delivery note, and send to Dispatch.")
    df = load_orders()

    # A conditional menu is deliberately used instead of st.tabs. Streamlit executes
    # every tab body during the rerun, even invisible tabs. On iPhones the old page
    # could therefore render multiple order cards + the full gallery in one websocket
    # update and crash Safari's message decoder.
    section = st.selectbox(
        "Packaging view",
        ["Ready for Packaging", "Packaging", "Delivery Note", "✅ Finished Work", "🖼️ All Orders"],
        key="packaging_mobile_section",
    )

    if section == "Ready for Packaging":
        render_hod_overview("Packaging", df)
        ready_q = filter_orders(df,["Ready for Packaging"])
        render_queue_table(ready_q, "Cakes Ready For Packaging")
        row = select_order(ready_q, "pack_ready")
        if row is not None:
            order_card(row)
            packaging_materials_ready = render_stage_material_planning("Packaging", row, st.session_state.get("staff_name", "Studio"))
            if not packaging_materials_ready:
                st.warning("Enter packaging materials before starting this job.")
            by = st.text_input("Updated by", value=first_name(st.session_state.get("staff_name", "Studio")), key="pack_by1")
            if st.button("▶️ Start Packaging", width='stretch', disabled=not packaging_materials_ready):
                update_order(row.order_id, {"workflow_status":"Packaging", "current_owner":"Packaging", "next_action":"Print delivery note and complete packaging", "packaging_status":"In Progress"}, by, "Packaging Started", "Packaging")
                st.session_state["_just_started_packaging"] = row.order_id
                st.rerun()
        just_started_id = st.session_state.get("_just_started_packaging")
        if just_started_id:
            fresh_df = load_orders()
            fresh_row = fresh_df[fresh_df["order_id"] == just_started_id]
            if not fresh_row.empty and fresh_row.iloc[0]["workflow_status"] == "Packaging":
                st.success(f"{just_started_id} moved to packaging — continue right here to finish it.")
                render_packaging_finish_step(fresh_row.iloc[0], key_prefix="pack_inline")
            else:
                st.session_state.pop("_just_started_packaging", None)
    elif section == "Packaging":
        row = select_order(filter_orders(df,["Packaging"]), "pack_prog")
        if row is not None:
            render_packaging_finish_step(row, key_prefix="pack_prog")
    elif section == "Delivery Note":
        row = select_order(df, "pack_note", "Select any order for delivery note")
        if row is not None:
            print_fmt2 = st.radio(
                "What are you printing?",
                ["Full Delivery Note (one page, for the customer)", "Small Box Label (thermal/receipt printer, sticks on the box)"],
                key="pack_print_fmt2")
            fmt2 = "label" if print_fmt2.startswith("Small") else "full"
            st.markdown(delivery_note_html(row, fmt2), unsafe_allow_html=True)
    elif section == "✅ Finished Work":
        render_finished_work_tab("Packaging")
    else:
        render_order_gallery(df, "🖼️ All Orders — Images & Copyable Details")

def render_dispatch(show_header=True):
    if show_header:
        page_header("🚚 Dispatch", "Create delivery runs with multiple cakes and stop sequence.")
    df = load_orders()
    _,_,_,_,drivers = staff_lists()
    ready = filter_orders(df,["Ready for Dispatch"])
    render_queue_table(ready, "Cakes Ready For Dispatch", ["delivery_date", "delivery_window_start","delivery_window_end"])
    if ready.empty:
        st.info("No cakes ready for dispatch.")
    else:
        st.markdown("### Create Delivery Run")
        driver = st.selectbox("Driver", drivers)
        by = st.text_input("Created by", value="Dispatch")
        selected = st.multiselect("Select orders for this run", ready.apply(order_label, axis=1).tolist())
        sequence = []
        if selected:
            st.markdown("### Stop sequence")
            for i, label in enumerate(selected, start=1):
                st.write(f"Stop {i}: {label}")
                sequence.append(label.split(" · ")[0])
        if st.button("Create Delivery Run", width='stretch') and sequence:
            run_id = f"RUN-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{driver[:3].upper()}"
            with connect() as conn:
                conn.execute("INSERT INTO delivery_runs(run_id, driver_name, run_status, created_at, created_by) VALUES(?,?,?,?,?)", (run_id, driver, "Planned", now_iso(), by))
                for stop, oid in enumerate(sequence, start=1):
                    conn.execute("INSERT INTO delivery_run_orders(run_id, order_id, stop_sequence, delivery_status) VALUES(?,?,?,?)", (run_id, oid, stop, "Planned"))
                    conn.execute("UPDATE orders SET workflow_status=?, current_owner=?, next_action=?, driver_assigned=?, last_updated_at=?, last_updated_by=? WHERE order_id=?", ("Delivery Run Assigned", "Driver", "Start delivery run", driver, now_iso(), by, oid))
                    conn.execute("INSERT INTO audit_logs(order_id, action_type, stage, action_details, performed_by, performed_at) VALUES(?,?,?,?,?,?)", (oid, "Assigned to Delivery Run", "Dispatch", run_id, by, now_iso()))
                conn.commit()
            # Notifications open their own separate database connection, so they must only run
            # after the transaction above has fully committed and closed — calling them while
            # this connection still had uncommitted writes open caused a genuine "database is
            # locked" error, since SQLite doesn't allow two simultaneous writers to the same file.
            for oid in sequence:
                create_notification(oid, "Dispatch / Driver", driver,
                                     f"🚚 {oid} assigned to you for delivery run {run_id}.")
            refresh_data(); st.success(f"Delivery run {run_id} created."); st.rerun()
    st.markdown("### Delivery Runs")
    runs = load_table("delivery_runs")
    table(runs, ["run_id","driver_name","run_status","run_started_at","run_completed_at","created_at","created_by"])


def render_driver(show_header=True):
    if show_header:
        page_header("🚗 Driver Delivery", "Simple delivery flow: Start Ride → Arrived → Payment/Delivered → Next Stop.")
    # Bigger, touch-friendly buttons for a phone in a moving vehicle — normal-sized buttons
    # are genuinely hard to tap accurately while driving/parked and in a hurry.
    st.markdown("""
    <style>
    div[data-testid="stButton"] > button {
        font-size: 1.15rem !important;
        padding: 18px 10px !important;
        min-height: 64px !important;
        font-weight: 600 !important;
        border-radius: 10px !important;
    }
    a.ca-call-btn {
        display: flex; align-items: center; justify-content: center;
        font-size: 1.15rem; font-weight: 600; text-decoration: none;
        min-height: 64px; padding: 18px 10px; border-radius: 10px;
        background-color: #F0E6F5; color: #1A1420; border: 1px solid #4B2A5C;
        width: 100%; box-sizing: border-box;
    }
    </style>
    """, unsafe_allow_html=True)
    runs = load_table("delivery_runs")
    if runs.empty:
        st.info("No delivery runs yet.")
        return
    run_label = st.selectbox("Select Delivery Run", runs["run_id"].tolist())
    run = runs[runs["run_id"] == run_label].iloc[0]
    dro = load_table("delivery_run_orders")
    orders = load_orders()
    stops = dro[dro["run_id"] == run_label].sort_values("stop_sequence")
    merged = stops.merge(orders, on="order_id", how="left", suffixes=("_run", "_order"))

    # Both delivery_run_orders and orders have delivery_status.
    # After merge, keep the delivery-run status as the driver's stop status.
    if "delivery_status_run" in merged.columns:
        merged["stop_delivery_status"] = merged["delivery_status_run"]
    elif "delivery_status" in merged.columns:
        merged["stop_delivery_status"] = merged["delivery_status"]
    else:
        merged["stop_delivery_status"] = "Planned"

    if "delivery_status_order" in merged.columns:
        merged["order_delivery_status"] = merged["delivery_status_order"]
    elif "delivery_status" in merged.columns:
        merged["order_delivery_status"] = merged["delivery_status"]
    else:
        merged["order_delivery_status"] = ""

    # Preserve the primary key from delivery_run_orders after merge.
    # The delivery_run_orders.id column may become id_run or id depending on overlap.
    if "id_run" in merged.columns:
        merged["stop_record_id"] = merged["id_run"]
    elif "id" in merged.columns:
        merged["stop_record_id"] = merged["id"]
    else:
        merged["stop_record_id"] = None

    st.markdown(f"### Driver: {run.driver_name} | Status: {run.run_status}")
    if run.run_status == "Planned":
        if st.button("🚗 Start Delivery Run", width='stretch'):
            with connect() as conn:
                conn.execute("UPDATE delivery_runs SET run_status='In Progress', run_started_at=? WHERE run_id=?", (now_iso(), run_label))
                conn.commit()
            st.rerun()

    table(merged, ["stop_sequence","order_id","customer_name","customer_number","location","expected_time","balance_to_collect","stop_delivery_status"])
    active = merged[merged["stop_delivery_status"].isin(["Planned","En Route","Arrived","Finance Pending","Payment Confirmed"])]
    if active.empty:
        st.success("All stops completed.")
        if st.button("✅ Complete Delivery Run", width='stretch'):
            with connect() as conn:
                conn.execute("UPDATE delivery_runs SET run_status='Completed', run_completed_at=? WHERE run_id=?", (now_iso(), run_label))
                conn.commit()
            st.rerun()
        return
    current = active.iloc[0]
    st.markdown("### Current Stop")
    order_card(current, [("Stop", current.stop_sequence), ("Balance", fmt_ugx(current.balance_to_collect))])
    win_start, win_end = disp(current.get("delivery_window_start")), disp(current.get("delivery_window_end"))
    if win_start != "—" and win_end != "—":
        try:
            now_t = datetime.now().time()
            ws = dtime.fromisoformat(str(win_start)[:8])
            we = dtime.fromisoformat(str(win_end)[:8])
            if ws <= now_t <= we:
                st.success(f"🟢 Within delivery window ({win_start}–{win_end}).")
            else:
                st.warning(f"⚠️ Outside the customer's delivery window ({win_start}–{win_end}). Consider calling ahead.")
        except Exception:
            pass

    phone = disp(current.get("customer_number"))
    call_col, ride_col = st.columns(2)
    with call_col:
        if phone != "—":
            phone_clean = re.sub(r"[^0-9+]", "", str(phone))
            st.markdown(f'<a class="ca-call-btn" href="tel:{phone_clean}">📞 Call {first_name(current.get("customer_name")) or "Client"}</a>',
                        unsafe_allow_html=True)
        else:
            st.button("📞 No Phone Number on File", disabled=True, width='stretch')
    with ride_col:
        not_started = current.stop_delivery_status == "Planned"
        if ride_col.button("🚗 Start Ride to This Stop" if not_started else "🚗 Already En Route",
                            disabled=not not_started, width='stretch'):
            with connect() as conn:
                conn.execute("UPDATE delivery_run_orders SET delivery_status='En Route' WHERE id=?", (int(current.stop_record_id),))
                conn.execute("UPDATE orders SET delivery_status='En Route', workflow_status='Driver En Route', current_owner='Driver', next_action='Arrive and hand over' WHERE order_id=?", (current.order_id,))
                conn.commit()
            create_notification(current.order_id, "Customer Care", None,
                                 f"🚗 {current.order_id} ({disp(current.get('customer_name'))}) — driver is now on the way, "
                                 f"in case the customer calls asking.")
            refresh_data(); st.rerun()

    a,b,c = st.columns(3)
    already_arrived = current.stop_delivery_status not in ("Planned", "En Route")
    if a.button("📍 Arrived at Destination" if not already_arrived else "✅ Already Marked Arrived",
                disabled=already_arrived, width='stretch'):
        with connect() as conn:
            conn.execute("UPDATE delivery_run_orders SET arrival_time=?, delivery_status='Arrived' WHERE id=?", (now_iso(), int(current.stop_record_id)))
            conn.execute("UPDATE orders SET delivery_status='Arrived', workflow_status='Arrived at Destination', current_owner='Driver', next_action='Collect balance or complete handover' WHERE order_id=?", (current.order_id,))
            conn.commit()
        create_notification(current.order_id, "Customer Care", None,
                             f"📍 {current.order_id} ({disp(current.get('customer_name'))}) — driver has arrived at the destination.")
        if float(current.balance_to_collect or 0) > 0:
            create_notification(current.order_id, "Finance", None,
                                 f"📍 {current.order_id} ({disp(current.get('customer_name'))}) — driver has arrived, "
                                 f"a balance of {fmt_ugx(current.balance_to_collect)} will need confirming shortly.")

        refresh_data(); st.rerun()
    balance = float(current.balance_to_collect or 0)
    if b.button("💰 Request Finance Confirmation" if balance > 0 else "No Balance Needed", disabled=balance <= 0, width='stretch'):
        with connect() as conn:
            conn.execute("UPDATE delivery_run_orders SET finance_confirmation_requested_at=?, delivery_status='Finance Pending' WHERE id=?", (now_iso(), int(current.stop_record_id)))
            conn.execute("UPDATE orders SET workflow_status='Finance Payment Confirmation Pending', current_owner='Finance', next_action='Confirm delivery balance received' WHERE order_id=?", (current.order_id,))
            conn.commit()
        create_notification(current.order_id, "Finance", None,
                             f"💰 {current.order_id} ({disp(current.get('customer_name'))}) — driver is requesting confirmation "
                             f"of {fmt_ugx(balance)} collected on delivery.")
        refresh_data(); st.rerun()
    can_deliver = balance <= 0 or current.workflow_status == "Payment Confirmed" or current.stop_delivery_status == "Payment Confirmed"
    if c.button("✅ Delivered / Next Stop", disabled=not can_deliver, width='stretch'):
        with connect() as conn:
            conn.execute("UPDATE delivery_run_orders SET delivery_completed_at=?, delivery_status='Delivered' WHERE id=?", (now_iso(), int(current.stop_record_id)))
            conn.execute("UPDATE orders SET workflow_status='Follow-up Pending', current_owner='Customer Care', next_action='Customer follow-up', delivery_status='Delivered', delivered_at=? WHERE order_id=?", (now_iso(), current.order_id))
            conn.commit()
        create_notification(current.order_id, "Customer Care", None,
                             f"✅ {current.order_id} ({disp(current.get('customer_name'))}) has been delivered — ready for follow-up.")
        refresh_data(); st.rerun()



def render_dispatch_driver():
    page_header("🚚 Delivery Department", "Plan dispatch runs and manage driver delivery from one clear workspace.")
    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    t1, t2, t3 = st.tabs(["Dispatch Planning", "Driver Delivery", "🖼️ All Orders"])
    with t1:
        render_dispatch()
    with t2:
        render_driver()
    with t3:
        render_order_gallery(load_orders(), "🖼️ All Orders — Images & Copyable Details")

def render_followup_complaints_section(df):
    """Follow-up calls and complaint case tracking, now folded into Customer Care
    (rather than a separate department login) since the same staff handle both."""
    with st.expander("📞 Handle Follow-Ups & Complaints"):
        t1,t2 = st.tabs(["Customer Follow-up", "Complaint Cases"])
        with t1:
            row = select_order(filter_orders(df,["Follow-up Pending"]), "follow_order")
            if row is not None:
                order_card(row)
                satisfied = st.radio("Customer satisfied?", ["Yes", "No"])
                rating = st.slider("Rating", 1, 5, 5)
                comments = st.text_area("Customer comments")
                by = st.text_input("Followed up by", value="Customer Care")
                if satisfied == "Yes":
                    if st.button("✅ Close Follow-up", width='stretch'):
                        update_order(row.order_id, {"workflow_status":"Follow-up Done", "current_owner":"Customer Care", "next_action":"Closed", "follow_up_status":"Done", "follow_up_completed_at":now_iso(), "satisfaction_rating":rating}, by, "Follow-up Closed", "Customer Care")
                        st.success("Order closed."); st.rerun()
                else:
                    cat = st.selectbox("Complaint category", ["Cake Quality", "Wrong Design", "Wrong Flavour", "Late Delivery", "Damaged Cake", "Customer Service", "Payment Issue", "Driver Conduct", "Missing Accessories", "Other"])
                    severity = st.selectbox("Severity", ["Low", "Medium", "High", "Critical"])
                    resp = st.selectbox("Responsible department", ["Customer Care", "Baking", "Filling / Piling", "Coating / Covering", "Decoration", "Studio / Final QC", "Packaging", "Delivery", "Finance", "Procurement"])
                    resp_person = st.text_input("Responsible staff member (if known)", placeholder="e.g. Billy — leave blank if unclear yet")
                    loss_value = st.number_input(
                        "Loss / complaint value (UGX)", min_value=0, step=5000,
                        help="The cost of this mistake — e.g. wasted ingredients, a refund given, or the full order value if it had to be redone. This is what gets accounted for with Finance.")
                    if st.button("🚨 Open Complaint Case", width='stretch'):
                        cid = f"CMP-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:3].upper()}"
                        with connect() as conn:
                            conn.execute("""INSERT INTO complaints(complaint_id, order_id, customer_name, complaint_category,
                                            complaint_details, severity, responsible_department, responsible_person,
                                            loss_value_ugx, repayment_status, opened_at, complaint_status)
                                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                                         (cid, row.order_id, row.customer_name, cat, comments, severity, resp,
                                          resp_person.strip(), loss_value, "Pending Review", now_iso(), "Opened"))
                            conn.commit()
                        update_order(row.order_id, {"workflow_status":"Complaint Open", "current_owner":"Customer Care", "next_action":"Resolve complaint", "follow_up_status":"Complaint Open", "satisfaction_rating":rating}, by, "Complaint Opened", "Follow-up")
                        who = resp_person.strip() or f"the {resp} team"
                        create_notification(
                            row.order_id, resp, resp_person.strip() or None,
                            f"Complaint {cid} ({cat}, {severity} severity) has been raised against {who} for order {row.order_id}. "
                            f"Estimated loss value: UGX {loss_value:,.0f}. This has been sent to Finance for accountability.")
                        st.warning(f"Complaint {cid} opened — {who} has been notified, and Finance has an accountability record for UGX {loss_value:,.0f}."); st.rerun()
        with t2:
            comp = load_table("complaints")
            table(comp, ["complaint_id","order_id","customer_name","complaint_category","severity",
                  "responsible_department","responsible_person","loss_value_ugx","repayment_status",
                  "complaint_status","opened_at","resolved_at"])
            if not comp.empty:
                cid = st.selectbox("Select complaint", comp["complaint_id"].tolist())
                rowc = comp[comp["complaint_id"] == cid].iloc[0]
                action = st.text_area("Resolution action")
                by = st.text_input("Updated by", value="Customer Care", key="comp_by")
                if st.button("✅ Mark Complaint Resolved", width='stretch'):
                    with connect() as conn:
                        conn.execute("UPDATE complaints SET complaint_status='Closed', resolution_action=?, resolved_at=?, customer_confirmation='Confirmed' WHERE complaint_id=?", (action, now_iso(), cid))
                        conn.commit()
                    update_order(rowc.order_id, {"workflow_status":"Follow-up Done", "current_owner":"Customer Care", "next_action":"Closed", "follow_up_status":"Complaint Resolved"}, by, "Complaint Resolved", "Follow-up")
                    st.success("Complaint closed."); st.rerun()


def render_management_overview(df, comp):
    """The business-level view management actually needs: orders, revenue, and complaints,
    filterable by day/week/month — separate from the raw operational tables below."""
    st.markdown("## 📊 Management Overview")
    period = st.selectbox("Time period", ["Today", "This Week", "This Month", "Custom Range"], key="mgmt_period")
    today = date.today()
    if period == "Today":
        start_date, end_date = today, today
    elif period == "This Week":
        start_date, end_date = today - timedelta(days=today.weekday()), today
    elif period == "This Month":
        start_date, end_date = today.replace(day=1), today
    else:
        a, b = st.columns(2)
        start_date = a.date_input("From", value=today - timedelta(days=7), key="mgmt_start")
        end_date = b.date_input("To", value=today, key="mgmt_end")

    if df.empty or "order_created_at" not in df.columns:
        st.info("No orders yet.")
        return

    created_dates = pd.to_datetime(df["order_created_at"], errors="coerce").dt.date
    period_df = df[(created_dates >= start_date) & (created_dates <= end_date)]

    period_comp = comp.iloc[0:0]
    if not comp.empty and "opened_at" in comp.columns:
        comp_dates = pd.to_datetime(comp["opened_at"], errors="coerce").dt.date
        period_comp = comp[(comp_dates >= start_date) & (comp_dates <= end_date)]

    total_orders = len(period_df)
    total_revenue = float(pd.to_numeric(period_df.get("price_ugx"), errors="coerce").fillna(0).sum())
    avg_order_value = total_revenue / total_orders if total_orders else 0
    urgent_count = int((period_df.get("order_type") == "Urgent / Abrupt Order").sum()) if not period_df.empty else 0
    complaints_opened = len(period_comp)
    complaints_resolved = int((period_comp.get("complaint_status") == "Closed").sum()) if not period_comp.empty else 0
    ratings = pd.to_numeric(period_df.get("satisfaction_rating"), errors="coerce").dropna() if not period_df.empty else pd.Series(dtype=float)
    avg_rating = ratings.mean() if len(ratings) else None

    st.caption(f"Showing **{start_date.strftime('%d %b %Y')} to {end_date.strftime('%d %b %Y')}**")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1: kpi("Orders", f"{total_orders:,}")
    with c2: kpi("Revenue", fmt_ugx(total_revenue))
    with c3: kpi("Avg Order Value", fmt_ugx(avg_order_value))
    with c4: kpi("Urgent Orders", f"{urgent_count:,}")
    with c5: kpi("Complaints", f"{complaints_opened:,}", f"{complaints_resolved} resolved")
    with c6: kpi("Avg Satisfaction", f"{avg_rating:.1f} / 5" if avg_rating is not None else "—", f"{len(ratings)} rated")

    if not period_df.empty:
        trend = period_df.copy()
        trend["date"] = pd.to_datetime(trend["order_created_at"], errors="coerce").dt.date
        trend["price_ugx"] = pd.to_numeric(trend.get("price_ugx"), errors="coerce").fillna(0)
        daily = trend.groupby("date").agg(orders=("order_id", "count"), revenue=("price_ugx", "sum")).reset_index()
        st.markdown("#### Orders & Revenue Trend")
        cc1, cc2 = st.columns(2)
        with cc1:
            st.caption("Orders per day")
            st.bar_chart(daily.set_index("date")[["orders"]])
        with cc2:
            st.caption("Revenue per day (UGX)")
            st.bar_chart(daily.set_index("date")[["revenue"]])

        st.markdown("#### Cakes vs Cookies")
        by_product = trend.groupby(trend.get("product_type", "Cake").fillna("Cake")).agg(
            orders=("order_id", "count"), revenue=("price_ugx", "sum")).reset_index()
        by_product.columns = ["product_type", "orders", "revenue"]
        cc1, cc2 = st.columns(2)
        with cc1:
            table(by_product, ["product_type", "orders", "revenue"])
        with cc2:
            st.caption("Revenue share")
            st.bar_chart(by_product.set_index("product_type")[["revenue"]])
    else:
        st.info("No orders in this period yet.")

    if not period_comp.empty:
        st.markdown("#### Complaints This Period — By Category & Severity")
        cc1, cc2 = st.columns(2)
        with cc1:
            cat_counts = period_comp["complaint_category"].value_counts().reset_index()
            cat_counts.columns = ["complaint_category", "count"]
            table(cat_counts, ["complaint_category", "count"])
        with cc2:
            sev_counts = period_comp["severity"].value_counts().reset_index()
            sev_counts.columns = ["severity", "count"]
            table(sev_counts, ["severity", "count"])
    st.divider()


def render_staff_accounts():
    st.markdown("## 🔐 Staff Accounts")
    st.caption("Only Owner/Admin can create or manage logins. Each staff member gets their own username and password — no more shared department passcodes.")
    accounts = load_table("staff_accounts")

    with st.expander("➕ Add New Staff Account"):
        a, b = st.columns(2)
        new_username = a.text_input("Username (lowercase, no spaces)", key="acct_new_username")
        new_fullname = b.text_input("Full name", key="acct_new_fullname")
        new_depts = st.multiselect("Department(s) — pick more than one if this person works across stages", DEPARTMENT_NAMES, key="acct_new_dept")
        new_hod = st.checkbox("This person is Head of Department", key="acct_new_hod")
        new_password = st.text_input("Temporary password (min. 6 characters — tell them to change it if you add that later)", type="password", key="acct_new_password")
        if st.button("Create Account", key="acct_create_btn", width='stretch'):
            uname = new_username.strip().lower()
            if not uname or not new_fullname.strip() or not new_password or not new_depts:
                st.error("Fill in username, full name, at least one department, and password.")
            elif len(new_password) < 6:
                st.error("Password should be at least 6 characters.")
            elif not accounts.empty and uname in accounts["username"].values:
                st.error(f"Username '{uname}' already exists — pick another.")
            else:
                digest, salt = hash_password(new_password)
                with connect() as conn:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO staff_accounts(username, full_name, password_hash, salt, department, departments, is_hod, is_active, created_at, created_by) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (uname, new_fullname.strip(), digest, salt, new_depts[0], ",".join(new_depts),
                         "Yes" if new_hod else "No", "Yes", now_iso(), st.session_state.get("staff_name", "Admin")))
                    conn.commit()
                    created = cur.rowcount > 0
                if created:
                    st.success(f"Account '{uname}' created for {new_fullname.strip()} ({', '.join(new_depts)})."); st.rerun()
                else:
                    st.error(f"Username '{uname}' was just taken (possibly by someone else at the same moment) — pick another.")

    if accounts.empty:
        st.info("No staff accounts yet besides the bootstrap admin.")
        return

    st.markdown("### All Accounts")
    display = accounts.copy()
    display["status"] = display.apply(
        lambda r: "🔒 Locked" if r.get("locked_until") and str(r.get("locked_until")) != "None" and str(r.get("locked_until")) != ""
        else ("Inactive" if r.get("is_active") != "Yes" else "Active"), axis=1)
    table(display, ["username", "full_name", "departments", "is_hod", "status", "last_login_at", "created_at"])

    st.markdown("### Manage an Account")
    pick = st.selectbox("Select account", accounts["username"].tolist(), key="acct_manage_pick")
    acct_row = accounts[accounts["username"] == pick].iloc[0]
    st.caption(f"{acct_row['full_name']} · {disp(acct_row.get('departments') or acct_row['department'])} · HOD: {acct_row['is_hod']}")
    a, b, c, d = st.columns(4)
    with a:
        if acct_row["is_active"] == "Yes":
            if st.button("🚫 Deactivate", key="acct_deactivate", width='stretch'):
                with connect() as conn:
                    conn.execute("UPDATE staff_accounts SET is_active='No' WHERE username=?", (pick,))
                    conn.commit()
                st.success(f"{pick} deactivated."); st.rerun()
        else:
            if st.button("✅ Reactivate", key="acct_reactivate", width='stretch'):
                with connect() as conn:
                    conn.execute("UPDATE staff_accounts SET is_active='Yes' WHERE username=?", (pick,))
                    conn.commit()
                st.success(f"{pick} reactivated."); st.rerun()
    with b:
        if st.button("🔓 Unlock Account", key="acct_unlock", width='stretch'):
            with connect() as conn:
                conn.execute("UPDATE staff_accounts SET failed_attempts=0, locked_until=NULL WHERE username=?", (pick,))
                conn.commit()
            st.success(f"{pick} unlocked."); st.rerun()
    with c:
        toggle_hod = st.button("🔁 Toggle HOD Flag", key="acct_toggle_hod", width='stretch')
        if toggle_hod:
            new_flag = "No" if acct_row["is_hod"] == "Yes" else "Yes"
            with connect() as conn:
                conn.execute("UPDATE staff_accounts SET is_hod=? WHERE username=?", (new_flag, pick))
                conn.commit()
            st.success(f"{pick} HOD flag set to {new_flag}."); st.rerun()

    st.markdown("#### Update Full Name")
    st.caption("Use this whenever a surname or spelling is confirmed later — no need to recreate the account.")
    new_name = st.text_input("Full name", value=acct_row["full_name"], key="acct_update_name")
    if st.button("Save Name", key="acct_save_name"):
        if not new_name.strip():
            st.error("Name can't be empty.")
        else:
            with connect() as conn:
                conn.execute("UPDATE staff_accounts SET full_name=? WHERE username=?", (new_name.strip(), pick))
                conn.commit()
            st.success(f"{pick}'s name updated to '{new_name.strip()}'."); st.rerun()

    st.markdown("#### Update Departments")
    current_depts = [d.strip() for d in str(acct_row.get("departments") or acct_row["department"]).split(",") if d.strip()]
    updated_depts = st.multiselect("Department(s)", DEPARTMENT_NAMES, default=current_depts, key="acct_update_depts")
    if st.button("Save Departments", key="acct_save_depts"):
        if not updated_depts:
            st.error("Pick at least one department.")
        else:
            with connect() as conn:
                conn.execute("UPDATE staff_accounts SET department=?, departments=? WHERE username=?",
                             (updated_depts[0], ",".join(updated_depts), pick))
                conn.commit()
            st.success(f"{pick}'s departments updated to: {', '.join(updated_depts)}."); st.rerun()

    st.markdown("#### Reset Password")
    new_pw = st.text_input("New password (min. 6 characters)", type="password", key="acct_reset_pw")
    if st.button("Reset Password", key="acct_reset_btn"):
        if len(new_pw) < 6:
            st.error("Password should be at least 6 characters.")
        else:
            digest, salt = hash_password(new_pw)
            with connect() as conn:
                conn.execute("UPDATE staff_accounts SET password_hash=?, salt=?, failed_attempts=0, locked_until=NULL WHERE username=?", (digest, salt, pick))
                conn.commit()
            st.success(f"Password reset for {pick}."); st.rerun()
    st.divider()


ALL_WORKFLOW_STATUSES = [
    "Awaiting Payment Confirmation", "Awaiting Deposit", "Payment Approval Required", "Payment Hold",
    "Deposit Confirmed", "Production Planned",
    "Baking", "Baking Correction Required",
    "Piling Incoming", "Piling", "Piling Correction Required",
    "Covering Incoming", "Covering", "Covering Correction Required",
    "Decorating Incoming", "Decorating", "Decoration Correction Required",
    "Studio Check",
    "Ready for Packaging", "Packaging",
    "Ready for Dispatch", "Delivery Run Assigned", "Arrived at Destination",
    "Finance Payment Confirmation Pending", "Payment Confirmed",
    "Follow-up Pending", "Follow-up Done", "Complaint Open",
]


def render_order_lookup_and_fix(df):
    st.markdown("## 🔍 Find & Fix an Order")
    st.caption("Look up any order to see exactly where it's sitting and who it's assigned to. If something looks stuck or wrongly assigned, correct it right here.")
    search = st.text_input("Search by Order ID, customer name, or assigned staff name", key="admin_order_search")
    if not search.strip():
        st.caption("Type something above to search.")
        return
    s = search.strip().lower()
    if df.empty:
        st.info("No orders yet.")
        return
    staff_cols = [c for c in ["baker_assigned", "piler_assigned", "coverer_assigned", "decorator_assigned", "driver_assigned"] if c in df.columns]
    mask = df["order_id"].astype(str).str.lower().str.contains(s, na=False) | df["customer_name"].astype(str).str.lower().str.contains(s, na=False)
    for c in staff_cols:
        mask = mask | df[c].astype(str).str.lower().str.contains(s, na=False)
    matches = df[mask]
    if matches.empty:
        st.info("No matching orders found.")
        return
    st.markdown(f"#### {len(matches)} match(es)")
    table(matches, ["order_id", "product_type", "customer_name", "workflow_status", "current_owner",
                     "baker_assigned", "piler_assigned", "coverer_assigned", "decorator_assigned", "driver_assigned", "next_action"])

    pick = st.selectbox("Select an order to inspect / fix", matches["order_id"].tolist(), key="admin_order_pick")
    row = matches[matches["order_id"] == pick].iloc[0]
    st.markdown(f"**Current status:** `{disp(row.get('workflow_status'))}` · **Owner:** `{disp(row.get('current_owner'))}` · **Next action:** {disp(row.get('next_action'))}")
    st.caption(f"Baker: {disp(row.get('baker_assigned'))} · Piler: {disp(row.get('piler_assigned'))} · "
               f"Coverer: {disp(row.get('coverer_assigned'))} · Decorator: {disp(row.get('decorator_assigned'))} · "
               f"Driver: {disp(row.get('driver_assigned'))}")

    with st.expander("✏️ Correct this order's status / owner / assignment"):
        all_statuses = ALL_WORKFLOW_STATUSES
        new_status = st.selectbox("Workflow status", all_statuses, index=all_statuses.index(row.get("workflow_status")) if row.get("workflow_status") in all_statuses else 0, key="admin_fix_status")
        new_owner = st.selectbox("Current owner (department)", DEPARTMENT_NAMES, index=DEPARTMENT_NAMES.index(row.get("current_owner")) if row.get("current_owner") in DEPARTMENT_NAMES else 0, key="admin_fix_owner")
        a, b = st.columns(2)
        new_coverer = a.text_input("Coverer assigned", value=disp(row.get("coverer_assigned")) if disp(row.get("coverer_assigned")) != "—" else "", key="admin_fix_coverer")
        new_decorator = b.text_input("Decorator assigned", value=disp(row.get("decorator_assigned")) if disp(row.get("decorator_assigned")) != "—" else "", key="admin_fix_decorator")
        fix_by = st.text_input("Corrected by", value=st.session_state.get("staff_name", "Admin"), key="admin_fix_by")
        if st.button("Save Correction", key="admin_fix_save", width='stretch'):
            update_order(row["order_id"], {
                "workflow_status": new_status, "current_owner": new_owner,
                "coverer_assigned": new_coverer, "decorator_assigned": new_decorator,
            }, fix_by, "Manual Admin Correction", "Admin")
            st.success(f"Order {row['order_id']} corrected."); st.rerun()

    with st.expander("📝 Correct order entry details (typos, wrong price/phone/flavour, etc.)"):
        st.caption("For mistakes made when the order was first typed up — this edits the order in place, so it never needs to move through the workflow again.")
        a, b = st.columns(2)
        e_name = a.text_input("Customer name", value=disp(row.get("customer_name")) if disp(row.get("customer_name")) != "—" else "", key="admin_fix_name")
        e_phone = b.text_input("Customer phone", value=disp(row.get("customer_number")) if disp(row.get("customer_number")) != "—" else "", key="admin_fix_phone")
        a, b = st.columns(2)
        e_flavours = a.text_input("Flavours", value=disp(row.get("flavours")) if disp(row.get("flavours")) != "—" else "", key="admin_fix_flavours")
        e_price = b.number_input("Price (UGX)", min_value=0.0, step=5000.0, value=float(row.get("price_ugx") or 0), key="admin_fix_price")
        a, b = st.columns(2)
        e_location = a.text_input("Delivery / pickup location", value=disp(row.get("location")) if disp(row.get("location")) != "—" else "", key="admin_fix_location")
        e_due_date = b.text_input("Due date (YYYY-MM-DD)", value=disp(row.get("due_date")) if disp(row.get("due_date")) != "—" else "", key="admin_fix_due_date")
        e_design = st.text_area("Design description", value=disp(row.get("design_description")) if disp(row.get("design_description")) != "—" else "", key="admin_fix_design")
        fix_by2 = st.text_input("Corrected by", value=st.session_state.get("staff_name", "Admin"), key="admin_fix_by2")
        if st.button("Save Entry Correction", key="admin_fix_entry_save", width='stretch'):
            update_order(row["order_id"], {
                "customer_name": e_name.strip(), "customer_number": e_phone.strip(),
                "flavours": e_flavours.strip(), "price_ugx": e_price,
                "location": e_location.strip(), "due_date": e_due_date.strip(),
                "design_description": e_design.strip(),
            }, fix_by2, "Manual Entry Correction (typo/price/phone/etc.)", "Admin")
            st.success(f"Order {row['order_id']} entry details corrected — no need for it to move through the workflow again."); st.rerun()
    st.divider()


def render_backup_restore():
    st.markdown("## 💾 Backup & Restore")
    st.warning(
        "**Important if this is hosted on Streamlit Community Cloud:** the free tier does not guarantee your database "
        "survives a restart, redeploy, or long period of inactivity. **Download a backup at the end of every business day** "
        "and keep it somewhere safe (email it to yourself, save to Google Drive, etc.) so you can restore instantly if the app ever resets.")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### Download a Backup")
        st.caption("One click — downloads the entire database exactly as it is right now.")
        if DATABASE_FILE.exists():
            backup_bytes = DATABASE_FILE.read_bytes()
            backup_name = f"cake_album_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            st.download_button("⬇️ Download Backup Now", data=backup_bytes, file_name=backup_name,
                                mime="application/octet-stream", width='stretch')
        else:
            st.caption("No database file found yet.")
    with col2:
        st.markdown("#### Restore From a Backup")
        st.caption("⚠️ This replaces everything currently in the app with what's in the backup file. Use this only if the live data was actually lost.")
        uploaded = st.file_uploader("Upload a .db backup file", type=["db"], key="restore_upload")
        if uploaded is not None:
            temp_path = Path("/tmp") / f"restore_check_{uuid.uuid4().hex[:8]}.db"
            temp_path.write_bytes(uploaded.getbuffer())
            try:
                check_conn = sqlite3.connect(temp_path)
                order_count = check_conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
                account_count = check_conn.execute("SELECT COUNT(*) FROM staff_accounts").fetchone()[0]
                try:
                    video_count = check_conn.execute("SELECT COUNT(*) FROM order_videos").fetchone()[0]
                except sqlite3.OperationalError:
                    video_count = 0
                check_conn.close()
                st.info(f"This backup contains **{order_count} order(s)**, **{account_count} staff account(s)**, and **{video_count} reference video(s)**.")
                confirm = st.checkbox("I understand this will overwrite all current data with this backup", key="restore_confirm")
                if st.button("🔁 Restore This Backup", disabled=not confirm, width='stretch'):
                    import shutil
                    shutil.copy(temp_path, DATABASE_FILE)
                    refresh_data()
                    st.success("Restored. Reloading...")
                    st.rerun()
            except Exception as e:
                st.error(f"This doesn't look like a valid backup of this app's database: {e}")
            finally:
                temp_path.unlink(missing_ok=True)
    st.divider()


PUSH_PROVIDER_LABELS = {
    "fcm.googleapis.com": "Android / Chrome",
    "android.googleapis.com": "Android / Chrome",
    "updates.push.services.mozilla.com": "Firefox",
    "web.push.apple.com": "iPhone / Safari",
    "wns2-": "Windows / Edge",
}


def push_provider_label(endpoint: str) -> str:
    """Human-friendly guess at which device/browser an endpoint belongs to."""
    try:
        host = urlparse(endpoint).netloc.lower()
    except Exception:
        return "Unknown"
    for needle, label in PUSH_PROVIDER_LABELS.items():
        if needle in host:
            return label
    return host or "Unknown"


def load_push_subscriptions():
    """All stored push subscriptions, newest first."""
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT username, department, endpoint, created_at
                   FROM push_subscriptions ORDER BY created_at DESC"""
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in rows]


def delete_push_subscription(endpoint: str):
    with connect() as conn:
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
        conn.commit()


def prune_dead_push_subscriptions(send_test=False, title="Cake Album Operations",
                                  body="Checking this device is still reachable."):
    """Ping every stored subscription with a real (or silent TTL=0) web-push request and
    delete the ones the push service rejects as gone (404 / 410).

    Returns (checked, removed, failed_but_kept, messages)."""
    messages = []
    if not VAPID_PRIVATE_KEY_FILE.exists():
        return 0, 0, 0, ["VAPID key file missing on the server — cannot verify subscriptions."]
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return 0, 0, 0, ["pywebpush is not installed — run: pip install pywebpush py-vapid"]

    subs = load_push_subscriptions()
    dead, kept_failures = [], 0
    for sub in subs:
        try:
            webpush(
                subscription_info=json.loads(
                    _subscription_json_for(sub["endpoint"]) or "{}"),
                data=json.dumps({"title": title, "body": body}),
                vapid_private_key=str(VAPID_PRIVATE_KEY_FILE),
                vapid_claims=dict(VAPID_CLAIMS),
                ttl=86400 if send_test else 0,
                headers={"Urgency": "high" if send_test else "very-low"},
            )
        except WebPushException as e:
            status = e.response.status_code if e.response is not None else None
            if status in (404, 410):
                dead.append(sub["endpoint"])
                messages.append(f"Removed dead device for @{sub['username']} ({status}).")
            else:
                kept_failures += 1
                messages.append(f"@{sub['username']}: push service returned {status or 'no response'} — kept.")
        except Exception as e:
            kept_failures += 1
            messages.append(f"@{sub['username']}: {type(e).__name__} — kept.")
    for endpoint in dead:
        delete_push_subscription(endpoint)
    return len(subs), len(dead), kept_failures, messages


def _subscription_json_for(endpoint: str):
    with connect() as conn:
        row = conn.execute(
            "SELECT subscription_json FROM push_subscriptions WHERE endpoint=?", (endpoint,)
        ).fetchone()
    return row[0] if row else None


def build_daily_report_html(report_date=None):
    """Builds the daily report as HTML: today's sales, pending balances, and drivers still
    holding unreconciled cash from the field. Returns (html, summary_dict) - the summary is
    used both in the email subject line and to show a quick preview in the app before sending."""
    report_date = report_date or date.today()
    df = load_orders()
    day_str = report_date.strftime("%Y-%m-%d")

    day_orders = df[pd.to_datetime(df["order_created_at"], errors="coerce").dt.date == report_date] if not df.empty else df.iloc[0:0]
    total_sales = float(pd.to_numeric(day_orders["price_ugx"], errors="coerce").fillna(0).sum()) if not day_orders.empty else 0.0

    pending_balance_orders = df[pd.to_numeric(df.get("balance"), errors="coerce").fillna(0) > 0] if not df.empty and "balance" in df.columns else df.iloc[0:0]
    total_pending = float(pd.to_numeric(pending_balance_orders.get("balance"), errors="coerce").fillna(0).sum()) if not pending_balance_orders.empty else 0.0

    unreconciled = df[df.get("cash_cleared_status") == "Pending Physical Handover"] if not df.empty and "cash_cleared_status" in df.columns else df.iloc[0:0]
    unreconciled_total = float(pd.to_numeric(unreconciled.get("balance"), errors="coerce").fillna(0).sum()) if not unreconciled.empty else 0.0

    def df_to_html_table(d, cols, empty_msg):
        if d.empty:
            return f"<p style='color:#666'>{empty_msg}</p>"
        rows = "".join(
            "<tr>" + "".join(f"<td style='padding:4px 8px;border:1px solid #ddd'>{disp(r.get(c))}</td>" for c in cols) + "</tr>"
            for _, r in d.iterrows()
        )
        header = "".join(f"<th style='padding:4px 8px;border:1px solid #ddd;background:#F0E6F5;text-align:left'>{c}</th>" for c in cols)
        return f"<table style='border-collapse:collapse;width:100%;font-size:13px'><tr>{header}</tr>{rows}</table>"

    sales_html = df_to_html_table(day_orders, ["order_id", "customer_name", "product_type", "cake_category", "price_ugx", "payment_method"],
                                   "No orders entered today.")
    balances_html = df_to_html_table(pending_balance_orders, ["order_id", "customer_name", "balance", "due_date", "workflow_status"],
                                      "No orders with a pending balance.")
    driver_summary = (unreconciled.groupby("driver_assigned")["balance"].sum().reset_index()
                       if not unreconciled.empty and "driver_assigned" in unreconciled.columns else pd.DataFrame(columns=["driver_assigned", "balance"]))
    drivers_html = df_to_html_table(driver_summary, ["driver_assigned", "balance"], "No drivers currently holding unreconciled cash.")

    html = f"""
    <div style="font-family:sans-serif;color:#1A1420;max-width:800px">
    <h2 style="color:#4B2A5C">Cake Album — Daily Report for {day_str}</h2>
    <h3>💰 Today's Sales — Total: {fmt_ugx(total_sales)} ({len(day_orders)} order(s))</h3>
    {sales_html}
    <h3 style="margin-top:24px">⏳ Pending Balances — Total Owed: {fmt_ugx(total_pending)} ({len(pending_balance_orders)} order(s))</h3>
    {balances_html}
    <h3 style="margin-top:24px">🚚 Drivers Not Yet Reconciled — Total: {fmt_ugx(unreconciled_total)}</h3>
    {drivers_html}
    <p style="margin-top:24px;color:#999;font-size:12px">Generated automatically by Cake Album Operations.</p>
    </div>
    """
    summary = {"date": day_str, "sales_count": len(day_orders), "total_sales": total_sales,
               "pending_count": len(pending_balance_orders), "total_pending": total_pending,
               "unreconciled_drivers": len(driver_summary), "unreconciled_total": unreconciled_total}
    return html, summary


def send_daily_report_email(to_address="cakealbumug@gmail.com", report_date=None):
    """Sends the daily report via Resend's HTTPS API rather than raw SMTP. DigitalOcean (and
    most cloud hosts) block outbound SMTP ports 25/465/587 on every server by default to stop
    spam abuse - this is a platform-level block, not something fixable from inside the server,
    so sending over HTTPS (443, the same port all web traffic already uses and is never
    blocked) is the standard way around it. Reads the key from RESEND_API_KEY and the sender
    address from RESEND_FROM_EMAIL - both environment variables, same pattern as everything
    else. Returns (success, message) so the caller can show a clear result either way."""
    import requests
    resend_key = os.environ.get("RESEND_API_KEY")
    from_email = os.environ.get("RESEND_FROM_EMAIL")
    if not resend_key or not from_email:
        return False, ("Email isn't set up yet on this server — RESEND_API_KEY and RESEND_FROM_EMAIL "
                        "environment variables aren't set. See the setup guide.")
    html, summary = build_daily_report_html(report_date)
    subject = f"Cake Album Daily Report — {summary['date']} — Sales {fmt_ugx(summary['total_sales'])}"
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
            json={"from": from_email, "to": [to_address], "subject": subject, "html": html},
            timeout=20,
        )
        if resp.status_code in (200, 201):
            return True, f"Report sent to {to_address}."
        return False, f"Failed to send: HTTP {resp.status_code}: {resp.text[:300]}"
    except Exception as e:
        return False, f"Failed to send: {type(e).__name__}: {e}"


def get_sheet_client():
    """Connects to Google Sheets using a service account. Reads the key file path from
    GOOGLE_SHEETS_CREDENTIALS_FILE and the target sheet name from GOOGLE_SHEETS_NAME -
    both environment variables, same pattern as the email setup. Returns (worksheet, error_message)."""
    if not GSPREAD_AVAILABLE:
        return None, "The gspread and google-auth packages aren't installed on this server yet. See the setup guide."
    creds_path = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_FILE")
    sheet_name = os.environ.get("GOOGLE_SHEETS_NAME")
    if not creds_path or not sheet_name:
        return None, "Google Sheets isn't set up yet - GOOGLE_SHEETS_CREDENTIALS_FILE and GOOGLE_SHEETS_NAME environment variables aren't set. See the setup guide."
    if not Path(creds_path).exists():
        return None, f"Credentials file not found at {creds_path}."
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = GoogleCredentials.from_service_account_file(creds_path, scopes=scopes)
        client = gspread.authorize(creds)
        sheet = client.open(sheet_name).sheet1
        return sheet, None
    except Exception as e:
        return None, f"Couldn't connect to Google Sheets: {type(e).__name__}: {e}"


SHEET_SYNC_COLUMNS = [
    "order_id", "customer_name", "customer_number", "product_type", "cake_category",
    "flavours", "cake_size_value", "cake_shape", "price_ugx", "balance", "payment_arrangement",
    "order_type", "urgency_level", "due_date", "workflow_status", "order_created_at",
]


def sync_order_to_sheet(order_row):
    """Appends a single order to the Google Sheet as a new row. Called right after an
    order is created. Fails silently (logged, not shown to the person creating the order)
    since a sync hiccup shouldn't block someone from placing a real order - Procurement,
    Finance, etc. all still work normally from the database regardless of sheet sync status."""
    sheet, error = get_sheet_client()
    if error:
        print(f"[SHEETS SYNC] Skipped - {error}", flush=True)
        return False, error
    try:
        row = [str(order_row.get(c, "")) for c in SHEET_SYNC_COLUMNS]
        sheet.append_row(row)
        return True, None
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        print(f"[SHEETS SYNC] Failed for {order_row.get('order_id')}: {err}", flush=True)
        return False, err


def render_sheets_sync_panel():
    st.markdown("## 📊 Google Sheets Sync")
    st.caption("Every new order created in the app is automatically sent to your Google Sheet as it happens. "
               "Use the button below to catch up any orders placed before this was set up.")
    sheet, error = get_sheet_client()
    if error:
        st.warning(error)
    else:
        st.success(f"Connected to Google Sheets — appending new orders automatically.")
    if st.button("📤 Sync All Existing Orders Now (catch-up)", width='stretch'):
        df = load_orders()
        if df.empty:
            st.info("No orders to sync.")
        else:
            sheet2, error2 = get_sheet_client()
            if error2:
                st.error(error2)
            else:
                with st.spinner(f"Syncing {len(df)} order(s)..."):
                    existing_ids = set()
                    try:
                        existing_rows = sheet2.get_all_values()
                        header = existing_rows[0] if existing_rows else []
                        if "order_id" in header:
                            id_col = header.index("order_id")
                            existing_ids = {r[id_col] for r in existing_rows[1:] if len(r) > id_col}
                    except Exception:
                        pass
                    to_sync = df[~df["order_id"].isin(existing_ids)] if existing_ids else df
                    rows = [[str(r.get(c, "")) for c in SHEET_SYNC_COLUMNS] for _, r in to_sync.iterrows()]
                    failed = 0
                    if rows:
                        try:
                            sheet2.append_rows(rows)
                        except Exception as e:
                            failed = len(rows)
                            st.error(f"Bulk sync failed: {type(e).__name__}: {e}")
                    if failed == 0:
                        st.success(f"Synced {len(rows)} order(s) to the sheet ({len(df) - len(rows)} were already there).")


def render_daily_report_panel():
    st.markdown("## 📧 Daily Report Email")
    st.caption("Today's sales, pending balances, and drivers still holding unreconciled cash — sent as one email.")
    report_date = st.date_input("Report date", value=date.today(), key="daily_report_date")
    to_addr = st.text_input("Send to", value="cakealbumug@gmail.com", key="daily_report_to")
    html, summary = build_daily_report_html(report_date)
    a, b, c = st.columns(3)
    with a: kpi("Sales Today", fmt_ugx(summary["total_sales"]))
    with b: kpi("Pending Balances", fmt_ugx(summary["total_pending"]))
    with c: kpi("Unreconciled (Drivers)", fmt_ugx(summary["unreconciled_total"]))
    with st.expander("Preview the report"):
        st.markdown(html, unsafe_allow_html=True)
    if not os.environ.get("RESEND_API_KEY") or not os.environ.get("RESEND_FROM_EMAIL"):
        st.warning("Email sending isn't configured on this server yet — see the setup guide to add RESEND_API_KEY and RESEND_FROM_EMAIL.")
    if st.button("📧 Send This Report Now", width='stretch'):
        with st.spinner("Sending..."):
            success, message = send_daily_report_email(to_addr.strip(), report_date)
        if success:
            st.success(message)
        else:
            st.error(message)


def render_push_subscription_admin():
    """Owner/Admin panel: review every device signed up for background notifications and
    clear out the ones that no longer exist."""
    st.markdown("## 🔔 Push Notification Devices")
    if not VAPID_PUBLIC_KEY:
        st.warning("No VAPID key pair on this server yet — background push is not configured.")

    subs = load_push_subscriptions()
    if not subs:
        st.info("No devices are subscribed yet. Staff must open the site on their phone and tap "
                "\"Turn on notifications\" (iPhone users must Add to Home Screen first).")
        return

    rows = []
    for s in subs:
        rows.append({
            "Staff": s["username"],
            "Departments": s["department"] or "",
            "Device": push_provider_label(s["endpoint"]),
            "Registered": (s["created_at"] or "")[:16].replace("T", " "),
            "Endpoint": s["endpoint"][:48] + "…",
        })
    st.caption(f"{len(rows)} active subscription(s).")
    st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("🧹 Check devices & remove dead ones", width='stretch',
                     key="push_prune_silent"):
            checked, removed, kept, msgs = prune_dead_push_subscriptions(send_test=False)
            st.success(f"Checked {checked} device(s) — removed {removed}, kept {kept} that failed for other reasons.")
            for m in msgs:
                st.caption(m)
            st.rerun()
    with c2:
        if st.button("📨 Send test alert to all devices", width='stretch',
                     key="push_prune_test"):
            checked, removed, kept, msgs = prune_dead_push_subscriptions(
                send_test=True, body="Test alert from the Owner/Admin panel.")
            st.success(f"Sent to {checked - removed - kept} device(s); removed {removed} dead, {kept} other failure(s).")
            for m in msgs:
                st.caption(m)
            st.rerun()

    with st.expander("Remove a single device"):
        options = {
            f"@{s['username']} · {push_provider_label(s['endpoint'])} · {(s['created_at'] or '')[:10]}": s["endpoint"]
            for s in subs
        }
        picked = st.selectbox("Device", list(options.keys()), key="push_remove_pick")
        if st.button("Remove this device", key="push_remove_btn"):
            delete_push_subscription(options[picked])
            st.success("Device removed. It can re-subscribe by tapping \"Turn on notifications\" again.")
            st.rerun()

def render_admin():
    page_header("👑 Owner / Admin Command Center", f"{APP_VERSION} operational visibility.")
    df = load_orders()
    comp = load_table("complaints")
    qc = load_table("stage_quality_checks")
    runs = load_table("delivery_runs")
    render_management_overview(df, comp)
    render_backup_restore()
    render_order_lookup_and_fix(df)
    render_staff_accounts()
    render_push_subscription_admin()
    render_daily_report_panel()
    render_sheets_sync_panel()
    st.markdown("## 🔧 Operational Detail")
    c1,c2,c3,c4 = st.columns(4)
    with c1: kpi("Orders", f"{len(df):,}")
    with c2: kpi("Active", f"{int(~col(df,'workflow_status').isin(['Follow-up Done']).sum()):,}")
    with c3: kpi("Quality Issues", f"{int((qc['check_status']=='Rejected').sum()) if not qc.empty else 0:,}")
    with c4: kpi("Open Complaints", f"{int((comp['complaint_status']!='Closed').sum()) if not comp.empty else 0:,}")
    st.markdown("### Workflow Status")
    if "workflow_status" in df.columns:
        status_counts = col(df, "workflow_status").value_counts().reset_index()
        status_counts.columns = ["workflow_status", "count"]
        table(status_counts, ["workflow_status", "count"])
    st.markdown("### Quality Issues")
    table(qc.sort_values("checked_at", ascending=False).head(50) if not qc.empty else qc, ["order_id","from_stage","to_stage","check_status","issue_category","issue_description","responsible_person","checked_by","checked_at"])
    st.markdown("### Delivery Runs")
    table(runs, ["run_id","driver_name","run_status","run_started_at","run_completed_at","created_at"])
    st.markdown("### Oven Temperature & Timing Log")
    oven = load_table("oven_logs")
    if not oven.empty:
        start_dt = pd.to_datetime(oven["oven_start_at"], errors="coerce")
        stop_dt = pd.to_datetime(oven["oven_stop_at"], errors="coerce")
        oven["duration_minutes"] = ((stop_dt - start_dt).dt.total_seconds() / 60).round(0)
        table(oven.sort_values("oven_start_at", ascending=False).head(50),
              ["order_id", "flavour", "product_type", "start_temp_c", "stop_temp_c", "duration_minutes", "oven_start_at", "oven_stop_at"])
    else:
        st.caption("No oven log entries yet.")

    st.markdown("### Baked Layer Inventory")
    inv = load_table("baked_cake_inventory")
    table(inv.sort_values("date_baked", ascending=False).head(50) if not inv.empty else inv,
          ["id","date_baked","flavour","cake_size_value","cake_shape","layers_available","quantity_available","baker","storage_location","inventory_status","reserved_order_id"])

    st.markdown("### Extra Cake Layers for the Day")
    extra = load_table("extra_baking_assignments")
    table(extra.sort_values("created_at", ascending=False).head(50) if not extra.empty else extra,
          ["id","plan_date","flavour","cake_size_value","cake_shape","layers_per_cake","cake_units","total_layers","assigned_baker","reason","assignment_status"])

    st.markdown("### Layer Reconciliations")
    rec = load_table("layer_inventory_reconciliation")
    table(rec.sort_values("confirmed_at", ascending=False).head(30) if not rec.empty else rec,
          ["reconciliation_date","confirmed_by","opening_layers","layers_used","closing_layers","procurement_balance","confirmed_at"])

    st.markdown("### Complaints")
    table(comp, ["complaint_id","order_id","customer_name","complaint_category","severity","complaint_status","responsible_department","opened_at"])


PAGES = {
    "Owner / Admin": render_admin,
    "Customer Care": render_customer_care,
    "Finance": render_finance,
    "Production Planning": render_production_planning,
    "Baking": render_baking,
    "Filling / Piling": render_piling,
    "Coating / Covering": render_covering,
    "Design & Innovation": render_design_innovation,
    "Decoration": render_decoration,
    "Studio / Final QC": render_studio_qc,
    "Dispatch / Driver": render_driver,
    "Procurement": render_procurement,
    "Team Chat & AI": render_team_hub,
}

def render_login():
    st.markdown("<div style='height:4vh'></div>", unsafe_allow_html=True)
    left, mid, right = st.columns([1, 1.3, 1])
    with mid:
        st.markdown(
            f"<div style='text-align:center;padding:8px 0 22px;'>"
            f"<img src='{LOGO_DATA_URI}' style='height:120px;width:auto;'>"
            f"<p style='font-family:Fraunces,serif;font-size:1.05rem;color:var(--plum-deep);margin:10px 0 2px;font-weight:600;'>{APP_TAGLINE}</p>"
            f"<p style='color:var(--muted-soft);font-size:.8rem;letter-spacing:.03em;'>OPERATIONS PLATFORM &nbsp;·&nbsp; {APP_VERSION}</p>"
            f"</div>", unsafe_allow_html=True)
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Enter", width='stretch')
        if submitted:
            ok, message, account = authenticate(username, password)
            if ok:
                apply_login_to_session(account)
                token = create_app_session(account["username"])
                st.session_state["_session_token"] = token
                # Putting the token in the address bar is what survives a refresh
                # or a back-button navigation.
                st.query_params[SESSION_QUERY_PARAM] = token
                st.rerun()
            else:
                st.error(message)
        st.caption("Forgot your password or need an account? Ask your Owner/Admin to reset or create it for you.")


def render_sidebar():
    render_theme_toggle()
    st.sidebar.markdown(f"<img src='{LOGO_DATA_URI}' style='height:60px;width:auto;'>", unsafe_allow_html=True)
    st.sidebar.caption(APP_TAGLINE)
    st.sidebar.caption(APP_VERSION)
    st.sidebar.divider()
    role_tag = " · HOD" if st.session_state.get("is_hod") else ""
    st.sidebar.write(f"Signed in as: **{first_name(st.session_state.get('staff_name',''))}**")
    st.sidebar.caption(f"@{st.session_state.get('username','')} · {st.session_state.department}{role_tag}")
    st.sidebar.caption(f"DB: {DATABASE_FILE.name}")

    with st.sidebar.expander("🔑 Change My Password"):
        cur_pw = st.text_input("Current password", type="password", key="sb_cur_pw")
        new_pw = st.text_input("New password (min. 6 characters)", type="password", key="sb_new_pw")
        if st.button("Update Password", key="sb_update_pw"):
            ok, _, _ = authenticate(st.session_state.get("username", ""), cur_pw)
            if not ok:
                st.error("Current password is incorrect.")
            elif len(new_pw) < 6:
                st.error("New password should be at least 6 characters.")
            else:
                digest, salt = hash_password(new_pw)
                with connect() as conn:
                    conn.execute("UPDATE staff_accounts SET password_hash=?, salt=? WHERE username=?",
                                 (digest, salt, st.session_state.get("username", "")))
                    conn.commit()
                st.success("Password updated.")

    if st.sidebar.button("Log out", width='stretch'):
        delete_app_session(st.session_state.get("_session_token"))
        for key in ("authenticated", "department", "departments", "staff_name", "is_hod", "username", "login_at", "_session_token"):
            st.session_state.pop(key, None)
        try:
            del st.query_params[SESSION_QUERY_PARAM]
        except Exception:
            pass
        st.rerun()

    forced_page = st.session_state.pop("_force_page", None)
    if forced_page == "Team Chat & AI":
        return "Team Chat & AI"

    allowed = st.session_state.get("departments") or [st.session_state.department]
    allowed = ["Studio / Final QC" if d == "Packaging" else d for d in allowed]
    allowed = list(dict.fromkeys(allowed))
    # Everyone can reach the shared chat / AI assistant page.
    if "Team Chat & AI" not in allowed:
        allowed = allowed + ["Team Chat & AI"]
    if "Owner / Admin" in allowed:
        st.sidebar.divider()
        page_names = list(PAGES.keys())
        last_page = st.query_params.get("p")
        if isinstance(last_page, list):
            last_page = last_page[0] if last_page else None
        index = page_names.index(last_page) if last_page in page_names else 0
        picked = st.sidebar.radio("Go to", page_names, index=index)
        if picked != last_page:
            st.query_params["p"] = picked
        return picked
    if len(allowed) > 1:
        st.sidebar.divider()
        df_counts = load_orders()
        labels = []
        for dept in allowed:
            if dept == "Team Chat & AI":
                labels.append("💬 Team Chat & AI")
                continue
            statuses = DEPARTMENT_STAGE_STATUSES.get(dept, [])
            count = int(df_counts["workflow_status"].isin(statuses).sum()) if statuses and not df_counts.empty and "workflow_status" in df_counts.columns else 0
            labels.append(f"{dept}" + (f"  🔴 {count} waiting" if count > 0 else "  — none waiting"))
        st.sidebar.caption("Jobs waiting in each of your departments:")
        picked_label = st.sidebar.radio("Go to", labels)
        choice = allowed[labels.index(picked_label)]
        if choice == "Team Chat & AI":
            return choice
        st.session_state.department = choice
        return choice
    return st.session_state.department


def session_expired():
    login_at = st.session_state.get("login_at")
    if not login_at:
        return False
    try:
        elapsed_hours = (datetime.now() - datetime.fromisoformat(login_at)).total_seconds() / 3600
        return elapsed_hours > SESSION_TIMEOUT_HOURS
    except Exception:
        return False



# ---------------------------------------------------------------------------
# PERSISTENT LOGIN
#
# st.session_state only lives as long as one websocket connection, so a browser
# refresh, a back-button navigation, or the one-time ?push_sub= reload used to
# throw people back to the login screen. A signed-in device now carries a random
# session token in the address bar; the token is looked up here on every run and
# the session is rebuilt from the database, so the page they came back to is the
# page they land on.
# ---------------------------------------------------------------------------
SESSION_QUERY_PARAM = "s"


def ensure_session_table():
    with connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS app_sessions(
            token_hash TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            created_at TEXT,
            last_seen_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS draft_entries(
            token_hash TEXT NOT NULL,
            field_key TEXT NOT NULL,
            value TEXT,
            updated_at TEXT,
            PRIMARY KEY (token_hash, field_key))""")
        conn.commit()


def save_draft_field(field_key: str, value: str):
    """Saves an in-progress, not-yet-submitted field value tied to this browser's session
    token - so if the window genuinely refreshes mid-entry (not just a normal Streamlit
    rerun, which already preserves widget state on its own), the typed value survives
    and nobody has to start over. Silently does nothing if there's no active session
    token yet (e.g. right at login) - this is a convenience, not something that should
    ever block the actual form."""
    token = st.session_state.get("_session_token")
    if not token:
        return
    try:
        with connect() as conn:
            conn.execute("""INSERT INTO draft_entries(token_hash, field_key, value, updated_at)
                            VALUES(?,?,?,?)
                            ON CONFLICT(token_hash, field_key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                         (_session_token_hash(token), field_key, value, now_iso()))
            conn.commit()
    except Exception:
        pass


def load_draft_field(field_key: str) -> str:
    token = st.session_state.get("_session_token")
    if not token:
        return ""
    try:
        with connect() as conn:
            row = conn.execute("SELECT value FROM draft_entries WHERE token_hash=? AND field_key=?",
                               (_session_token_hash(token), field_key)).fetchone()
            return row[0] if row and row[0] else ""
    except Exception:
        return ""


def clear_draft_field(field_key: str):
    token = st.session_state.get("_session_token")
    if not token:
        return
    try:
        with connect() as conn:
            conn.execute("DELETE FROM draft_entries WHERE token_hash=? AND field_key=?",
                         (_session_token_hash(token), field_key))
            conn.commit()
    except Exception:
        pass


def _session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_app_session(username: str) -> str:
    """Issue a browser session token. Only its hash is stored."""
    token = secrets_mod.token_urlsafe(32)
    ensure_session_table()
    stamp = now_iso()
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO app_sessions(token_hash, username, created_at, last_seen_at) VALUES(?,?,?,?)",
                     (_session_token_hash(token), username, stamp, stamp))
        # Housekeeping: drop tokens older than the timeout so the table stays small.
        cutoff = (datetime.now() - timedelta(hours=SESSION_TIMEOUT_HOURS)).isoformat()
        conn.execute("DELETE FROM app_sessions WHERE created_at < ?", (cutoff,))
        conn.commit()
    return token


def resolve_app_session(token: str):
    """Return (username, created_at) for a live token, or (None, None)."""
    if not token:
        return None, None
    ensure_session_table()
    try:
        with connect() as conn:
            row = conn.execute("SELECT username, created_at FROM app_sessions WHERE token_hash=?",
                               (_session_token_hash(token),)).fetchone()
            if row is None:
                return None, None
            try:
                age_hours = (datetime.now() - datetime.fromisoformat(row[1])).total_seconds() / 3600
                if age_hours > SESSION_TIMEOUT_HOURS:
                    conn.execute("DELETE FROM app_sessions WHERE token_hash=?", (_session_token_hash(token),))
                    conn.commit()
                    return None, None
            except Exception:
                pass
            conn.execute("UPDATE app_sessions SET last_seen_at=? WHERE token_hash=?", (now_iso(), _session_token_hash(token)))
            conn.commit()
            return row[0], row[1]
    except Exception as e:
        print(f"[SESSION] Could not resolve session token: {e}", flush=True)
        return None, None


def delete_app_session(token: str):
    if not token:
        return
    try:
        ensure_session_table()
        with connect() as conn:
            conn.execute("DELETE FROM app_sessions WHERE token_hash=?", (_session_token_hash(token),))
            conn.commit()
    except Exception:
        pass


def apply_login_to_session(account, login_at=None):
    """Fill st.session_state from a staff_accounts row — used by both a fresh login
    and a restored session, so the two can never drift apart."""
    depts = [d.strip() for d in (account["departments"] or account["department"]).split(",") if d.strip()]
    depts = ["Studio / Final QC" if d == "Packaging" else d for d in depts]
    depts = list(dict.fromkeys(depts))
    st.session_state.authenticated = True
    st.session_state.departments = depts
    st.session_state.department = depts[0] if depts else account["department"]
    st.session_state.staff_name = account["full_name"]
    st.session_state.is_hod = account["is_hod"] == "Yes"
    st.session_state.username = account["username"]
    st.session_state.login_at = login_at or datetime.now().isoformat()


def restore_session_from_query():
    """Rebuild a signed-in session from the ?s= token in the address bar."""
    if st.session_state.get("authenticated"):
        return
    try:
        token = st.query_params.get(SESSION_QUERY_PARAM)
    except Exception:
        return
    if isinstance(token, list):
        token = token[0] if token else None
    if not token:
        return
    username, created_at = resolve_app_session(token)
    if not username:
        try:
            del st.query_params[SESSION_QUERY_PARAM]
        except Exception:
            pass
        return
    with connect() as conn:
        account = conn.execute("SELECT * FROM staff_accounts WHERE username=?", (username,)).fetchone()
    if account is None or account["is_active"] != "Yes":
        delete_app_session(token)
        return
    apply_login_to_session(account, login_at=created_at)
    st.session_state["_session_token"] = token


@st.cache_resource
def _init_schema_and_roster_once():
    """Schema migrations and roster bootstrapping only need to happen once when the
    server starts, not on every rerun from every logged-in person. Same reasoning as
    _init_push_assets_once() above - repeatedly re-checking table structure under
    real concurrent, multi-user load adds needless database contention."""
    ensure_release_2_schema()
    ensure_bootstrap_admin()
    ensure_default_staff_roster()
    apply_staff_name_corrections()
    ensure_session_table()
    return True


def main():
    inject_css()
    _init_schema_and_roster_once()
    try:
        ensure_collaboration_schema()
    except Exception as _e:
        print(f"[COMMENTS] schema init failed: {_e}", flush=True)
    st.session_state["_script_run_id"] = st.session_state.get("_script_run_id", 0) + 1
    st.session_state["_idea_widget_calls"] = 0
    st.session_state["_refresh_widget_calls"] = 0
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
        st.session_state.department = None
        st.session_state.departments = []
        st.session_state.staff_name = ""
        st.session_state.is_hod = False
        st.session_state.username = ""
    restore_session_from_query()
    if st.session_state.authenticated and session_expired():
        delete_app_session(st.session_state.get("_session_token"))
        for key in ("authenticated", "department", "departments", "staff_name", "is_hod", "username", "login_at", "_session_token"):
            st.session_state.pop(key, None)
        try:
            del st.query_params[SESSION_QUERY_PARAM]
        except Exception:
            pass
        st.info(f"You were logged out after {SESSION_TIMEOUT_HOURS} hours for security. Please log back in.")
    if not st.session_state.authenticated:
        render_login(); return
    # A phone push may return with ?thread=ORDER_ID. Convert it into the same in-app
    # navigation state used by the Open cake chat button, then remove the one-shot parameter.
    try:
        thread_q = st.query_params.get("thread")
        if isinstance(thread_q, list):
            thread_q = thread_q[0] if thread_q else None
        if thread_q:
            st.session_state["_open_order_thread"] = str(thread_q)
            st.session_state["_force_page"] = "Team Chat & AI"
            del st.query_params["thread"]
    except Exception:
        pass
    page = render_sidebar()
    PAGES[page]()
    render_auto_refresh_toggle(key_suffix="_bottom")


if __name__ == "__main__":
    main()
