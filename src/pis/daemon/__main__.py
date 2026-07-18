import uvicorn

from pis.daemon.app import create_daemon_app

uvicorn.run(create_daemon_app(), host="127.0.0.1", port=8787)
