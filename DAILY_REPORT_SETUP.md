# Daily Report Email — Setup Guide

## What this does

Every day, a report can go out to `cakealbum@gmail.com` showing:
- Today's sales (every order entered today, with prices)
- Every order still owing a balance
- Every driver still holding cash that hasn't been physically reconciled with Finance

You can already send this **on demand** right now — log in as Admin and scroll to "📧 Daily Report Email." But to have it send **automatically** every day without anyone touching it, follow the steps below.

---

## Step 1: Create a Gmail "App Password"

Gmail won't accept your normal password for this — it needs a special App Password.

1. Go to your Google Account → **Security**
2. Turn on **2-Step Verification** if it isn't already on (required for App Passwords)
3. Search for **"App Passwords"** in your account settings
4. Create a new one, name it something like "Cake Album Reports"
5. Copy the 16-character password it gives you — you'll only see it once

## Step 2: Add the credentials to the server

```bash
su - cakealbum
nano ~/.bashrc
```

Add these two lines at the bottom (replace with your actual sending email and the App Password from Step 1 — this can be the same `cakealbum@gmail.com` address, sending to itself, or a different account):

```bash
export SMTP_EMAIL="your-sending-email@gmail.com"
export SMTP_APP_PASSWORD="the16characterapppassword"
```

Save, then reload it:
```bash
source ~/.bashrc
```

**Also add these same two lines to the systemd service**, so the main app picks them up too:
```bash
exit
sudo nano /etc/systemd/system/cakealbum.service
```
Under `[Service]`, add:
```ini
Environment="SMTP_EMAIL=your-sending-email@gmail.com"
Environment="SMTP_APP_PASSWORD=the16characterapppassword"
```
Save, then:
```bash
sudo systemctl daemon-reload
sudo systemctl restart cakealbum
```

## Step 3: Confirm it works — send one manually first

Log into the app as Admin, go to "📧 Daily Report Email," and click **"Send This Report Now."** You should see a green success message and the email should land in the inbox within a few seconds. If it fails, the red error message will tell you exactly why (usually a typo in the App Password).

## Step 4: Automate it with a daily cron job

Once Step 3 works, set up the schedule:

```bash
su - cakealbum
crontab -e
```

Add this line to run it every day at 6:00 PM (adjust the time as you like — this uses 24-hour format, `18 00` means 6:00 PM):

```
0 18 * * * cd /home/cakealbum/cake-album-operations-system && SMTP_EMAIL="your-sending-email@gmail.com" SMTP_APP_PASSWORD="the16characterapppassword" /home/cakealbum/cake-album-operations-system/venv/bin/python3 send_daily_report.py >> /home/cakealbum/daily_report.log 2>&1
```

Save and exit. That's it — from now on, the report sends itself every day at 6 PM, and any errors get logged to `~/daily_report.log` so you can check if something ever goes wrong.

## Testing the cron job's script directly (without waiting for 6 PM)

```bash
cd /home/cakealbum/cake-album-operations-system
SMTP_EMAIL="your-sending-email@gmail.com" SMTP_APP_PASSWORD="the16characterapppassword" venv/bin/python3 send_daily_report.py
```

You're looking for `Report sent to cakealbum@gmail.com.` printed at the end.
