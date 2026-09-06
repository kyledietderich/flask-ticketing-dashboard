# Kyle IT Support Desk

A small IT support desk app: a single-page static frontend served by nginx, backed
by a Flask + SQLite JSON API.

## Layout

```
api/
  app.py              Flask API (tickets, hardware/inventory, agents, settings, activity feed)
  requirements.txt    Python dependencies
web/
  index.html          Single-page frontend (calls /api/*)
deploy/
  kyle-it.service           systemd unit that runs the API via gunicorn on 127.0.0.1:5000
  nginx-kyle-it-api.conf    nginx snippet that proxies /api/ to the gunicorn process
```

The SQLite database (`kyle_it.db`) is not committed — `init_db()` creates it and
seeds demo data on first start.

## Run locally

```bash
cd api
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py          # serves the API on http://127.0.0.1:5000
```

Then open `web/index.html` through a web server that proxies `/api/` to port 5000
(see `deploy/nginx-kyle-it-api.conf`).

## Deploy (Amazon Linux, nginx + systemd)

```bash
# API
sudo cp -r api /home/ec2-user/kyle-it
cd /home/ec2-user/kyle-it && python3 -m venv venv && venv/bin/pip install -r requirements.txt
sudo cp deploy/kyle-it.service /etc/systemd/system/
sudo systemctl enable --now kyle-it

# Frontend
sudo cp web/index.html /usr/share/nginx/html/
sudo cp deploy/nginx-kyle-it-api.conf /etc/nginx/default.d/
sudo systemctl reload nginx
```

Restart the API after changes: `sudo systemctl restart kyle-it`
