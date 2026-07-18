import uvicorn

from pis.api.app import create_app

uvicorn.run(create_app(), host="127.0.0.1", port=8800)
