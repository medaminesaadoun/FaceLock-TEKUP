# modules/notifications.py
def notify(title: str, body: str) -> None:
    try:
        from winotify import Notification
        Notification(app_id="FaceLock", title=title, msg=body).show()
    except Exception:
        pass
