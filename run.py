"""One worker is mandatory: memory is not shared between processes."""
import os
import uvicorn

if __name__ == '__main__':
    uvicorn.run('main:app', host=os.getenv('HOST', '0.0.0.0'),
                port=int(os.getenv('PORT', '8080')), workers=1,
                timeout_graceful_shutdown=5, timeout_keep_alive=2,
                access_log=False, backlog=128)
