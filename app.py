import os
import re
from datetime import date, datetime

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import select
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash


def load_env_file(path=".env"):
    """Load simple KEY=VALUE entries without requiring python-dotenv."""
    try:
        with open(path, encoding="utf-8") as env_file:
            for line in env_file:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'\"")
                if key:
                    os.environ.setdefault(key, value)
    except FileNotFoundError:
        pass


load_env_file()  # reads values from the .env file

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-only-change-me")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Set SESSION_COOKIE_SECURE=1 in your hosting environment (HTTPS only)
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE") == "1"

# ---------- database (SQLAlchemy) ----------
# Tests set DATABASE_URL (a temporary SQLite file) so they never touch MySQL.
# Normally it is empty and the MySQL settings from .env are used.
if os.getenv("DATABASE_URL"):
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
    engine_options = {}
else:
    # URL.create handles special characters in the password safely
    app.config["SQLALCHEMY_DATABASE_URI"] = URL.create(
        "mysql+pymysql",
        username=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 3306)),
        database=os.getenv("DB_NAME", "taskdb"),
    )
    engine_options = {"pool_pre_ping": True, "pool_recycle": 280}
    # Hosted MySQL services often require SSL; set DB_SSL_CA to the CA file path
    if os.getenv("DB_SSL_CA"):
        engine_options["connect_args"] = {"ssl": {"ca": os.getenv("DB_SSL_CA")}}
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = engine_options

db = SQLAlchemy(app)
csrf = CSRFProtect(app)  # checks the csrf_token on every POST form

login_manager = LoginManager(app)
login_manager.login_view = "login"  # where to send people who aren't logged in
login_manager.login_message = "Please log in to continue."

PRIORITIES = {1: "Low", 2: "Medium", 3: "High"}
USERNAME_RE = re.compile(r"[a-z0-9_]{3,50}")


# ---------- models ----------
class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())

    tasks = db.relationship(
        "Task", backref="owner", cascade="all, delete-orphan", passive_deletes=True
    )


class Task(db.Model):
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    done = db.Column(db.Boolean, nullable=False, default=False)
    priority = db.Column(db.SmallInteger, nullable=False, default=2)
    due_date = db.Column(db.Date, nullable=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def is_safe_next(target):
    """Only allow redirects to pages on this site."""
    return bool(
        target
        and target.startswith("/")
        and not target.startswith("//")
        and "\\" not in target
    )


def get_user_task(task_id):
    """Fetch a task only if it belongs to the logged-in user."""
    return db.session.scalar(
        select(Task).where(Task.id == task_id, Task.user_id == current_user.id)
    )


def parse_task_form():
    """Read and clean title, priority and due date from the submitted form."""
    title = request.form.get("title", "").strip()[:255]

    try:
        priority = int(request.form.get("priority", 2))
    except ValueError:
        priority = 2
    if priority not in PRIORITIES:
        priority = 2

    due_date = None
    raw_due = request.form.get("due_date", "").strip()
    if raw_due:
        try:
            due_date = datetime.strptime(raw_due, "%Y-%m-%d").date()
        except ValueError:
            due_date = None  # ignore a badly formatted date

    return title, priority, due_date


# ---------- auth routes ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        error = None
        if not USERNAME_RE.fullmatch(username):
            error = "Username: 3-50 characters, letters, numbers and underscores only."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."

        if error is None:
            user = User(username=username, password_hash=generate_password_hash(password))
            db.session.add(user)
            try:
                db.session.commit()
            except IntegrityError:  # username already exists
                db.session.rollback()
                error = "That username is already taken."
            else:
                login_user(user)
                return redirect(url_for("index"))
        flash(error, "error")
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        user = db.session.scalar(select(User).where(User.username == username))
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            next_url = request.args.get("next")
            return redirect(next_url if is_safe_next(next_url) else url_for("index"))
        # Same message for "no such user" and "wrong password" on purpose
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm", "")

        error = None
        if not check_password_hash(current_user.password_hash, current):
            error = "Current password is incorrect."
        elif len(new) < 8:
            error = "New password must be at least 8 characters."
        elif new != confirm:
            error = "New passwords do not match."
        elif check_password_hash(current_user.password_hash, new):
            error = "New password must be different from the current one."

        if error is None:
            current_user.password_hash = generate_password_hash(new)
            db.session.commit()
            flash("Password changed successfully.")
            return redirect(url_for("index"))
        flash(error)
    return render_template("change_password.html")


# ---------- task routes (all require login, all scoped to the current user) ----------
@app.route("/")
@login_required
def index():
    tasks = db.session.scalars(
        select(Task)
        .where(Task.user_id == current_user.id)
        .order_by(
            Task.done,
            Task.priority.desc(),
            Task.due_date.is_(None),
            Task.due_date,
            Task.id.desc(),
        )
    ).all()
    return render_template(
        "index.html", tasks=tasks, priorities=PRIORITIES, today=date.today()
    )


@app.route("/add", methods=["POST"])
@login_required
def add():
    title, priority, due_date = parse_task_form()
    if title:
        db.session.add(
            Task(title=title, priority=priority, due_date=due_date, user_id=current_user.id)
        )
        db.session.commit()
    return redirect(url_for("index"))


@app.route("/edit/<int:task_id>", methods=["GET", "POST"])
@login_required
def edit(task_id):
    task = get_user_task(task_id)
    if task is None:
        flash("Task not found.")
        return redirect(url_for("index"))

    if request.method == "POST":
        title, priority, due_date = parse_task_form()
        if not title:
            flash("Title cannot be empty.")
            return render_template("edit.html", task=task, priorities=PRIORITIES)

        task.title = title
        task.priority = priority
        task.due_date = due_date
        db.session.commit()
        flash("Task updated.")
        return redirect(url_for("index"))

    return render_template("edit.html", task=task, priorities=PRIORITIES)


@app.route("/toggle/<int:task_id>", methods=["POST"])
@login_required
def toggle(task_id):
    task = get_user_task(task_id)
    if task:
        task.done = not task.done
        db.session.commit()
    return redirect(url_for("index"))


@app.route("/delete/<int:task_id>", methods=["POST"])
@login_required
def delete(task_id):
    task = get_user_task(task_id)
    if task:
        db.session.delete(task)
        db.session.commit()
    return redirect(url_for("index"))


# Creates any missing tables. Existing tables and data are left untouched.
with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1")