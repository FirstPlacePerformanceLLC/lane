"""
Lane. A channel first photo feed.
Tap a lane (Sports, Music, Art, Funny, Food, Nearby) and the feed becomes
that lane, newest first, from people you follow. One tap swaps the whole
feed. The user picks the feed instead of an algorithm picking it for them.

Single file Flask MVP built to run on the standard stack:
Python, Flask, PostgreSQL via psycopg 3, photos stored as bytea, signed
session cookies. Deploy via GitHub Upload Files then Railway redeploy.

Env vars required on Railway:
  DATABASE_URL   reference the Postgres plugin
  SECRET_KEY     a long random string, the Flask session signer

Scope on purpose: auth, follow graph, photo post with a lane, lane filtered
chronological feed, like. No ranking model, no ads, no video, no DMs,
no object storage. Photos ride in Postgres as bytea for v1, the same call
already accepted on TalkLog. Move to object storage when volume grows.
"""

import os
import io
import base64

import psycopg
from psycopg.rows import dict_row
from flask import (
    Flask,
    request,
    session,
    redirect,
    url_for,
    Response,
    render_template_string,
    abort,
    flash,
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev_only_change_me")
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# The fixed lane set. Start broad so the feed stays full. Depth comes later
# once supply exists. The key is stored on the post, the label is shown.
LANES = [
    ("sports", "Sports"),
    ("music", "Music"),
    ("art", "Art"),
    ("funny", "Funny"),
    ("food", "Food"),
    ("nearby", "Nearby"),
]
LANE_KEYS = [k for k, _ in LANES]
LANE_LABELS = dict(LANES)


def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db():
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                handle TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS follows (
                follower_id INTEGER NOT NULL REFERENCES users(id),
                followee_id INTEGER NOT NULL REFERENCES users(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (follower_id, followee_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS posts (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                lane TEXT NOT NULL,
                caption TEXT NOT NULL DEFAULT '',
                mime TEXT NOT NULL,
                data BYTEA NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS posts_lane_created "
            "ON posts (lane, created_at DESC)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS likes (
                user_id INTEGER NOT NULL REFERENCES users(id),
                post_id INTEGER NOT NULL REFERENCES posts(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (user_id, post_id)
            )
            """
        )
        conn.commit()


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, handle FROM users WHERE id = %s", (uid,))
        return cur.fetchone()


def login_required():
    if not session.get("uid"):
        abort(redirect(url_for("login")))


BASE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lane</title>
<style>
  :root { --navy:#0f2942; --ink:#12202f; --line:#e3e6ea; --muted:#7a8797;
          --accent:#2f7bd8; --accent_bg:#eaf2fc; --bg:#f6f8fa; --card:#ffffff; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
         background:var(--bg); color:var(--ink); }
  .wrap { max-width:520px; margin:0 auto; background:var(--card); min-height:100vh;
          border-left:1px solid var(--line); border-right:1px solid var(--line); }
  .top { display:flex; align-items:center; justify-content:space-between;
         padding:14px 18px 10px; }
  .brand { font-size:22px; font-weight:600; letter-spacing:0.3px; color:var(--navy); }
  .top a { color:var(--navy); text-decoration:none; font-size:14px; font-weight:500; }
  .lanes { display:flex; gap:10px; padding:4px 14px 12px; overflow-x:auto; }
  .lane { flex:0 0 auto; display:flex; flex-direction:column; align-items:center;
          gap:6px; text-decoration:none; }
  .tile { width:64px; height:64px; border-radius:16px; background:var(--bg);
          border:1px solid var(--line); display:flex; align-items:center;
          justify-content:center; font-size:22px; }
  .lane .label { font-size:12px; color:var(--muted); }
  .lane.active .tile { background:var(--accent_bg); border:2px solid var(--accent); }
  .lane.active .label { color:var(--accent); font-weight:600; }
  .cue { display:flex; align-items:center; gap:6px; padding:12px 18px;
         font-size:13px; color:var(--muted); border-top:1px solid var(--line); }
  .post { padding:14px 18px; border-top:1px solid var(--line); }
  .phead { display:flex; align-items:center; gap:10px; margin-bottom:10px; }
  .av { width:36px; height:36px; border-radius:50%; background:var(--accent_bg);
        color:var(--accent); display:flex; align-items:center; justify-content:center;
        font-weight:600; font-size:14px; }
  .phead .h { font-size:14px; font-weight:600; }
  .phead .t { font-size:12px; color:var(--muted); }
  .pimg { width:100%; border-radius:14px; display:block; background:var(--bg); }
  .pcap { font-size:15px; margin:10px 2px 6px; }
  .pacts { display:flex; align-items:center; gap:16px; font-size:14px;
           color:var(--muted); }
  .pacts form { margin:0; }
  .pacts button { background:none; border:none; color:var(--muted); font-size:14px;
                  cursor:pointer; padding:0; }
  .pacts .liked { color:var(--accent); font-weight:600; }
  .empty { padding:40px 24px; text-align:center; color:var(--muted); font-size:15px; }
  .card { padding:22px 20px; }
  label { display:block; font-size:13px; color:var(--muted); margin:14px 0 6px; }
  input, select, textarea { width:100%; padding:11px 12px; border:1px solid var(--line);
         border-radius:10px; font-size:15px; font-family:inherit; background:var(--card); }
  textarea { resize:vertical; min-height:64px; }
  .btn { width:100%; margin-top:18px; padding:12px; border:none; border-radius:10px;
         background:var(--navy); color:#fff; font-size:15px; font-weight:600; cursor:pointer; }
  .fab { position:fixed; left:50%; transform:translateX(-50%); bottom:22px;
         background:var(--navy); color:#fff; padding:13px 22px; border-radius:26px;
         text-decoration:none; font-size:15px; font-weight:600; box-shadow:0 4px 14px rgba(15,41,66,0.25); }
  .flash { padding:12px 18px; background:var(--accent_bg); color:var(--navy); font-size:14px; }
  .foot { padding:22px; text-align:center; }
  .foot a { color:var(--accent); text-decoration:none; font-size:14px; }
  .subnav { display:flex; gap:18px; padding:10px 18px; font-size:14px;
            border-top:1px solid var(--line); }
  .subnav a { color:var(--muted); text-decoration:none; }
  .who { display:flex; align-items:center; justify-content:space-between;
         padding:12px 18px; border-top:1px solid var(--line); }
  .who .name { font-weight:600; font-size:15px; }
  .who form { margin:0; }
  .who button { padding:8px 16px; border-radius:20px; border:1px solid var(--navy);
                background:#fff; color:var(--navy); font-weight:600; cursor:pointer; }
  .who button.on { background:var(--navy); color:#fff; }
</style>
</head>
<body>
  <div class="wrap">
    {% with msgs = get_flashed_messages() %}
      {% if msgs %}<div class="flash">{{ msgs[0] }}</div>{% endif %}
    {% endwith %}
    {{ body|safe }}
  </div>
</body>
</html>
"""

LANE_ICON = {
    "sports": "&#9917;",
    "music": "&#127925;",
    "art": "&#127912;",
    "funny": "&#128513;",
    "food": "&#127828;",
    "nearby": "&#128205;",
}


def lane_strip(active):
    out = ['<div class="lanes">']
    foryou = "active" if active == "foryou" else ""
    out.append(
        '<a class="lane %s" href="%s">'
        '<div class="tile">&#10022;</div>'
        '<div class="label">For You</div></a>'
        % (foryou, url_for("feed", lane="foryou"))
    )
    for key, label in LANES:
        cls = "active" if active == key else ""
        out.append(
            '<a class="lane %s" href="%s">'
            '<div class="tile">%s</div>'
            '<div class="label">%s</div></a>'
            % (cls, url_for("feed", lane=key), LANE_ICON.get(key, "&#9679;"), label)
        )
    out.append("</div>")
    return "".join(out)


def render(body):
    return render_template_string(BASE, body=body)


@app.route("/")
def home():
    if session.get("uid"):
        return redirect(url_for("feed", lane="foryou"))
    return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        handle = (request.form.get("handle") or "").strip().lower()
        pw = request.form.get("password") or ""
        if not handle or not pw or len(pw) < 6:
            flash("Pick a handle and a password of at least six characters.")
            return redirect(url_for("signup"))
        try:
            with db() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (handle, password_hash) VALUES (%s, %s) "
                    "RETURNING id",
                    (handle, generate_password_hash(pw)),
                )
                session["uid"] = cur.fetchone()["id"]
                conn.commit()
        except psycopg.errors.UniqueViolation:
            flash("That handle is taken. Try another.")
            return redirect(url_for("signup"))
        return redirect(url_for("feed", lane="foryou"))
    body = """
      <div class="top"><span class="brand">Lane</span></div>
      <div class="card">
        <h2 style="margin:0 0 4px;color:#0f2942;">Create your account</h2>
        <p style="color:#7a8797;font-size:14px;margin:0;">You choose the feed. Not a machine.</p>
        <form method="post">
          <label>Handle</label>
          <input name="handle" placeholder="yourname" autocomplete="off">
          <label>Password</label>
          <input name="password" type="password" placeholder="at least six characters">
          <button class="btn" type="submit">Create account</button>
        </form>
      </div>
      <div class="foot"><a href="%s">Already have an account. Sign in</a></div>
    """ % url_for("login")
    return render(body)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        handle = (request.form.get("handle") or "").strip().lower()
        pw = request.form.get("password") or ""
        with db() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, password_hash FROM users WHERE handle = %s", (handle,)
            )
            row = cur.fetchone()
        if row and check_password_hash(row["password_hash"], pw):
            session["uid"] = row["id"]
            return redirect(url_for("feed", lane="foryou"))
        flash("Wrong handle or password.")
        return redirect(url_for("login"))
    body = """
      <div class="top"><span class="brand">Lane</span></div>
      <div class="card">
        <h2 style="margin:0 0 4px;color:#0f2942;">Sign in</h2>
        <form method="post">
          <label>Handle</label>
          <input name="handle" placeholder="yourname" autocomplete="off">
          <label>Password</label>
          <input name="password" type="password">
          <button class="btn" type="submit">Sign in</button>
        </form>
      </div>
      <div class="foot"><a href="%s">New here. Create an account</a></div>
    """ % url_for("signup")
    return render(body)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/feed/<lane>")
def feed(lane):
    login_required()
    uid = session["uid"]
    if lane != "foryou" and lane not in LANE_KEYS:
        return redirect(url_for("feed", lane="foryou"))

    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS c FROM follows WHERE follower_id = %s", (uid,)
        )
        follow_count = cur.fetchone()["c"]

        # Feed scope: people you follow plus yourself. If you follow no one
        # yet, fall back to everyone so a fresh account is never empty.
        if follow_count > 0:
            scope = (
                "p.user_id IN "
                "(SELECT followee_id FROM follows WHERE follower_id = %(uid)s) "
                "OR p.user_id = %(uid)s"
            )
        else:
            scope = "TRUE"

        params = {"uid": uid}
        lane_clause = ""
        if lane != "foryou":
            lane_clause = "AND p.lane = %(lane)s"
            params["lane"] = lane

        cur.execute(
            """
            SELECT p.id, p.lane, p.caption, p.created_at, u.handle,
                   (SELECT COUNT(*) FROM likes l WHERE l.post_id = p.id) AS likes,
                   EXISTS(SELECT 1 FROM likes l WHERE l.post_id = p.id
                          AND l.user_id = %(uid)s) AS liked
            FROM posts p
            JOIN users u ON u.id = p.user_id
            WHERE ({scope}) {lane_clause}
            ORDER BY p.created_at DESC
            LIMIT 100
            """.format(scope=scope, lane_clause=lane_clause),
            params,
        )
        rows = cur.fetchall()

    if lane == "foryou":
        cue = "For You, newest first, from people you follow"
    else:
        cue = "%s, newest first, from people you follow" % LANE_LABELS[lane]
    if follow_count == 0:
        cue = "Everything on Lane, newest first. Follow people to shape this."

    parts = [
        '<div class="top"><span class="brand">Lane</span>'
        '<a href="%s">Discover</a></div>' % url_for("discover")
    ]
    parts.append(lane_strip(lane))
    parts.append('<div class="cue">&#128337;&nbsp;%s</div>' % cue)

    if not rows:
        parts.append(
            '<div class="empty">No posts in this lane yet. '
            'Be the first to post here.</div>'
        )
    else:
        for r in rows:
            initials = r["handle"][:2].upper()
            liked_cls = "liked" if r["liked"] else ""
            like_word = "Liked" if r["liked"] else "Like"
            parts.append(
                '<div class="post">'
                '<div class="phead">'
                '<div class="av">%s</div>'
                '<div><div class="h">%s</div>'
                '<div class="t">%s in %s</div></div></div>'
                '<img class="pimg" src="%s" alt="post">'
                '%s'
                '<div class="pacts">'
                '<form method="post" action="%s">'
                '<button type="submit" class="%s">&#9829; %s %d</button></form>'
                '</div></div>'
                % (
                    initials,
                    r["handle"],
                    _ago(r["created_at"]),
                    LANE_LABELS.get(r["lane"], r["lane"]),
                    url_for("photo", post_id=r["id"]),
                    ('<div class="pcap">%s</div>' % _esc(r["caption"]))
                    if r["caption"] else "",
                    url_for("like", post_id=r["id"]),
                    liked_cls,
                    like_word,
                    r["likes"],
                )
            )

    parts.append(
        '<div class="subnav">'
        '<a href="%s">Feed</a><a href="%s">Discover</a>'
        '<a href="%s">Sign out</a></div>'
        % (url_for("feed", lane="foryou"), url_for("discover"), url_for("logout"))
    )
    parts.append('<a class="fab" href="%s">Post</a>' % url_for("post_new"))
    return render("".join(parts))


@app.route("/post", methods=["GET", "POST"])
def post_new():
    login_required()
    if request.method == "POST":
        lane = request.form.get("lane") or ""
        caption = (request.form.get("caption") or "").strip()[:500]
        f = request.files.get("photo")
        if lane not in LANE_KEYS:
            flash("Pick a lane for your post.")
            return redirect(url_for("post_new"))
        if not f or not f.filename:
            flash("Choose a photo to post.")
            return redirect(url_for("post_new"))
        data = f.read()
        mime = f.mimetype or "image/jpeg"
        if not mime.startswith("image/"):
            flash("That file is not an image.")
            return redirect(url_for("post_new"))
        with db() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO posts (user_id, lane, caption, mime, data) "
                "VALUES (%s, %s, %s, %s, %s)",
                (session["uid"], lane, caption, mime, data),
            )
            conn.commit()
        return redirect(url_for("feed", lane=lane))

    opts = "".join(
        '<option value="%s">%s</option>' % (k, v) for k, v in LANES
    )
    body = """
      <div class="top"><span class="brand">Lane</span>
        <a href="%s">Cancel</a></div>
      <div class="card">
        <h2 style="margin:0 0 12px;color:#0f2942;">New post</h2>
        <form method="post" enctype="multipart/form-data">
          <label>Photo</label>
          <input type="file" name="photo" accept="image/*">
          <label>Lane</label>
          <select name="lane">%s</select>
          <label>Caption</label>
          <textarea name="caption" placeholder="Say something"></textarea>
          <button class="btn" type="submit">Share to this lane</button>
        </form>
      </div>
    """ % (url_for("feed", lane="foryou"), opts)
    return render(body)


@app.route("/photo/<int:post_id>")
def photo(post_id):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT mime, data FROM posts WHERE id = %s", (post_id,))
        row = cur.fetchone()
    if not row:
        abort(404)
    return Response(bytes(row["data"]), mimetype=row["mime"])


@app.route("/like/<int:post_id>", methods=["POST"])
def like(post_id):
    login_required()
    uid = session["uid"]
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM likes WHERE user_id = %s AND post_id = %s",
            (uid, post_id),
        )
        if cur.fetchone():
            cur.execute(
                "DELETE FROM likes WHERE user_id = %s AND post_id = %s",
                (uid, post_id),
            )
        else:
            cur.execute(
                "INSERT INTO likes (user_id, post_id) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                (uid, post_id),
            )
        conn.commit()
    return redirect(request.referrer or url_for("feed", lane="foryou"))


