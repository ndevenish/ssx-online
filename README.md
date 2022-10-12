# Basic React/FastAPI base

This is intended to grow into a project, but for now is an example of
combining react and FastAPI. Built by starting from create-react-app, exploding,
stripping things down, then building up again until the broken pieces I want to
keep work again.

Both Uvicorn and Webpack-dev-server are started, and webpack proxies to the
fastapi server at `/api`.

Usage:

```
npm run build # Build the JS output for serving in build/
npm run start # Start serving development webpack and uvicorn front and backends
```
