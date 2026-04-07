from fastapi import HTTPException, Request


def require_admin_session(request: Request):
    if not request.session.get("admin_ok"):
        raise HTTPException(status_code=401, detail="Unauthorized")