@app.route("/discover")
def discover():
    login_required()
    uid = session["uid"]
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.id, u.handle,
                   EXISTS(SELECT 1 FROM follows f
                          WHERE f.follower_id = %s AND f.followee_id = u.id) AS following
            FROM users u
            WHERE u.id <> %s
            ORDER BY u.created_at DESC
            LIMIT 100
            """,
            (uid, uid),
        )
        rows = cur.fetchall()

    parts = [
        '<div class="top"><span class="brand">Lane</span>'
        '<a href="%s">Feed</a></div>' % url_for("feed", lane="foryou")
    ]
    parts.append(
        '<div class="cue">&#128101;&nbsp;People on Lane</div>'
    )
    if not rows:
        parts.append('<div class="empty">No one else here yet.</div>')
    for r in rows:
        on = "on" if r["following"] else ""
        word = "Following" if r["following"] else "Follow"
        parts.append(
            '<div class="who"><span class="name">%s</span>'
            '<form method="post" action="%s">'
            '<button class="%s" type="submit">%s</button></form></div>'
            % (r["handle"], url_for("follow", target_id=r["id"]), on, word)
        )
    return render("".join(parts))


@app.route("/follow/<int:target_id>", methods=["POST"])
def follow(target_id):
    login_required()
    uid = session["uid"]
    if target_id == uid:
        return redirect(url_for("discover"))
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM follows WHERE follower_id = %s AND followee_id = %s",
            (uid, target_id),
        )
        if cur.fetchone():
            cur.execute(
                "DELETE FROM follows WHERE follower_id = %s AND followee_id = %s",
                (uid, target_id),
            )
        else:
            cur.execute(
                "INSERT INTO follows (follower_id, followee_id) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                (uid, target_id),
            )
        conn.commit()
    return redirect(request.referrer or url_for("discover"))


def _esc(s):
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _ago(ts):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    secs = int((now - ts).total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return "%d min ago" % (secs // 60)
    if secs < 86400:
        return "%d hr ago" % (secs // 3600)
    return "%d days ago" % (secs // 86400)


try:
    if DATABASE_URL:
        init_db()
except Exception as exc:
    print("init_db skipped:", exc)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
