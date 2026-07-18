from __future__ import annotations

import html
import secrets as pysecrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from pis.oauth import service


def build_router(settings, db_session_dep) -> APIRouter:
    router = APIRouter()
    public = settings.public_url.rstrip("/")

    @router.get("/.well-known/oauth-authorization-server")
    def as_metadata():
        return {
            "issuer": public,
            "authorization_endpoint": f"{public}/authorize",
            "token_endpoint": f"{public}/token",
            "registration_endpoint": f"{public}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": service.SCOPES,
        }

    @router.post("/register")
    async def register(request: Request, db=Depends(db_session_dep)):
        body = await request.json()
        redirect_uris = body.get("redirect_uris") or []
        if not redirect_uris:
            raise HTTPException(status_code=400, detail="redirect_uris required")
        client = service.register_client(db, body.get("client_name"), redirect_uris)
        return JSONResponse(status_code=201, content={
            "client_id": client.client_id,
            "client_name": client.client_name,
            "redirect_uris": redirect_uris,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        })

    @router.get("/authorize", response_class=HTMLResponse)
    def authorize_form(client_id: str, redirect_uri: str, state: str = "",
                       code_challenge: str = "", code_challenge_method: str = "S256",
                       scope: str = "kb", response_type: str = "code"):
        fields = {
            "client_id": client_id, "redirect_uri": redirect_uri, "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method, "scope": scope,
        }
        hidden = "\n".join(
            f'<input type="hidden" name="{k}" value="{html.escape(v, quote=True)}">'
            for k, v in fields.items()
        )
        return f"""<!doctype html><html><head><title>PIS access</title></head>
<body style="font-family: system-ui; max-width: 24rem; margin: 4rem auto;">
<h2>Personal Intelligence System</h2>
<p>A client wants access to your knowledge ledger (scope: {html.escape(scope)}).</p>
<form method="post" action="/authorize">
{hidden}
<label>Passcode <input type="password" name="passcode" autofocus></label>
<button type="submit">Approve</button>
</form></body></html>"""

    @router.post("/authorize")
    def authorize_submit(
        client_id: str = Form(...), redirect_uri: str = Form(...),
        state: str = Form(""), code_challenge: str = Form(...),
        code_challenge_method: str = Form("S256"), scope: str = Form("kb"),
        passcode: str = Form(...), db=Depends(db_session_dep),
    ):
        if not pysecrets.compare_digest(passcode, settings.oauth_passcode):
            raise HTTPException(status_code=403, detail="bad passcode")
        try:
            code = service.issue_code(
                db, client_id, redirect_uri, code_challenge,
                code_challenge_method, scope.split(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        params = {"code": code}
        if state:
            params["state"] = state
        separator = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(
            url=f"{redirect_uri}{separator}{urlencode(params)}", status_code=302
        )

    @router.post("/token")
    def token(grant_type: str = Form(...), code: str = Form(""),
              redirect_uri: str = Form(""), client_id: str = Form(""),
              code_verifier: str = Form(""), refresh_token: str = Form(""),
              db=Depends(db_session_dep)):
        try:
            if grant_type == "authorization_code":
                return service.exchange_code(db, code, client_id, redirect_uri, code_verifier)
            if grant_type == "refresh_token":
                return service.refresh_grant(db, refresh_token, client_id)
        except ValueError:
            return JSONResponse(status_code=400, content={"error": "invalid_grant"})
        return JSONResponse(status_code=400, content={"error": "unsupported_grant_type"})

    return router
