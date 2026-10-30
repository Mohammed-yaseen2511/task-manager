from sqlalchemy import select

from app import Task, User, db
from conftest import login, register


# ---------- helpers ----------
def all_tasks(app):
    with app.app_context():
        return db.session.scalars(select(Task).order_by(Task.id)).all()


def first_task(app):
    with app.app_context():
        task = db.session.scalars(select(Task).order_by(Task.id)).first()
        return {
            "id": task.id,
            "title": task.title,
            "done": task.done,
            "priority": task.priority,
            "due_date": task.due_date,
        }


def add_task(client, title="Buy milk", priority="2", due_date=""):
    return client.post(
        "/add",
        data={"title": title, "priority": priority, "due_date": due_date},
        follow_redirects=True,
    )


# ---------- login required ----------
def test_home_redirects_to_login_when_logged_out(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_add_requires_login(client, app):
    response = client.post("/add", data={"title": "Sneaky"})
    assert response.status_code == 302
    assert all_tasks(app) == []


# ---------- register ----------
def test_register_success_logs_user_in(client, app):
    response = register(client)
    assert response.status_code == 200
    assert b"Task Manager" in response.data
    with app.app_context():
        user = db.session.scalar(select(User).where(User.username == "alice"))
        assert user is not None
        assert user.password_hash != "password123"  # stored hashed


def test_register_duplicate_username(client):
    register(client)
    client.post("/logout")
    response = register(client)
    assert b"That username is already taken." in response.data


def test_register_short_password(client):
    response = register(client, password="short")
    assert b"Password must be at least 8 characters." in response.data


def test_register_password_mismatch(client):
    response = register(client, confirm="different123")
    assert b"Passwords do not match." in response.data


def test_register_invalid_username(client):
    response = register(client, username="a b")
    assert b"Username: 3-50 characters" in response.data


# ---------- login / logout ----------
def test_login_success(client):
    register(client)
    client.post("/logout")
    response = login(client)
    assert b"Task Manager" in response.data


def test_login_wrong_password(client):
    register(client)
    client.post("/logout")
    response = login(client, password="wrongpassword")
    assert b"Invalid username or password." in response.data


def test_login_unknown_user(client):
    response = login(client, username="nobody")
    assert b"Invalid username or password." in response.data


def test_logout(client):
    register(client)
    response = client.post("/logout", follow_redirects=True)
    assert b"Login" in response.data
    assert client.get("/").status_code == 302


# ---------- add ----------
def test_add_task(client, app):
    register(client)
    response = add_task(client, "Buy milk", "3", "2026-12-01")
    assert b"Buy milk" in response.data
    task = first_task(app)
    assert task["title"] == "Buy milk"
    assert task["priority"] == 3
    assert str(task["due_date"]) == "2026-12-01"


def test_add_empty_title_is_ignored(client, app):
    register(client)
    add_task(client, "   ")
    assert all_tasks(app) == []


def test_add_invalid_priority_defaults_to_medium(client, app):
    register(client)
    add_task(client, "Task A", priority="9")
    assert first_task(app)["priority"] == 2


def test_add_bad_date_is_ignored(client, app):
    register(client)
    add_task(client, "Task B", due_date="not-a-date")
    assert first_task(app)["due_date"] is None


# ---------- edit ----------
def test_edit_page_shows_current_values(client, app):
    register(client)
    add_task(client, "Buy milk")
    task_id = first_task(app)["id"]
    response = client.get(f"/edit/{task_id}")
    assert response.status_code == 200
    assert b'value="Buy milk"' in response.data


def test_edit_task(client, app):
    register(client)
    add_task(client, "Buy milk")
    task_id = first_task(app)["id"]
    response = client.post(
        f"/edit/{task_id}",
        data={"title": "Buy bread", "priority": "3", "due_date": "2026-11-15"},
        follow_redirects=True,
    )
    assert b"Task updated." in response.data
    task = first_task(app)
    assert task["title"] == "Buy bread"
    assert task["priority"] == 3
    assert str(task["due_date"]) == "2026-11-15"


def test_edit_empty_title_rejected(client, app):
    register(client)
    add_task(client, "Buy milk")
    task_id = first_task(app)["id"]
    response = client.post(
        f"/edit/{task_id}",
        data={"title": "  ", "priority": "2", "due_date": ""},
    )
    assert b"Title cannot be empty." in response.data
    assert first_task(app)["title"] == "Buy milk"


def test_edit_missing_task(client):
    register(client)
    response = client.get("/edit/9999", follow_redirects=True)
    assert b"Task not found." in response.data


# ---------- toggle / delete ----------
def test_toggle_task(client, app):
    register(client)
    add_task(client)
    task_id = first_task(app)["id"]
    client.post(f"/toggle/{task_id}")
    assert first_task(app)["done"] is True
    client.post(f"/toggle/{task_id}")
    assert first_task(app)["done"] is False


def test_delete_task(client, app):
    register(client)
    add_task(client)
    task_id = first_task(app)["id"]
    client.post(f"/delete/{task_id}")
    assert all_tasks(app) == []


# ---------- users cannot touch each other's tasks ----------
def test_user_cannot_see_other_users_tasks(client, other_client):
    register(client, "alice")
    add_task(client, "Alice secret task")
    register(other_client, "bobby")
    response = other_client.get("/")
    assert b"Alice secret task" not in response.data


def test_user_cannot_edit_other_users_task(client, other_client, app):
    register(client, "alice")
    add_task(client, "Alice task")
    task_id = first_task(app)["id"]

    register(other_client, "bobby")
    other_client.post(
        f"/edit/{task_id}",
        data={"title": "Hacked", "priority": "1", "due_date": ""},
    )
    assert first_task(app)["title"] == "Alice task"


def test_user_cannot_toggle_or_delete_other_users_task(client, other_client, app):
    register(client, "alice")
    add_task(client, "Alice task")
    task_id = first_task(app)["id"]

    register(other_client, "bobby")
    other_client.post(f"/toggle/{task_id}")
    other_client.post(f"/delete/{task_id}")

    task = first_task(app)
    assert task["done"] is False
    assert task["title"] == "Alice task"


# ---------- change password ----------
def test_change_password_success(client):
    register(client)
    response = client.post(
        "/change-password",
        data={
            "current_password": "password123",
            "new_password": "newpassword456",
            "confirm": "newpassword456",
        },
        follow_redirects=True,
    )
    assert b"Password changed successfully." in response.data

    client.post("/logout")
    assert b"Invalid username or password." in login(client, password="password123").data
    assert b"Task Manager" in login(client, password="newpassword456").data


def test_change_password_wrong_current(client):
    register(client)
    response = client.post(
        "/change-password",
        data={
            "current_password": "wrongpassword",
            "new_password": "newpassword456",
            "confirm": "newpassword456",
        },
    )
    assert b"Current password is incorrect." in response.data


def test_change_password_mismatch(client):
    register(client)
    response = client.post(
        "/change-password",
        data={
            "current_password": "password123",
            "new_password": "newpassword456",
            "confirm": "different789",
        },
    )
    assert b"New passwords do not match." in response.data